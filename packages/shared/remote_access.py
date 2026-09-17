"""Remote Access — phone control center, WebSocket bridge over Tailscale."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Optional


class RemoteCommand(Enum):
    PING = "ping"
    STATUS = "status"
    START_CALL = "start_call"
    END_CALL = "end_call"
    APPROVE = "approve"
    REJECT = "reject"
    GET_CONTACTS = "get_contacts"
    GET_HISTORY = "get_history"
    GET_SETTINGS = "get_settings"
    UPDATE_SETTINGS = "update_settings"
    EXECUTE_ACTION = "execute_action"
    VOICE_COMMAND = "voice_command"


@dataclass
class RemoteRequest:
    command: RemoteCommand
    params: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({
            "command": self.command.value,
            "params": self.params,
            "request_id": self.request_id,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_json(cls, raw: str) -> "RemoteRequest":
        d = json.loads(raw)
        return cls(
            command=RemoteCommand(d["command"]),
            params=d.get("params", {}),
            request_id=d.get("request_id", ""),
            timestamp=d.get("timestamp", time.time()),
        )


@dataclass
class RemoteResponse:
    request_id: str
    success: bool
    data: Any = None
    error: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps({
            "request_id": self.request_id,
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_json(cls, raw: str) -> "RemoteResponse":
        d = json.loads(raw)
        return cls(
            request_id=d["request_id"],
            success=d["success"],
            data=d.get("data"),
            error=d.get("error", ""),
            timestamp=d.get("timestamp", time.time()),
        )


class PhoneControlCenter:
    """Handles commands from the phone app over Tailscale WebSocket."""

    def __init__(self):
        self._handlers: dict[RemoteCommand, Callable] = {}
        self._connected_devices: dict[str, dict] = {}
        self._command_history: list[dict] = []
        self._register_defaults()

    def _register_defaults(self):
        self.on(RemoteCommand.PING, self._handle_ping)
        self.on(RemoteCommand.STATUS, self._handle_status)

    def on(self, command: RemoteCommand, handler: Callable):
        self._handlers[command] = handler

    async def handle(self, request: RemoteRequest) -> RemoteResponse:
        handler = self._handlers.get(request.command)
        if not handler:
            return RemoteResponse(
                request_id=request.request_id,
                success=False,
                error=f"Unknown command: {request.command.value}",
            )

        try:
            if asyncio.iscoroutinefunction(handler):
                result = await handler(request)
            else:
                result = handler(request)

            self._command_history.append({
                "command": request.command.value,
                "request_id": request.request_id,
                "success": True,
                "timestamp": datetime.now(UTC).isoformat(),
            })

            return RemoteResponse(
                request_id=request.request_id,
                success=True,
                data=result,
            )
        except Exception as e:
            return RemoteResponse(
                request_id=request.request_id,
                success=False,
                error=str(e),
            )

    def register_device(self, device_id: str, device_type: str, name: str = ""):
        self._connected_devices[device_id] = {
            "device_type": device_type,
            "name": name,
            "connected_at": datetime.now(UTC).isoformat(),
            "last_seen": datetime.now(UTC).isoformat(),
        }

    def unregister_device(self, device_id: str):
        self._connected_devices.pop(device_id, None)

    def get_devices(self) -> list[dict]:
        return [{"id": k, **v} for k, v in self._connected_devices.items()]

    def get_command_history(self, limit: int = 50) -> list[dict]:
        return self._command_history[-limit:]

    async def _handle_ping(self, request: RemoteRequest) -> dict:
        return {"pong": True, "server_time": datetime.now(UTC).isoformat()}

    async def _handle_status(self, request: RemoteRequest) -> dict:
        return {
            "connected_devices": len(self._connected_devices),
            "commands_processed": len(self._command_history),
            "uptime": datetime.now(UTC).isoformat(),
        }


class TailscaleBridge:
    """WebSocket bridge between phone and laptop over Tailscale."""

    def __init__(self, tailscale_ip: str = "", port: int = 8765):
        self.tailscale_ip = tailscale_ip
        self.port = port
        self._server = None
        self._connections: dict[str, Any] = {}
        self._control_center = PhoneControlCenter()

    async def start(self) -> tuple[bool, str]:
        try:
            import websockets
            self._server = await websockets.serve(
                self._handle_connection,
                self.tailscale_ip or "0.0.0.0",
                self.port,
            )
            return True, f"Server started on {self.tailscale_ip}:{self.port}"
        except ImportError:
            return False, "websockets not installed"
        except Exception as e:
            return False, str(e)

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_connection(self, websocket, path=None):
        device_id = str(id(websocket))
        self._connections[device_id] = websocket

        try:
            async for message in websocket:
                request = RemoteRequest.from_json(message)
                response = await self._control_center.handle(request)
                await websocket.send(response.to_json())
        finally:
            self._connections.pop(device_id, None)

    async def send_to_device(self, device_id: str, request: RemoteRequest) -> Optional[RemoteResponse]:
        websocket = self._connections.get(device_id)
        if not websocket:
            return None

        try:
            await websocket.send(request.to_json())
            raw = await asyncio.wait_for(websocket.recv(), timeout=10)
            return RemoteResponse.from_json(raw)
        except Exception:
            return None

    async def broadcast(self, request: RemoteRequest) -> list[RemoteResponse]:
        responses = []
        for device_id in list(self._connections.keys()):
            resp = await self.send_to_device(device_id, request)
            if resp:
                responses.append(resp)
        return responses

    @property
    def connected_count(self) -> int:
        return len(self._connections)

    @property
    def control_center(self) -> PhoneControlCenter:
        return self._control_center
