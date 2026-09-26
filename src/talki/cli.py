import argparse
import logging
import sys

from . import __version__
from .paths import LOG_PATH, ensure_dirs


def _setup_logging(verbose: bool) -> None:
    ensure_dirs()
    handlers = [logging.StreamHandler(), logging.FileHandler(LOG_PATH)]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    for noisy in ("httpx", "httpcore", "huggingface_hub", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main() -> None:
    from .ipc import COMMANDS, send

    p = argparse.ArgumentParser(prog="talki", description="Local voice dictation for Linux.")
    p.add_argument("--version", action="version", version=f"talki {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="cmd")
    ctl = sub.add_parser("ctl", help="control a running Talki instance")
    ctl.add_argument("action", choices=sorted(COMMANDS), help=", ".join(f"{k}: {v}" for k, v in COMMANDS.items()))
    ctl.add_argument("args", nargs="*")
    sub.add_parser("devices", help="list microphones")
    sub.add_parser("doctor", help="check the system for everything Talki needs")
    conf = sub.add_parser("config", help="read or change a setting, e.g. `talki config stt.backend whisper-server`")
    conf.add_argument("key", nargs="?", help="dotted key; omit to print all settings")
    conf.add_argument("value", nargs="?", help="new value (JSON, or a plain string)")
    args = p.parse_args()

    if args.cmd == "ctl":
        reply = send(args.action, *args.args)
        if reply is None:
            print("Talki is not running.", file=sys.stderr)
            sys.exit(1)
        print(reply)
        return
    if args.cmd == "devices":
        from .audio import list_sources

        for name, desc in list_sources():
            print(f"{desc}\n    {name}")
        return
    if args.cmd == "config":
        import json

        from .config import Config

        cfg = Config()
        if not args.key:
            print(json.dumps(cfg.data, indent=2, ensure_ascii=False))
        elif args.value is None:
            print(json.dumps(cfg.get(args.key), indent=2, ensure_ascii=False))
        else:
            try:
                value = json.loads(args.value)
            except json.JSONDecodeError:
                value = args.value
            cfg.set(args.key, value)
            print(f"{args.key} = {json.dumps(value)} (restart Talki to apply)")
        return
    if args.cmd == "doctor":
        from .doctor import run

        sys.exit(run())

    _setup_logging(args.verbose)
    from .app import TalkiApp

    sys.exit(TalkiApp(sys.argv).run())
