"""Entry point: python -m backend [--station NAME] [--port N] [--host H] [--show-attribution]"""
import argparse
import socket
import sys

import uvicorn

from . import app as app_module


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.connect_ex((host, port)) == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Queensland Rail station sign server")
    parser.add_argument("--station", default="Northgate", help="station shown by default (name or id)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8731)
    parser.add_argument(
        "--show-attribution",
        action="store_true",
        help="show the TransLink data attribution / licence line at the bottom of the sign (hidden by default)",
    )
    args = parser.parse_args()
    if _port_in_use(args.host, args.port):
        sys.exit(
            f"Port {args.port} is already in use (is the sign already running?). "
            f"Stop it, or pick another port with --port N."
        )
    app_module.settings["station"] = args.station
    app_module.settings["show_attribution"] = args.show_attribution
    uvicorn.run(app_module.app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
