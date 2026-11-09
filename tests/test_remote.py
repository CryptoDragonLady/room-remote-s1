"""Exercise real SOAP envelopes against a local HTTP fixture. Never contacts a speaker."""
import contextlib
import http.client
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import xml.etree.ElementTree as ET

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins/room-remote-s1/skills/s1-remote/scripts"
sys.path.insert(0, str(SCRIPTS))
import remote
import s1

FIRST, SECOND, S2 = "10.12.0.10", "10.12.0.11", "10.12.0.12"
NAMES = {FIRST: "Kitchen", SECOND: "Den", S2: "Modern"}
IDS = {FIRST: "RINCON_TEST_A", SECOND: "RINCON_TEST_B", S2: "RINCON_TEST_C"}
TRACK = '<DIDL-Lite><item><title>Tea &amp; Toast</title><creator>Example Artist</creator></item></DIDL-Lite>'


def topology():
    group = ET.Element("ZoneGroup", Coordinator=IDS[FIRST])
    for host in (FIRST, SECOND):
        ET.SubElement(group, "ZoneGroupMember", UUID=IDS[host], ZoneName=NAMES[host],
                      Location=f"http://{host}:1400/xml/device_description.xml")
    ET.SubElement(group, "ZoneGroupMember", UUID="RINCON_SUB", ZoneName="Sub", Invisible="1")
    groups = ET.Element("ZoneGroups")
    groups.append(group)
    return ET.tostring(groups, encoding="unicode")


class Speaker(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_xml(self, data, code=200):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        host = self.headers["X-Test-Host"]
        fields = {"deviceType": s1.ZONE_PLAYER, "manufacturer": "Sonos, Inc.", "modelName": "Fixture",
                  "UDN": "uuid:" + IDS[host], "swGen": "2" if host == S2 else "1",
                  "displayVersion": "15.0" if host == S2 else "11.0"}
        root = ET.Element("root", xmlns="urn:schemas-upnp-org:device-1-0")
        device = ET.SubElement(root, "device")
        for key, value in fields.items():
            ET.SubElement(device, key).text = value
        self.send_xml(ET.tostring(root))

    def do_POST(self):
        host = self.headers["X-Test-Host"]
        body = ET.fromstring(self.rfile.read(int(self.headers["Content-Length"])))
        command, = body.find(f"{{{s1.SOAP_NS}}}Body")
        urn, action = command.tag[1:].split("}")
        options = {e.tag: e.text for e in command}
        self.server.calls.append((host, action, options))
        if self.headers["SOAPACTION"] != f'"{urn}#{action}"' or self.path != s1.SERVICES[urn.split(":")[-2]]:
            self.send_xml(b"<error/>", 400)
            return
        result = {}
        if action == "GetZoneGroupState":
            result["ZoneGroupState"] = topology()
        elif action == "GetTransportInfo":
            result["CurrentTransportState"] = self.server.state
        elif action == "GetPositionInfo":
            result.update(TrackMetaData=TRACK, RelTime="0:01:00", TrackDuration="0:03:00")
        elif action in ("Play", "Pause"):
            self.server.state = "PLAYING" if action == "Play" else "PAUSED_PLAYBACK"
        elif action == "GetVolume":
            result["CurrentVolume"] = str(self.server.level[host])
        elif action == "SetVolume":
            self.server.level[host] = int(options["DesiredVolume"])
        elif action == "GetMute":
            result["CurrentMute"] = str(self.server.muted[host])
        elif action == "SetMute":
            self.server.muted[host] = int(options["DesiredMute"])
        elif action == "Browse":
            result.update(Result=TRACK, TotalMatches="21")
        elif self.server.fail_skip and action == "Next":
            self.send_xml(b"<Fault><errorCode>701</errorCode></Fault>", 500)
            return
        envelope = ET.Element(f"{{{s1.SOAP_NS}}}Envelope")
        reply = ET.SubElement(ET.SubElement(envelope, f"{{{s1.SOAP_NS}}}Body"), f"{{{urn}}}{action}Response")
        for key, value in result.items():
            ET.SubElement(reply, key).text = value
        self.send_xml(ET.tostring(envelope))


class FixtureConnection(http.client.HTTPConnection):
    """Send fixture requests to loopback while preserving the logical speaker identity."""
    port_for_test = None

    def __init__(self, host, port, timeout):
        self.logical_host = host
        super().__init__("127.0.0.1", self.port_for_test, timeout=timeout)

    def request(self, method, url, body=None, headers=None):
        super().request(method, url, body, {**(headers or {}), "X-Test-Host": self.logical_host})


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Speaker)
        FixtureConnection.port_for_test = cls.httpd.server_port
        thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(cls.httpd.server_close)
        cls.addClassCleanup(cls.httpd.shutdown)

    def setUp(self):
        self.httpd.calls = []
        self.httpd.level = {FIRST: 10, SECOND: 15}
        self.httpd.muted = {FIRST: 0, SECOND: 0}
        self.httpd.state = "STOPPED"
        self.httpd.fail_skip = False
        for guard in (patch.object(s1, "ssdp_hosts", return_value=[FIRST, S2]),
                      patch.object(s1.http.client, "HTTPConnection", FixtureConnection)):
            guard.start()
            self.addCleanup(guard.stop)

    def invoke(self, *args):
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = remote.main(args)
        return code, json.loads((errors if code else output).getvalue())

    def test_discovery_follows_members_and_filters_s2_and_hidden_players(self):
        code, result = self.invoke("discover")
        self.assertEqual(code, 0)
        self.assertEqual([r["room"] for r in result["rooms"]], ["Den", "Kitchen"])
        self.assertEqual(result["rooms"][0]["group"], ["Kitchen", "Den"])
        self.assertEqual(result["warnings"], [])

    def test_group_play_and_pause_target_coordinator(self):
        for action, state in [("play", "PLAYING"), ("pause", "PAUSED_PLAYBACK")]:
            code, result = self.invoke("--room", "den", action)
            self.assertEqual((code, result["state"]), (0, state))
            params = {"InstanceID": "0", **({"Speed": "1"} if action == "play" else {})}
            self.assertIn((FIRST, action.title(), params), self.httpd.calls)

    def test_skips_sent_exactly_once_to_coordinator(self):
        for action in ["next", "previous"]:
            code, result = self.invoke("--room", "Den", action)
            self.assertEqual((code, result["accepted"]), (0, action))
            self.assertEqual(self.httpd.calls.count((FIRST, action.title(), {"InstanceID": "0"})), 1)

    def test_skip_refusal_keeps_error_code_and_does_not_retry(self):
        self.httpd.fail_skip = True
        code, result = self.invoke("--room", "Den", "next")
        self.assertEqual(code, 1)
        self.assertIn("701", result["error"])
        self.assertEqual(sum(a == "Next" for _, a, _ in self.httpd.calls), 1)

    def test_volume_boundaries_and_mute_are_room_local(self):
        for value in (0, 100):
            code, result = self.invoke("--room", "Den", "set-volume", str(value))
            self.assertEqual((code, result["volume"], self.httpd.level[FIRST]), (0, value, 10))
        for action, muted in [("mute", True), ("unmute", False)]:
            code, result = self.invoke("--room", IDS[SECOND], action)
            self.assertEqual((code, result["muted"], self.httpd.muted[FIRST]), (0, muted, 0))

    def test_invalid_volume_rejected_before_discovery(self):
        for value in ("-1", "101", "loud"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                remote.main(["set-volume", value])
        self.assertEqual(self.httpd.calls, [])

    def test_metadata_preserves_escaped_characters(self):
        code, result = self.invoke("--room", "Kitchen", "status")
        self.assertEqual((code, result["title"], result["artist"]), (0, "Tea & Toast", "Example Artist"))

    def test_queue_paginates_on_coordinator(self):
        code, result = self.invoke("--room", "Den", "queue", "--start", "20")
        self.assertEqual((code, result["total"], result["tracks"][0]["title"]), (0, 21, "Tea & Toast"))
        host, action, params = self.httpd.calls[-1]
        self.assertEqual((host, action, params["StartingIndex"], params["RequestedCount"]), (FIRST, "Browse", "20", "20"))

    def test_unknown_and_ambiguous_rooms_are_not_changed(self):
        for args in (("play",), ("--room", "Missing", "mute")):
            code, result = self.invoke(*args)
            self.assertEqual(code, 1)
            self.assertIn("Choose a room", result["error"])
        self.assertFalse(any(a in ("Play", "SetMute") for _, a, _ in self.httpd.calls))

    def test_empty_network_and_single_room(self):
        with patch.object(s1, "ssdp_hosts", return_value=[]):
            self.assertEqual(self.invoke("discover")[1]["rooms"], [])
            self.assertIn("No reachable", self.invoke("play")[1]["error"])
        rooms, _ = s1.discover()
        self.assertEqual(s1.choose(rooms[:1], None), rooms[0])
        with self.assertRaises(s1.RemoteError):
            s1.choose(rooms[:1], None, partial=True)

    def test_duplicate_names_require_id(self):
        rooms, _ = s1.discover()
        duplicate = s1.Room(rooms[0].name, rooms[1].device, rooms[1].coordinator, rooms[1].group)
        with self.assertRaises(s1.RemoteError):
            s1.choose([rooms[0], duplicate], rooms[0].name)
        self.assertEqual(s1.choose([rooms[0], duplicate], duplicate.device.uid), duplicate)

    def test_rejects_external_or_redirect_like_device_locations(self):
        for address in ("https://10.12.0.10:1400/x", "http://example.com:1400/x", "http://8.8.8.8:1400",
                        "http://user@10.12.0.10:1400", "http://127.0.0.1:1400", "http://10.12.0.10:80"):
            self.assertIsNone(s1.location_host(address))
        self.assertEqual(s1.location_host("http://10.12.0.10:1400/xml/desc.xml"), FIRST)

    def test_invalid_xml_and_entity_declarations_rejected(self):
        for data in (b"<bad", b'<!DOCTYPE x [<!ENTITY e "bad">]><x>&e;</x>'):
            with self.assertRaises(s1.RemoteError):
                s1.xml(data)


if __name__ == "__main__":
    unittest.main()
