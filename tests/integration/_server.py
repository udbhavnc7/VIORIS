"""Shared helper: boot a FastAPI app on a free port as a real uvicorn server.

Used by the full-stack exit e2e tests (Phase 5, Phase 6). No component is
stubbed over HTTP — these servers are the real production apps, and every hop
between them is real HTTP.

The probe loop catches `requests.exceptions.RequestError` (connect/read/timeout)
and retries until the deadline, so a slow bind under test load never aborts a
fixture on the first transient timeout.
"""

from __future__ import annotations

import threading
import time

import requests
import uvicorn


class Server:
    """Boot `app` on a free port and wait until it answers `probe_path`."""

    def __init__(self, app, port: int, probe_path: str = "/health") -> None:
        self.port = port
        self._probe = probe_path
        self.thread = threading.Thread(
            target=uvicorn.run,
            kwargs={"app": app, "host": "127.0.0.1", "port": port, "log_level": "error"},
            daemon=True,
        )

    def start(self, seconds: float = 6.0) -> None:
        self.thread.start()
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                r = requests.get(f"http://127.0.0.1:{self.port}{self._probe}", timeout=1)
                if 200 <= r.status_code < 600:
                    return
            except requests.exceptions.RequestError:
                pass
            time.sleep(0.05)
        raise RuntimeError(f"server on :{self.port} did not become healthy")


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
