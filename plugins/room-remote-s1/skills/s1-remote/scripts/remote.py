"""Command-line entry point for Room Remote for S1."""
import argparse
import json
import sys

from s1 import RemoteError, run


def integer(text, minimum, maximum):
    value = int(text)
    if not minimum <= value <= maximum:
        raise argparse.ArgumentTypeError(f"Choose a number between {minimum} and {maximum}.")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="Discover and control local Sonos S1 rooms.")
    parser.add_argument("--room", help="Exact room name or discovered speaker ID")
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("discover", "status", "play", "pause", "next", "previous", "volume", "mute", "unmute"):
        commands.add_parser(action)
    commands.add_parser("set-volume").add_argument("value", type=lambda n: integer(n, 0, 100))
    commands.add_parser("queue").add_argument("--start", type=lambda n: integer(n, 0, 2**31-1), default=0)
    args = parser.parse_args(argv)
    try:
        result = run(args.action, args.room, getattr(args, "value", getattr(args, "start", None)))
    except (RemoteError, OSError, KeyError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
