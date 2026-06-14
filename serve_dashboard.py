#!/usr/bin/env python
"""Serve the dashboard as a web app — view it from a browser or phone, or deploy to a VPS.

Local (view in your own browser):
    python serve_dashboard.py
    # then open http://localhost:8000

On a VPS / phone access (expose on the network):
    python serve_dashboard.py --host 0.0.0.0 --port 8000
    # then open http://<your-vps-ip>:8000 from your phone

Requires: pip install textual-serve   (already in requirements.txt)

SECURITY: --host 0.0.0.0 exposes the dashboard to anyone who can reach the port.
On a public VPS, put it behind a reverse proxy (nginx/Caddy) with TLS + basic auth,
or restrict the port to your IP / a VPN. Pressing 'r' in the dashboard re-runs the
pipeline (downloads data, hits the live Polymarket feed) — fine on a server with network.
"""
import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the World Cup edge dashboard over the web.")
    parser.add_argument("--host", default="localhost", help="Bind host (use 0.0.0.0 to expose on a VPS)")
    parser.add_argument("--port", type=int, default=8000, help="Port to serve on")
    args = parser.parse_args()

    try:
        from textual_serve.server import Server
    except ImportError:
        print("textual-serve is not installed. Run:  pip install textual-serve")
        raise SystemExit(1)

    launcher = Path(__file__).resolve().parent / "run_dashboard.py"
    command = f'"{sys.executable}" "{launcher}"'
    server = Server(command, host=args.host, port=args.port, title="World Cup 2026 Edge")
    print(f"Serving the dashboard at http://{args.host}:{args.port}")
    if args.host != "localhost":
        print("Exposed on the network — open http://<this-machine-ip>:%d from your phone." % args.port)
        print("Put it behind a reverse proxy + auth before leaving it on a public VPS.")
    server.serve()


if __name__ == "__main__":
    main()
