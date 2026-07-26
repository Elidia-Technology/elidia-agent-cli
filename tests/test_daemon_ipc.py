"""Tests for elidia.daemon.ipc — real Unix domain socket server + client,
in-process (no subprocess needed to test the protocol itself)."""
from pathlib import Path

import pytest

from elidia.daemon.ipc import DaemonIPCServer, send_request, stream_request


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

import asyncio

class TestStreamingProtocol:
    """New in Phase 0 (Elidia Agent Desktop IPC bridge) — the daemon socket
    now supports streaming commands where one request yields MULTIPLE response
    lines (one per event), terminated by {"event":"done"}, on the same Unix
    socket the existing single-response commands already use."""

    @pytest.mark.asyncio
    async def test_chat_stream_yields_tool_call_and_content_then_done(self, tmp_dir: Path):
        socket_path = tmp_dir / "test.sock"

        async def stream_handler(request):
            for e in [
                {"event": "thinking", "data": {"model": "test-model"}},
                {"event": "tool_call", "data": {"name": "file_read", "arguments": {"path": "x.txt"}}},
                {"event": "tool_result", "data": {"name": "file_read", "content": "hello"}},
                {"event": "content", "data": "The file says: hello"},
            ]:
                yield e

        server = DaemonIPCServer(socket_path, handler=lambda r: {"ok": False}, stream_handler=stream_handler)
        await server.start()
        try:
            events = []
            async for event in stream_request(socket_path, {"cmd": "chat", "messages": [{"role": "user", "content": "read x.txt"}]}):
                events.append(event)
            assert len(events) == 5  # 4 handler events + 1 trailing done
            assert events[1]["event"] == "tool_call"
            assert events[1]["data"]["name"] == "file_read"
            assert events[-1]["event"] == "done"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_stream_handler_exception_becomes_error_event_then_done(self, tmp_dir: Path):
        socket_path = tmp_dir / "test.sock"

        async def stream_handler(request):
            yield {"event": "content", "data": "before"}
            raise RuntimeError("mid-stream crash")

        server = DaemonIPCServer(socket_path, handler=lambda r: {"ok": False}, stream_handler=stream_handler)
        await server.start()
        try:
            events = []
            async for event in stream_request(socket_path, {"cmd": "chat", "messages": []}):
                events.append(event)
            assert events[0]["event"] == "content"
            assert events[1]["event"] == "error"
            assert "mid-stream crash" in events[1]["data"]
            assert events[2]["event"] == "done"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_non_streaming_command_still_uses_original_handler(self, tmp_dir: Path):
        """A command NOT in STREAMING_COMMANDS (e.g. 'ping') must still go through
        the regular single-response handler even when a stream_handler is also
        configured on the same server."""
        socket_path = tmp_dir / "test.sock"

        async def handler(request):
            return {"ok": True, "cmd": request.get("cmd")}

        async def stream_handler(request):
            yield {"event": "should", "data": "not be called"}

        server = DaemonIPCServer(socket_path, handler=handler, stream_handler=stream_handler)
        await server.start()
        try:
            response = await send_request(socket_path, {"cmd": "ping"})
            assert response == {"ok": True, "cmd": "ping"}
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_stream_client_times_out_on_overall_deadline(self, tmp_dir: Path):
        socket_path = tmp_dir / "test.sock"

        async def stream_handler(request):
            yield {"event": "content", "data": "starts"}
            await asyncio.sleep(20)  # way past stream_request's timeout

        server = DaemonIPCServer(socket_path, handler=lambda r: {"ok": False}, stream_handler=stream_handler)
        await server.start()
        try:
            events = []
            with pytest.raises(TimeoutError):
                async for event in stream_request(socket_path, {"cmd": "chat", "messages": []}, timeout=2.0):
                    events.append(event)
            assert len(events) == 1
            assert events[0]["event"] == "content"
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_send_request_connection_error_still_raised(self, tmp_dir: Path):
        """send_request is for simple commands; having a stream_handler does
        not affect its behavior when no server is listening."""
        socket_path = tmp_dir / "nobody.sock"
        with pytest.raises((ConnectionRefusedError, FileNotFoundError, OSError)):
            await send_request(socket_path, {"cmd": "status"}, timeout=1.0)
