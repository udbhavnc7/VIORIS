"""
Vioris Gateway — startup script.

Binds to 127.0.0.1 by default. Loopback-only is the safe default; to reach it
from a phone, opt in explicitly with `--host 0.0.0.0` only on a Tailscale
interface and never expose it to the public internet.

The pairing signing secret is generated once and persisted in
`vioris_data/pairing.json`; set VIORIS_PAIRING_SECRET to supply your own.

Usage:
    python run_gateway.py
    python run_gateway.py --port 8420 --host 0.0.0.0   # Tailscale only
"""

import argparse
import os
import sys

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Vioris API Gateway")
    parser.add_argument(
        "--host",
        default=os.getenv("VIORIS_API_HOST", "127.0.0.1"),
        help="Bind host (default: 127.0.0.1; pass 0.0.0.0 only on a private Tailscale network)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("VIORIS_API_PORT", "8420")),
        help="Bind port (default: 8420)",
    )
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    import uvicorn

    print(f"Starting Vioris Gateway on {args.host}:{args.port}")
    print(f"WebSocket: ws://{args.host}:{args.port}/ws/device")
    print(f"Dashboard: http://{args.host}:{args.port}/app")
    print(f"API docs:  http://{args.host}:{args.port}/docs")
    print()

    # Check if accessible over Tailscale
    try:
        import subprocess

        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            tailscale_ip = result.stdout.strip()
            print(f"Tailscale IP: {tailscale_ip}")
            print(f"Phone can connect: ws://{tailscale_ip}:{args.port}/ws/device")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("Tailscale not found — local access only")

    print()
    uvicorn.run(
        "services.api_gateway.app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
