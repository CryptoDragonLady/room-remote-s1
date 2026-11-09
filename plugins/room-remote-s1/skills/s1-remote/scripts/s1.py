"""Local S1 discovery and control. Uses only the Python standard library."""
from __future__ import annotations

import concurrent.futures
import http.client
import ipaddress
import select
import socket
import time
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from dataclasses import dataclass
from urllib.parse import urlsplit

ZONE_PLAYER = "urn:schemas-upnp-org:device:ZonePlayer:1"
SSDP = ("239.255.255.250", 1900)
PORT = 1400
MAX_XML = 2 * 1024 * 1024
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
SERVICES = {
    "ZoneGroupTopology": "/ZoneGroupTopology/Control",
    "AVTransport": "/MediaRenderer/AVTransport/Control",
    "RenderingControl": "/MediaRenderer/RenderingControl/Control",
    "ContentDirectory": "/MediaServer/ContentDirectory/Control",
}


class RemoteError(Exception):
    pass


def xml(data):
    if len(data) > MAX_XML or b"<!doctype" in data.lower() or b"<!entity" in data.lower():
        raise RemoteError("Speaker returned an unsupported XML document.")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise RemoteError("Speaker returned invalid XML.") from exc


def local_host(text):
    try:
        address = ipaddress.IPv4Address(text)
    except ipaddress.AddressValueError:
        return False
    return (address.is_private and not address.is_loopback
            and not address.is_reserved and not address.is_unspecified)


def location_host(location):
    try:
        url = urlsplit(location)
        if (url.scheme == "http" and url.port == PORT and url.username is None
                and url.password is None and local_host(url.hostname)):
            return url.hostname
    except (TypeError, ValueError):
        pass
    return None


def ssdp_hosts(duration=1.8):
    request = ("\r\n".join(("M-SEARCH * HTTP/1.1", "HOST: 239.255.255.250:1900",
                           'MAN: "ssdp:discover"', "MX: 1", f"ST: {ZONE_PLAYER}", "", ""))).encode()
    addresses = {"0.0.0.0"}
    try:
        addresses.update(s[4][0] for s in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM))
    except OSError:
        pass
    hosts = set()
    with ExitStack() as cleanup:
        listeners = []
        for address in addresses:
            try:
                sock = cleanup.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
                sock.bind((address, 0))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                if address != "0.0.0.0":
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
                for _ in range(2):
                    sock.sendto(request, SSDP)
                listeners.append(sock)
            except OSError:
                continue
        if not listeners:
            raise RemoteError("Could not send local discovery. Check the network and firewall.")
        deadline = time.monotonic() + duration
        while (remaining := deadline - time.monotonic()) > 0:
            ready, _, _ = select.select(listeners, [], [], remaining)
            for sock in ready:
                try:
                    packet, (peer, _) = sock.recvfrom(8192)
                except OSError:
                    continue
                headers = {}
                lines = packet.decode("latin1").splitlines()
                if not lines or not lines[0].upper().startswith("HTTP/1.1 200"):
                    continue
                for line in lines[1:]:
                    key, _, value = line.partition(":")
                    headers[key.strip().lower()] = value.strip()
                if headers.get("st") == ZONE_PLAYER and location_host(headers.get("location", "")) == peer:
                    hosts.add(peer)
    return sorted(hosts)


def request(host, path, body=None, action=None):
    if not local_host(host):
        raise RemoteError("Expected a local IPv4 speaker address.")
    connection = http.client.HTTPConnection(host, PORT, timeout=3)
    headers = {"Content-Type": 'text/xml; charset="utf-8"', "SOAPACTION": f'"{action}"'} if body else {}
    try:
        connection.request("POST" if body else "GET", path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read(MAX_XML + 1)
        if response.status != 200:
            detail = ""
            if data and response.status == 500:
                try:
                    code = xml(data).findtext(".//{*}errorCode")
                    detail = f" (UPnP {code})" if code else ""
                except RemoteError:
                    pass
            raise RemoteError(f"{(action or 'Discovery').split('#')[-1]} refused by speaker"
                              f" (HTTP {response.status}){detail}.")
        return xml(data)
    except (OSError, http.client.HTTPException) as exc:
        raise RemoteError("Could not reach a speaker. Check its power and local network.") from exc
    finally:
        connection.close()


def soap(host, service, action, **args):
    urn = f"urn:schemas-upnp-org:service:{service}:1"
    envelope = ET.Element(f"{{{SOAP_NS}}}Envelope")
    body = ET.SubElement(envelope, f"{{{SOAP_NS}}}Body")
    command = ET.SubElement(body, f"{{{urn}}}{action}")
    for key, value in args.items():
        ET.SubElement(command, key).text = str(value)
    response = request(host, SERVICES[service], ET.tostring(envelope, encoding="utf-8"), f"{urn}#{action}")
    result = response.find(f".//{{{urn}}}{action}Response")
    if result is None:
        raise RemoteError(f"Speaker returned no {action} response.")
    return {item.tag.split("}")[-1]: item.text or "" for item in result}


@dataclass(frozen=True)
class Device:
    uid: str
    host: str
    model: str
    firmware: str


def describe(host):
    dev = request(host, "/xml/device_description.xml").find("{*}device")
    if dev is None or dev.findtext("{*}deviceType") != ZONE_PLAYER:
        return None
    if "sonos" not in dev.findtext("{*}manufacturer", "").lower():
        return None
    generation = dev.findtext("{*}swGen")
    firmware = dev.findtext("{*}displayVersion", "")
    major = firmware.split(".")[0]
    if generation != "1" and not (generation is None and major.isdigit() and 0 < int(major) <= 11):
        return None
    uid = dev.findtext("{*}UDN", "").removeprefix("uuid:")
    if not uid.startswith("RINCON_"):
        return None
    return Device(uid, host, dev.findtext("{*}modelName", ""), firmware)


@dataclass(frozen=True)
class Room:
    name: str
    device: Device
    coordinator: str
    group: tuple[str, ...]


def discover():
    devices = {}
    warnings = []

    def inspect(host):
        try:
            return describe(host)
        except RemoteError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for dev in pool.map(inspect, ssdp_hosts()):
            if dev:
                devices[dev.uid] = dev
    rooms = {}
    observed = set()
    for seed in list(devices.values()):
        if seed.uid in observed:
            continue
        try:
            result = soap(seed.host, "ZoneGroupTopology", "GetZoneGroupState")
            groups = xml(result.get("ZoneGroupState", "").encode())
        except RemoteError as exc:
            warnings.append(str(exc))
            continue
        for group in groups.findall(".//{*}ZoneGroup"):
            members = group.findall("{*}ZoneGroupMember")
            visible = [m for m in members if m.get("Invisible") != "1"]
            names = tuple(m.get("ZoneName", "") for m in visible)
            for member in visible:
                uid = member.get("UUID")
                observed.add(uid)
                dev = devices.get(uid)
                if not dev:
                    host = location_host(member.get("Location", ""))
                    candidate = inspect(host) if host else None
                    if candidate and candidate.uid == uid:
                        dev = devices[uid] = candidate
                if dev:
                    rooms[uid] = Room(member.get("ZoneName", ""), dev, group.get("Coordinator", ""), names)
                else:
                    warnings.append(f"Room unavailable: {member.get('ZoneName', 'unnamed')}.")
    return sorted(rooms.values(), key=lambda r: (r.name.casefold(), r.device.uid)), warnings


def choose(rooms, target, *, partial=False):
    if not rooms:
        raise RemoteError("No reachable Sonos S1 rooms found. Connect to the speaker's LAN and allow multicast discovery.")
    if not target and partial:
        raise RemoteError("Discovery was incomplete. Specify a room by name.")
    matches = rooms if not target else [
        r for r in rooms if target.casefold() in (r.name.casefold(), r.device.uid.casefold())]
    if len(matches) == 1:
        return matches[0]
    labels = [f"{r.name} ({r.device.uid})" for r in rooms]
    raise RemoteError("Choose a room by exact name or speaker ID. Available: " + ", ".join(labels))


def coordinator(room, rooms):
    for item in rooms:
        if item.device.uid == room.coordinator:
            return item.device.host
    raise RemoteError("Group coordinator is unavailable. Discover the rooms again.")


def metadata(data):
    if not data or data == "NOT_IMPLEMENTED":
        return {}
    try:
        tree = xml(data.encode())
    except RemoteError:
        return {}
    return {key: value for key, tag in (("title", "title"), ("artist", "creator"), ("album", "album"))
            if (value := tree.findtext(f".//{{*}}{tag}"))}


def entries(host, start):
    result = soap(host, "ContentDirectory", "Browse", ObjectID="Q:0", BrowseFlag="BrowseDirectChildren",
                  Filter="*", StartingIndex=start, RequestedCount=20, SortCriteria="")
    tree = xml(result["Result"].encode())
    rows = [metadata(ET.tostring(item, encoding="unicode")) for item in tree.findall("{*}item")]
    return {"start": start, "total": int(result["TotalMatches"]), "tracks": rows}


def volume(host):
    params = {"InstanceID": 0, "Channel": "Master"}
    level = soap(host, "RenderingControl", "GetVolume", **params)["CurrentVolume"]
    muted = soap(host, "RenderingControl", "GetMute", **params)["CurrentMute"]
    return {"volume": int(level), "muted": muted.lower() in ("1", "true", "yes")}


def transport_state(host):
    return soap(host, "AVTransport", "GetTransportInfo", InstanceID=0)["CurrentTransportState"]


def run(action, target=None, value=None):
    rooms, warnings = discover()
    if action == "discover":
        return {"rooms": [{"room": r.name, "id": r.device.uid, "model": r.device.model,
                           "firmware": r.device.firmware, "group": list(r.group),
                           "coordinator": r.coordinator} for r in rooms], "warnings": warnings}
    room = choose(rooms, target, partial=bool(warnings))
    output = {"room": room.name}
    if action in {"volume", "set-volume", "mute", "unmute"}:
        host = room.device.host
        if action == "set-volume":
            soap(host, "RenderingControl", "SetVolume", InstanceID=0, Channel="Master", DesiredVolume=value)
        if action in {"mute", "unmute"}:
            soap(host, "RenderingControl", "SetMute", InstanceID=0, Channel="Master", DesiredMute=int(action == "mute"))
        return {**output, **volume(host)}
    host = coordinator(room, rooms)
    output["group"] = list(room.group)
    if action == "queue":
        return {**output, **entries(host, value or 0)}
    if action == "status":
        state = transport_state(host)
        position = soap(host, "AVTransport", "GetPositionInfo", InstanceID=0)
        return {**output, "state": state, "position": position.get("RelTime"),
                "duration": position.get("TrackDuration"), **metadata(position.get("TrackMetaData"))}
    method = {"play": "Play", "pause": "Pause", "next": "Next", "previous": "Previous"}[action]
    soap(host, "AVTransport", method, InstanceID=0, **({"Speed": "1"} if action == "play" else {}))
    try:
        state = transport_state(host)
    except RemoteError:
        state = "UNKNOWN"
    return {**output, "accepted": action, "state": state}
