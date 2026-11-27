"""
Vioris — one-command local runtime.

Starts the services the interface actually talks to, in dependency order:

    demo connectors  :8460   sample read payloads; writes refuse honestly
    memory           :8441   local memory store
    task runner      :8421   planning, approvals, execution, audit
    computer agent   :8430   screen mirror + remote input (optional)
    browser agent    :8431   isolated URL reads (optional)
    documents agent  :8432   allow-listed file reads (optional)
    knowledge index  :8442   approved-doc search (optional)
    API gateway      :8420   the only service the UI may reach
    UI host          :8425   React build served by a loopback Express proxy

Everything binds to 127.0.0.1. Phone access is meant to travel over Tailscale,
never a public port.

Usage:
    python run_vioris.py                 # demo data on, UI on
    python run_vioris.py --open          # also open the browser
    python run_vioris.py --critical-pin 1234
    python run_vioris.py --no-demo       # no sample connectors
    python run_vioris.py --no-agent      # skip local agents (screen mirroring, browser, files, knowledge)
    python run_vioris.py --no-ui         # backend only
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "apps" / "web"
DATA = ROOT / "vioris_data"

NPM = shutil.which("npm")
NODE = shutil.which("node")


@dataclass
class Service:
    key: str
    label: str
    port: int
    module: str
    health: str
    color: str
    optional: bool = False
    proc: subprocess.Popen | None = field(default=None, repr=False)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


COLORS = {
    "cyan": "\033[36m",
    "violet": "\033[35m",
    "green": "\033[32m",
    "amber": "\033[33m",
    "blue": "\033[34m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}


def color(name: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{COLORS.get(name, '')}{text}{COLORS['reset']}"


def local_agent_services(include_computer: bool) -> list[Service]:
    """Optional local daemons the executor routes browser/file tools to.

    They are optional: a missing Playwright browser or an empty file allow-list
    must not stop the core loop, and every planner route that needs them already
    reports the underlying failure instead of claiming success.
    """
    services = [
        Service(
            "browser",
            "Browser agent",
            8431,
            "agents.browser.app.daemon:app",
            "/health",
            "blue",
            optional=True,
        ),
        Service(
            "documents",
            "Documents agent",
            8432,
            "agents.documents.app.daemon:app",
            "/health",
            "amber",
            optional=True,
        ),
        Service(
            "knowledge",
            "Knowledge index",
            8442,
            "services.knowledge_service.app.api:app",
            "/knowledge/health",
            "violet",
            optional=True,
        ),
    ]
    if include_computer:
        services.insert(
            0,
            Service(
                "computer",
                "Computer agent",
                8430,
                "agents.computer.app.daemon:app",
                "/windows",
                "amber",
                optional=True,
            ),
        )
    return services


def probe(url: str, timeout: float = 0.8) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def stream(service: Service) -> None:
    assert service.proc and service.proc.stdout
    tag = color(service.color, f"{service.key:<9}")
    for line in service.proc.stdout:
        print(f"{tag} {line.rstrip()}", flush=True)


def start(service: Service, env: dict[str, str], logs: list[Service]) -> bool:
    if port_open(service.port):
        if probe(service.url + service.health):
            print(f"  {service.label:<16} already answering on {service.url} - reusing it")
            return True
        print(
            f"  {color('amber', service.label + ' port is busy')} "
            f"({service.port}) and does not answer {service.health}. Stop it or pick another port."
        )
        return False

    command = [
        sys.executable,
        "-m",
        "uvicorn",
        service.module,
        "--host",
        "127.0.0.1",
        "--port",
        str(service.port),
        "--log-level",
        "warning",
    ]
    service.proc = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )
    logs.append(service)
    threading.Thread(target=stream, args=(service,), daemon=True).start()
    print(f"  {service.label:<16} starting on {service.url}")
    return True


def wait_for(service: Service, seconds: float = 30.0) -> bool:
    if service.proc is None and probe(service.url + service.health):
        return True
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if service.proc is not None and service.proc.poll() is not None:
            print(f"  {color('amber', service.label + ' exited')} - see its log above")
            return False
        if probe(service.url + service.health):
            return True
        time.sleep(0.25)
    print(f"  {color('amber', service.label + ' did not answer in time')}")
    return False


def run(command: list[str], cwd: Path, env: dict[str, str]) -> bool:
    print(f"  $ {' '.join(command)}")
    try:
        result = subprocess.run(command, cwd=cwd, env=env, check=False)
    except FileNotFoundError:
        print("  npm was not found on PATH. Install Node.js 20+ and try again.")
        return False
    return result.returncode == 0


def build_ui(env: dict[str, str], force: bool) -> bool:
    if NPM is None:
        print("  npm was not found on PATH. Install Node.js 20+ and try again.")
        return False
    if not (WEB / "node_modules").exists():
        print("  Installing web dependencies (first run)...")
        if not run([NPM, "install", "--no-audit", "--no-fund"], WEB, env):
            return False
    if force or not (WEB / "dist" / "index.html").exists():
        print("  Building the interface...")
        return run([NPM, "run", "build"], WEB, env)
    return True


def gmail_credentials() -> tuple[str, str]:
    """Resolve Gmail OAuth credentials without ever printing them.

    Environment variables win; a gitignored local JSON file is the chat-safe
    alternative. Corrupt/unreadable files fail closed with empty values.
    """
    client_id = os.getenv("VIORIS_GMAIL_CLIENT_ID") or os.getenv(
        "VIORUS_GMAIL_CLIENT_ID", ""
    )
    client_secret = os.getenv("VIORIS_GMAIL_CLIENT_SECRET") or os.getenv(
        "VIORUS_GMAIL_CLIENT_SECRET", ""
    )
    if client_id and client_secret:
        return client_id, client_secret
    cred_file = DATA / "gmail_credentials.json"
    if cred_file.exists():
        try:
            data = json.loads(cred_file.read_text("utf-8"))
            client_id = client_id or str(data.get("client_id", ""))
            client_secret = client_secret or str(data.get("client_secret", ""))
        except (OSError, ValueError):
            pass
    return client_id, client_secret


def stop_all(services: list[Service]) -> None:
    running = [service for service in services if service.proc and service.proc.poll() is None]
    for service in running:
        service.proc.terminate()  # type: ignore[union-attr]
    deadline = time.monotonic() + 8
    for service in running:
        while service.proc.poll() is None and time.monotonic() < deadline:  # type: ignore[union-attr]
            time.sleep(0.1)
        if service.proc.poll() is None:  # type: ignore[union-attr]
            service.proc.kill()  # type: ignore[union-attr]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local Vioris stack")
    parser.add_argument("--no-demo", action="store_true", help="do not start sample connectors")
    parser.add_argument("--no-ui", action="store_true", help="backend only")
    parser.add_argument("--no-agent", action="store_true", help="skip the computer agent")
    parser.add_argument(
        "--gmail",
        action="store_true",
        help="start the real Gmail connector on 8445 (requires OAuth client credentials)",
    )
    parser.add_argument("--no-build", action="store_true", help="use the existing UI build as-is")
    parser.add_argument("--open", action="store_true", help="open the interface in a browser")
    parser.add_argument("--gateway-port", type=int, default=8420)
    parser.add_argument("--ui-port", type=int, default=8425)
    parser.add_argument(
        "--critical-pin",
        default=os.getenv("VIORIS_CRITICAL_PIN", ""),
        help="unlock Critical approvals for this run; omit to keep them locked",
    )
    args = parser.parse_args()

    DATA.mkdir(exist_ok=True)
    demo = not args.no_demo

    env = os.environ.copy()
    env["VIORIS_DEMO_CONNECTORS"] = "1" if demo else "0"
    env["VIORUS_TASK_RUNNER_URL"] = "http://127.0.0.1:8421"
    env["VIORIS_MEMORY_URL"] = "http://127.0.0.1:8441"
    env["VIORUS_COMPUTER_URL"] = "http://127.0.0.1:8430"
    env["VIORUS_BROWSER_URL"] = "http://127.0.0.1:8431"
    env["VIORUS_DOCUMENTS_URL"] = "http://127.0.0.1:8432"
    env["VIORUS_KNOWLEDGE_URL"] = "http://127.0.0.1:8442"
    if args.critical_pin:
        env["VIORIS_CRITICAL_PIN"] = args.critical_pin
    else:
        env.pop("VIORIS_CRITICAL_PIN", None)

    if demo:
        # The connector catalog reads these exact variables. Point every
        # read path at the sample service instead of a real account.
        for name in (
            "gmail",
            "whatsapp",
            "calendar",
            "contacts",
            "notes",
            "cloud_files",
            "bookmarks",
            "history",
            "telephony",
            "reservations",
            "smart_home",
        ):
            env[f"VIORUS_{name.upper()}_URL"] = f"http://127.0.0.1:8460/{name}"

    if args.gmail:
        client_id, client_secret = gmail_credentials()
        if not client_id or not client_secret:
            print(
                "  --gmail needs Gmail OAuth credentials. Set "
                "VIORIS_GMAIL_CLIENT_ID / VIORIS_GMAIL_CLIENT_SECRET, or write "
                "vioris_data/gmail_credentials.json with client_id and client_secret."
            )
            return 1
        # Override the demo URL set above: Gmail now talks to its real connector.
        env["VIORUS_GMAIL_URL"] = "http://127.0.0.1:8445"
        env["VIORIS_GMAIL_LIVE"] = "1"

    if not args.no_ui:
        if not build_ui(env, force=not args.no_build):
            print("Could not build the interface. Fix the error above and try again.")
            return 1

    services: list[Service] = []
    if demo:
        services.append(
            Service("samples", "Demo connectors", 8460, "services.demo_connectors.app:app", "/health", "blue")
        )
    services += [
        Service("memory", "Memory", 8441, "services.memory_service.app.api:app", "/memory", "violet"),
        Service("runner", "Task runner", 8421, "services.task_runner.app.api:app", "/tasks", "green"),
    ]
    if args.gmail:
        services.append(
            Service(
                "gmail", "Gmail connector", 8445, "integrations.gmail.api:app", "/health", "green"
            )
        )
    if not args.no_agent:
        services.extend(local_agent_services(include_computer=True))
    services.append(
        Service(
            "gateway",
            "API gateway",
            args.gateway_port,
            "services.api_gateway.app.main:app",
            "/health",
            "cyan",
        )
    )
    if args.gateway_port != 8420:
        env["VIORIS_API_PORT"] = str(args.gateway_port)

    print(color("violet", "\nVioris") + color("dim", "  local-first. observe broadly, act narrowly.\n"))
    logs: list[Service] = []
    started: list[Service] = []
    try:
        for service in services:
            if not start(service, env, logs):
                if service.optional:
                    continue
                stop_all(started)
                return 1
            started.append(service)

        ready = True
        for service in services:
            if service not in started:
                continue
            if not wait_for(service):
                if not service.optional:
                    ready = False
                else:
                    print(f"  {service.label} is not available; screen mirroring will report offline.")

        if not ready:
            stop_all(started)
            return 1

        ui_proc = None
        if not args.no_ui:
            if port_open(args.ui_port):
                print(
                    f"  {color('amber', 'Interface port is busy')} ({args.ui_port}). "
                    "Stop whatever owns it, or pass --ui-port <n>."
                )
                stop_all(started)
                return 1
            ui_env = env.copy()
            ui_env["VIORIS_UI_PORT"] = str(args.ui_port)
            ui_env["VIORIS_GATEWAY_URL"] = f"http://127.0.0.1:{args.gateway_port}"
            ui_proc = subprocess.Popen(
                [NODE or "node", "server/index.mjs"],
                cwd=WEB,
                env=ui_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )
            ui_tag = color("cyan", f"{'ui':<9}")
            assert ui_proc.stdout
            threading.Thread(
                target=lambda: [print(f"{ui_tag} {line.rstrip()}", flush=True) for line in ui_proc.stdout],
                daemon=True,
            ).start()
            if not wait_for(
                Service("ui", "Interface", args.ui_port, "", "/", "cyan"),
                seconds=20,
            ):
                print("  The interface did not answer. Its log is above.")
                stop_all(started)
                ui_proc.terminate()
                return 1
        else:
            ui_proc = None

        print()
        print(color("green", "  Ready."))
        if ui_proc:
            print(f"    Interface   http://127.0.0.1:{args.ui_port}")
        print(f"    Gateway     http://127.0.0.1:{args.gateway_port}")
        print(f"    API docs    http://127.0.0.1:{args.gateway_port}/docs")
        print(f"    Data        {DATA}")
        print(
            "    Critical    "
            + (
                color("amber", "unlocked for this run (PIN + 5s cooldown still required)")
                if args.critical_pin
                else "locked (start with --critical-pin to allow them)"
            )
        )
        if demo:
            print("    Data mode   " + color("blue", "demo - sample reads, writes refuse honestly"))
        if args.gmail:
            print(
                "    Gmail       "
                + color("green", "live connector on 8445 (read-only unless write scopes are opted in)")
            )
        print(color("dim", "\n  Press Ctrl+C to stop everything.\n"))

        if ui_proc and args.open:
            webbrowser.open(f"http://127.0.0.1:{args.ui_port}")

        while True:
            for service in started:
                if service.proc and service.proc.poll() is not None:
                    print(f"\n  {service.label} stopped unexpectedly. Shutting down.")
                    return 1
            if ui_proc and ui_proc.poll() is not None:
                print("\n  The interface stopped unexpectedly. Shutting down.")
                return 1
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n  Stopping...")
        return 0
    finally:
        if "ui_proc" in locals() and ui_proc and ui_proc.poll() is None:
            ui_proc.terminate()
        stop_all(started)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.default_int_handler)
    sys.exit(main())
