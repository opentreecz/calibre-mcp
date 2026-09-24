import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_server import _reading_list_prefix


def test_readonly_abort_does_not_reach_server(srv):
    m, state = srv
    job = json.loads(m.convert_book(1, "mobi", wait=False))["job_id"]
    m.READONLY = True
    with pytest.raises(m.CalibreError, match="read-only"):
        m.conversion_job_status(job, abort=True)
    assert job in state.jobs
    assert json.loads(m.conversion_job_status(job))["running"]


def test_large_search_preserves_order_and_batches(srv):
    m, state = srv
    for i in range(2, 452):
        state.db["Calibre_Library"][i] = {
            "application_id": i, "title": str(i), "formats": ["EPUB"]}
    result = json.loads(m.search_books(limit=451, sort="application_id"))
    assert [b["id"] for b in result["books"]] == list(range(451, 0, -1))
    assert len([s for s in state.seen if s == ("GET", "/ajax/books")]) == 3


def test_all_search_applies_pagination(srv):
    m, _ = srv
    result = json.loads(m.search_books(library="all", offset=1))
    assert all(not library["books"] for library in result.values())


def test_saved_searches_are_per_library(srv):
    m, _ = srv
    m.create_saved_search("test", "tags:test", library="calibre_pdb")
    assert json.loads(m.list_saved_searches()) == {}


def test_pairing_refuses_multiple_missing_works():
    ns = _reading_list_prefix()
    missing = [(1, "A", "olbracht", []), (2, "B", "olbracht", [])]
    books = [{"id": 1, "authors": ["Ivan Olbracht"]}]
    assert ns["pair_by_author"](missing, books) == ([], missing, books)


def test_http_settings(srv, monkeypatch):
    m, _ = srv
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MCP_PATH", "/custom")
    monkeypatch.setenv("MCP_PORT", "9876")
    monkeypatch.setattr(m.mcp, "run", lambda **kw: None)
    m.main()
    assert m.mcp.settings.streamable_http_path == "/custom"
    assert m.mcp.settings.port == 9876


def test_copy_refuses_implicit_default_as_target(srv):
    m, state = srv
    with pytest.raises(m.CalibreError, match="same library"):
        m.copy_to_library([1], "Calibre_Library", move=True)
    assert not any(s[0] == "COPY" for s in state.seen)


def test_copy_requires_explicit_target(srv):
    m, _ = srv
    m.LIB = "Calibre_Library"
    with pytest.raises(m.CalibreError, match="target library"):
        m.copy_to_library([7], "", library="calibre_pdb")


def test_uploads_book_and_format(srv, tmp_path):
    m, state = srv
    book = tmp_path / "new book.epub"
    book.write_bytes(b"test ebook")
    m.add_book(str(book))
    m.add_format(7, str(book), library="calibre_pdb")
    assert any(s[0] == "ADD" for s in state.seen)
    assert ("UPLOAD", "calibre_pdb", 7, "epub", b"test ebook") in state.seen


@pytest.mark.parametrize("disposition,expected", [
    ("attachment; filename=fallback.epub; filename*=UTF-8''M%C3%A1j.epub", "Máj.epub"),
    ('attachment; filename="../../book.epub"', "book.epub"),
    ('attachment; filename=".."', "safe.epub"),
])
def test_download_filename(srv, disposition, expected):
    m, _ = srv
    response = SimpleNamespace(headers={"Content-Disposition": disposition})
    assert m._filename_from(response, "safe.epub") == expected


def test_smoke_tool_error_exits_nonzero(monkeypatch):
    import sys
    src = Path(__file__).resolve().parents[1] / "scripts/smoke.py"
    prefix = src.read_text().split('info = rpc("initialize"')[0]
    monkeypatch.setattr(sys, "argv", [str(src)])
    ns = {}
    exec(compile(prefix, str(src), "exec"), ns)
    ns["rpc"] = lambda *a: {"isError": True, "content": [{"text": "failed"}]}
    with pytest.raises(SystemExit, match="failed") as error:
        ns["call"]("list_libraries")
    assert error.value.code != 0
