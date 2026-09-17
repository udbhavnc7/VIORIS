"""
Vioris Gateway — startup script.

Binds to 0.0.0.0:8420 (accessible over Tailscale).

Usage:
    python run_gateway.py
    python run_gateway.py --port 8420 --host 0.0.0.0
"""

import argparse
import os
import sys

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("VIORIS_PAIRING_SECRET", "vioris_stable_dev_secret_key_2026_x89a")


def main() -> None:
    parser = argparse.ArgumentParser(description="Vioris API Gateway")
    parser.add_argument(
        "--host",
        default=os.getenv("VIORIS_API_HOST", "0.0.0.0"),
        help="Bind host (default: 0.0.0.0 — accessible over Tailscale)",
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
