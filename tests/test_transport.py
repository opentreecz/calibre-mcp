import asyncio
import os
from pathlib import Path
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import mock_calibre


ROOT = Path(__file__).resolve().parents[1]


def test_http_transport():
    subprocess.run([sys.executable, str(ROOT / "tests/container_smoke.py")],
                   check=True, timeout=40)


def test_stdio_transport():
    url, _, stop = mock_calibre.start()

    async def check():
        params = StdioServerParameters(command=sys.executable,
                                      args=[str(ROOT / "calibre_mcp.py")],
                                      env=dict(os.environ, CALIBRE_URL=url,
                                               CALIBRE_AUTH="none",
                                               MCP_TRANSPORT="stdio"))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("list_libraries", {})
                assert not result.isError
                assert "Calibre_Library" in result.content[0].text

    try:
        asyncio.run(asyncio.wait_for(check(), 30))
    finally:
        stop()
