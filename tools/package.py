"""Build and inspect the desktop directory ZIP using only the standard library."""
import argparse
import json
from pathlib import Path
import struct
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "room-remote-s1"
FILES = (
    "plugin.json", ".codex-plugin/plugin.json", "LICENSE",
    "assets/speaker.png", "assets/Bootstrap-Icons-LICENSE.txt",
    "skills/s1-remote/SKILL.md", "skills/s1-remote/scripts/remote.py",
    "skills/s1-remote/scripts/s1.py",
)


def check_package(path):
    with zipfile.ZipFile(path) as archive:
        if sorted(archive.namelist()) != sorted(FILES) or archive.testzip():
            raise ValueError("Invalid archive contents.")
        manifest = json.loads(archive.read("plugin.json"))
        legacy = json.loads(archive.read(".codex-plugin/plugin.json"))
        ext = manifest["extensions"]["com.openai"]
        interface = ext["interface"]
        if manifest["$schema"] != "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json":
            raise ValueError("Unknown portable schema.")
        for field in ("name", "version", "author", "license"):
            if legacy[field] != manifest[field]:
                raise ValueError(f"Manifest mismatch in {field}.")
        if legacy["interface"] != interface or ext["requires_local_executor"] is not True:
            raise ValueError("Desktop interface mismatch.")
        if not 1 <= len(interface["shortDescription"]) <= 30 or len(interface["displayName"]) > 30:
            raise ValueError("Invalid listing text.")
        if interface["category"] != "Entertainment" or ext["review"]["commerce"] is not False:
            raise ValueError("Unexpected category or commerce settings.")
        for name, minimum in (("logo", 256), ("composerIcon", 48)):
            image = archive.read(interface[name].removeprefix("./"))
            width, height = struct.unpack(">II", image[16:24])
            if (not image.startswith(b"\x89PNG\r\n\x1a\n") or width != height or
                    not minimum <= width <= 4096 or len(image) > 5 * 1024 * 1024):
                raise ValueError("Invalid icon.")
        for name in FILES:
            if name.endswith(".py"):
                compile(archive.read(name), name, "exec")


def build(destination):
    version = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))["version"]
    destination.mkdir(exist_ok=True, parents=True)
    result = destination / f"room-remote-s1-{version}.zip"
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            source = ROOT / name if name == "LICENSE" else PLUGIN / name
            archive.writestr(info, source.read_bytes())
    check_package(result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    print(build(parser.parse_args().output_dir))
