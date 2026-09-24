"""Exercise a real HTTP MCP process against a disposable Content Server."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import requests

import mock_calibre


def main():
    url, _, stop = mock_calibre.start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = Path("/app/calibre_mcp.py")
    if not server.exists():
        server = Path(__file__).resolve().parents[1] / "calibre_mcp.py"
    env = dict(os.environ, CALIBRE_URL=url, CALIBRE_AUTH="none",
               CALIBRE_LIBRARY_ID="", CALIBRE_READONLY="1",
               MCP_TRANSPORT="streamable-http", MCP_HOST="127.0.0.1",
               MCP_PORT=str(port), MCP_PATH="/custom")
    process = subprocess.Popen([sys.executable, str(server)], env=env)
    endpoint = f"http://127.0.0.1:{port}/custom"
    session = requests.Session()
    session.headers.update({"Accept": "application/json, text/event-stream"})

    def rpc(method, params, number=1):
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if number is not None:
            body["id"] = number
        response = session.post(endpoint, json=body, timeout=10)
        response.raise_for_status()
        sid = response.headers.get("mcp-session-id")
        if sid:
            session.headers["mcp-session-id"] = sid
        if number is None:
            return None
        if response.headers.get("content-type", "").startswith("application/json"):
            return response.json()["result"]
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:])["result"]
        raise AssertionError(response.text)

    try:
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError("MCP server exited during startup")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.1)
        info = rpc("initialize", {"protocolVersion": "2025-06-18",
                                  "capabilities": {},
                                  "clientInfo": {"name": "ci", "version": "1"}})
        session.headers["MCP-Protocol-Version"] = info["protocolVersion"]
        rpc("notifications/initialized", {}, None)
        assert any(t["name"] == "search_books" for t in rpc("tools/list", {})["tools"])
        result = rpc("tools/call", {"name": "search_books", "arguments": {}})
        assert not result.get("isError"), result
        assert json.loads(result["content"][0]["text"])["books"][0]["title"] == "Main Book"
        result = rpc("tools/call", {"name": "conversion_job_status",
                                    "arguments": {"job_id": 1, "abort": True}})
        assert result["isError"]
        print("HTTP MCP initialization, tool calls, custom path and read-only checks passed")
    finally:
        session.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        stop()


if __name__ == "__main__":
    main()
