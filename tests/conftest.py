import sys

import pytest

import mock_calibre


@pytest.fixture()
def srv(monkeypatch):
    url, state, stop = mock_calibre.start()
    monkeypatch.setenv("CALIBRE_URL", url)
    monkeypatch.setenv("CALIBRE_AUTH", "none")
    monkeypatch.setenv("CALIBRE_READONLY", "0")
    monkeypatch.delenv("CALIBRE_LIBRARY_ID", raising=False)
    sys.modules.pop("calibre_mcp", None)
    import calibre_mcp
    try:
        yield calibre_mcp, state
    finally:
        if calibre_mcp._session is not None:
            calibre_mcp._session.close()
        stop()
