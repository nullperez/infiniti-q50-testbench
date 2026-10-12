"""Local control server: line-delimited JSON-RPC over TCP on 127.0.0.1.

Request:  {"id": 1, "method": "set_signal", "params": {"path": "main.180.ENGINE_RPM", "value": 2000}}
Response: {"id": 1, "result": {...}}  or  {"id": 1, "error": {"message": "..."}}
Special methods: "tools.list" returns the tool registry (name, description, input_schema).

The GUI app owns the hardware; scripts and the MCP server drive it through
this port instead of opening adapters/serial ports themselves.
"""

from __future__ import annotations

import json
import socket
import socketserver
import threading
import traceback
from typing import Optional

from q50tb.control.api import TOOLS, ApiError, BenchAPI

DEFAULT_PORT = 8765


def _jsonable(obj):
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex(" ")
    if isinstance(obj, set):
        return sorted(obj)
    return str(obj)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        srv: "ControlServer" = self.server.owner  # type: ignore[attr-defined]
        for raw in self.rfile:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            rid = None
            try:
                req = json.loads(line)
                rid = req.get("id")
                if srv.token and req.get("token") != srv.token:
                    raise ApiError("bad or missing token")
                method = req.get("method", "")
                if method == "tools.list":
                    result = list(TOOLS.values())
                else:
                    with srv.lock:
                        result = srv.api.call(method, req.get("params") or {})
                resp = {"id": rid, "result": result}
            except ApiError as exc:
                resp = {"id": rid, "error": {"message": str(exc)}}
            except TypeError as exc:
                resp = {"id": rid, "error": {"message": f"bad parameters: {exc}"}}
            except Exception as exc:
                resp = {"id": rid, "error": {"message": f"{type(exc).__name__}: {exc}",
                                             "trace": traceback.format_exc(limit=4)}}
            self.wfile.write((json.dumps(resp, default=_jsonable) + "\n").encode())
            self.wfile.flush()


class _TCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class ControlServer:
    def __init__(self, api: BenchAPI, port: int = DEFAULT_PORT, token: str = ""):
        self.api = api
        self.port = port
        self.token = token
        self.lock = threading.Lock()   # one API call at a time
        self._srv: Optional[_TCPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._srv = _TCPServer(("127.0.0.1", self.port), _Handler)
        self._srv.owner = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._srv.serve_forever, name="control-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None


class ControlClient:
    """Tiny synchronous client (used by the MCP server and scripts)."""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, token: str = "", timeout: float = 30.0):
        self.addr = (host, port)
        self.token = token
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._file = None
        self._id = 0
        self._lock = threading.Lock()

    def connect(self) -> None:
        self._sock = socket.create_connection(self.addr, timeout=self.timeout)
        self._file = self._sock.makefile("rwb")

    def close(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def call(self, method: str, params: Optional[dict] = None):
        with self._lock:
            if self._sock is None:
                self.connect()
            self._id += 1
            req = {"id": self._id, "method": method, "params": params or {}}
            if self.token:
                req["token"] = self.token
            try:
                self._file.write((json.dumps(req) + "\n").encode())
                self._file.flush()
                line = self._file.readline()
            except OSError:
                self.close()
                raise
            if not line:
                self.close()
                raise ConnectionError("control server closed the connection")
            resp = json.loads(line)
            if "error" in resp:
                raise ApiError(resp["error"].get("message", "error"))
            return resp.get("result")
