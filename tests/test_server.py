"""Smoke tests for calibre_mcp against a mock Content Server."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mock_calibre  # noqa: E402


@pytest.fixture()
def srv(monkeypatch, tmp_path):
    url, state, stop = mock_calibre.start()

    # a stand-in converter: marks the file so we can prove bytes round-trip
    conv = tmp_path / "fake-ebook-convert"
    conv.write_text('#!/bin/sh\nprintf "CONVERTED:" > "$2"; cat "$1" >> "$2"\n')
    conv.chmod(0o755)

    monkeypatch.setenv("CALIBRE_URL", url)
    monkeypatch.delenv("CALIBRE_LIBRARY_ID", raising=False)
    monkeypatch.setenv("EBOOK_CONVERT", str(conv))

    for mod in ("calibre_mcp",):
        sys.modules.pop(mod, None)
    import calibre_mcp
    calibre_mcp._session = None
    calibre_mcp._lib_cache = None
    calibre_mcp.BASE = url
    calibre_mcp.LIB = ""
    calibre_mcp.EBOOK_CONVERT = str(conv)
    calibre_mcp.READONLY = False

    yield calibre_mcp, state
    stop()


def test_lists_both_libraries(srv):
    m, _ = srv
    out = json.loads(m.list_libraries())
    assert set(out["knihovny"]) == {"Calibre_Library", "calibre_pdb"}
    assert out["vychozi_na_serveru"] == "Calibre_Library"


def test_search_defaults_to_default_library(srv):
    m, _ = srv
    out = json.loads(m.search_books())
    assert [b["title"] for b in out["knihy"]] == ["Main Book"]


def test_search_routes_to_named_library(srv):
    m, _ = srv
    out = json.loads(m.search_books(library="calibre_pdb"))
    assert out["knihovna"] == "calibre_pdb"
    assert [b["title"] for b in out["knihy"]] == ["PDB Book"]


def test_search_all_libraries_covers_both(srv):
    m, _ = srv
    out = json.loads(m.search_all_libraries("anything"))
    assert set(out) == {"Calibre_Library", "calibre_pdb"}


def test_unknown_library_lists_valid_ones(srv):
    m, _ = srv
    with pytest.raises(m.CalibreError) as e:
        m.search_books(library="nope")
    assert "Calibre_Library" in str(e.value) and "calibre_pdb" in str(e.value)


def test_download_hits_the_right_library(srv, tmp_path):
    m, _ = srv
    res = json.loads(m.download_book(7, str(tmp_path), "pdf",
                                     library="calibre_pdb"))
    # the mock encodes the library it served from into the payload
    assert Path(res["soubor"]).read_bytes() == b"FAKE:calibre_pdb:pdf:7"


def test_set_metadata_reaches_the_named_library(srv):
    m, state = srv
    m.set_metadata(7, {"tags": ["manual"]}, library="calibre_pdb")
    edits = [s for s in state.seen if s[0] == "EDIT"]
    assert edits and edits[-1][1] == "calibre_pdb"


def test_convert_uploads_result_back(srv):
    m, state = srv
    res = json.loads(m.convert_book(7, "epub", library="calibre_pdb"))
    assert res["format"] == "epub"
    uploads = [s for s in state.seen if s[0] == "UPLOAD"]
    assert uploads, "no format was uploaded"
    lib, bid, ext, blob = uploads[-1][1:]
    assert (lib, bid, ext) == ("calibre_pdb", 7, "epub")
    assert blob == b"CONVERTED:FAKE:calibre_pdb:pdf:7"
    assert "EPUB" in state.db["calibre_pdb"][7]["formats"]


def test_convert_refuses_existing_format(srv):
    m, _ = srv
    with pytest.raises(m.CalibreError):
        m.convert_book(1, "epub")


def test_convert_save_to_does_not_touch_library(srv, tmp_path):
    m, state = srv
    m.convert_book(7, "epub", save_to=str(tmp_path), library="calibre_pdb")
    assert not [s for s in state.seen if s[0] == "UPLOAD"]


def test_copy_to_library_sends_source_and_target(srv):
    m, state = srv
    m.copy_to_library([7], "Calibre_Library", library="calibre_pdb")
    copies = [s for s in state.seen if s[0] == "COPY"]
    assert copies[-1][1:4] == ("calibre_pdb", "Calibre_Library", [7])


def test_copy_to_same_library_refused(srv):
    m, _ = srv
    with pytest.raises(m.CalibreError):
        m.copy_to_library([7], "calibre_pdb", library="calibre_pdb")


def test_readonly_blocks_writes(srv):
    m, _ = srv
    m.READONLY = True
    try:
        with pytest.raises(m.CalibreError):
            m.set_metadata(1, {"title": "x"})
    finally:
        m.READONLY = False
