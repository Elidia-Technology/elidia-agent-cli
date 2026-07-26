"""Tests for elidia.daemon.ipc — real Unix domain socket server + client,
in-process (no subprocess needed to test the protocol itself)."""
from pathlib import Path

import pytest

from elidia.daemon.ipc import DaemonIPCServer, send_request


class TestIPCRoundTrip:
    @pytest.mark.asyncio
    async def test_status_style_request(self, tmp_dir: Path):
        socket_path = tmp_dir / "test.sock"

        async def handler(request: dict) -> dict:
            if request["cmd"] == "status":
                return {"ok": True, "status": {"running": True, "task_count": 3}}
            return {"ok": False, "error": "unknown"}

        server = DaemonIPCServer(socket_path, handler)
        await server.start()
        try:
            response = await send_request(socket_path, {"cmd": "status"})
            assert response == {"ok": True, "status": {"running": True, "task_count": 3}}
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_unknown_command_gets_error_response(self, tmp_dir: Path):
        socket_path = tmp_dir / "test.sock"

        async def handler(request: dict) -> dict:
            return {"ok": False, "error": f"unknown command: {request.get('cmd')}"}

        server = DaemonIPCServer(socket_path, handler)
        await server.start()
        try:
            response = await send_request(socket_path, {"cmd": "bogus"})
            assert response["ok"] is False
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_handler_exception_becomes_error_response_not_crash(self, tmp_dir: Path):
        socket_path = tmp_dir / "test.sock"

        async def handler(request: dict) -> dict:
            raise RuntimeError("boom")

        server = DaemonIPCServer(socket_path, handler)
        await server.start()
        try:
            response = await send_request(socket_path, {"cmd": "status"})
            assert response["ok"] is False
            assert "boom" in response["error"]
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_client_gets_connection_error_when_no_server(self, tmp_dir: Path):
        socket_path = tmp_dir / "nobody-listening.sock"
        with pytest.raises((ConnectionRefusedError, FileNotFoundError, OSError)):
            await send_request(socket_path, {"cmd": "status"}, timeout=1.0)

    @pytest.mark.asyncio
    async def test_stop_removes_socket_file(self, tmp_dir: Path):
        socket_path = tmp_dir / "test.sock"

        async def handler(request: dict) -> dict:
            return {"ok": True}

        server = DaemonIPCServer(socket_path, handler)
        await server.start()
        assert socket_path.exists()
        await server.stop()
        assert not socket_path.exists()

    @pytest.mark.asyncio
    async def test_multiple_sequential_requests(self, tmp_dir: Path):
        socket_path = tmp_dir / "test.sock"
        call_count = {"n": 0}

        async def handler(request: dict) -> dict:
            call_count["n"] += 1
            return {"ok": True, "call_number": call_count["n"]}

        server = DaemonIPCServer(socket_path, handler)
        await server.start()
        try:
            r1 = await send_request(socket_path, {"cmd": "ping"})
            r2 = await send_request(socket_path, {"cmd": "ping"})
            assert r1["call_number"] == 1
            assert r2["call_number"] == 2
        finally:
            await server.stop()
