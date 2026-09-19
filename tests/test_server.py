"""Smoke tests for calibre_mcp against a mock Content Server."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mock_calibre  # noqa: E402


@pytest.fixture()
def srv(monkeypatch):
    url, state, stop = mock_calibre.start()
    monkeypatch.setenv("CALIBRE_URL", url)
    monkeypatch.delenv("CALIBRE_LIBRARY_ID", raising=False)

    sys.modules.pop("calibre_mcp", None)
    import calibre_mcp
    calibre_mcp._session = None
    calibre_mcp._lib_cache = None
    calibre_mcp.BASE = url
    calibre_mcp.LIB = ""
    calibre_mcp.READONLY = False

    yield calibre_mcp, state
    stop()


# ------------------------------------------------------------ libraries

def test_lists_both_libraries(srv):
    m, _ = srv
    out = json.loads(m.list_libraries())
    assert set(out["libraries"]) == {"Calibre_Library", "calibre_pdb"}
    assert out["server_default"] == "Calibre_Library"


def test_search_defaults_to_default_library(srv):
    m, _ = srv
    assert [b["title"] for b in json.loads(m.search_books())["books"]] == ["Main Book"]


def test_search_routes_to_named_library(srv):
    m, _ = srv
    out = json.loads(m.search_books(library="calibre_pdb"))
    assert out["library"] == "calibre_pdb"
    assert [b["title"] for b in out["books"]] == ["PDB Book"]


def test_search_all_libraries_covers_both(srv):
    m, _ = srv
    assert set(json.loads(m.search_all_libraries("x"))) == {
        "Calibre_Library", "calibre_pdb"}


def test_unknown_library_lists_valid_ones(srv):
    m, _ = srv
    with pytest.raises(m.CalibreError) as e:
        m.search_books(library="nope")
    assert "Calibre_Library" in str(e.value) and "calibre_pdb" in str(e.value)


def test_download_hits_the_right_library(srv, tmp_path):
    m, _ = srv
    res = json.loads(m.download_book(7, str(tmp_path), "pdf", library="calibre_pdb"))
    assert Path(res["file"]).read_bytes() == b"FAKE:calibre_pdb:pdf:7"


# --------------------------------------------------------------- edits

def test_set_metadata_reaches_the_named_library(srv):
    m, state = srv
    m.set_metadata(7, {"tags": ["manual"]}, library="calibre_pdb")
    edits = [s for s in state.seen if s[0] == "EDIT"]
    assert edits and edits[-1][1] == "calibre_pdb"


def test_copy_to_library_sends_source_and_target(srv):
    m, state = srv
    m.copy_to_library([7], "Calibre_Library", library="calibre_pdb")
    assert [s for s in state.seen if s[0] == "COPY"][-1][1:4] == (
        "calibre_pdb", "Calibre_Library", [7])


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


# ---------------------------------------------- server-side conversion

def test_conversion_options_lists_formats(srv):
    m, _ = srv
    out = json.loads(m.conversion_options(7, library="calibre_pdb"))
    assert out["input_formats"] == ["PDF"]
    assert "epub" in out["output_formats"]


def test_convert_runs_on_the_server_and_polls(srv):
    m, state = srv
    state.polls_before_done = 2          # force at least one "running" poll
    out = json.loads(m.convert_book(7, "epub", library="calibre_pdb"))
    assert out["ok"] and out["new_format"] == "epub"
    assert out["bytes"] == 4242
    started = [s for s in state.seen if s[0] == "CONV_START"]
    assert started[-1][1:5] == ("calibre_pdb", 7, "pdf", "epub")
    # the server adds the format itself
    assert "EPUB" in state.db["calibre_pdb"][7]["formats"]
    assert not [s for s in state.seen if s[0] == "UPLOAD"]


def test_convert_passes_options_through(srv):
    m, state = srv
    m.convert_book(1, "mobi", options={"pretty_print": True})
    assert [s for s in state.seen if s[0] == "CONV_START"][-1][5] == {
        "pretty_print": True}


def test_convert_without_wait_returns_job_id(srv):
    m, _ = srv
    out = json.loads(m.convert_book(1, "mobi", wait=False))
    assert isinstance(out["job_id"], int) and out["status"] == "started"


def test_job_status_can_abort(srv):
    m, state = srv
    job = json.loads(m.convert_book(1, "mobi", wait=False))["job_id"]
    out = json.loads(m.conversion_job_status(job, abort=True))
    assert out["was_aborted"] is True
    assert [s for s in state.seen if s[0] == "CONV_ABORT"]


def test_convert_refuses_unsupported_source(srv):
    m, _ = srv
    with pytest.raises(m.CalibreError) as e:
        m.convert_book(7, "epub", from_format="djvu", library="calibre_pdb")
    assert "djvu" in str(e.value)


def test_convert_refuses_unsupported_target(srv):
    m, _ = srv
    with pytest.raises(m.CalibreError) as e:
        m.convert_book(1, "cbz")
    assert "cbz" in str(e.value)


def test_readonly_blocks_conversion(srv):
    m, _ = srv
    m.READONLY = True
    try:
        with pytest.raises(m.CalibreError):
            m.convert_book(1, "mobi")
    finally:
        m.READONLY = False


# ----------------------------------------------- all libraries at once

def test_search_with_library_all_covers_every_library(srv):
    m, _ = srv
    out = json.loads(m.search_books(query="x", library="all"))
    assert set(out) == {"Calibre_Library", "calibre_pdb"}


def test_libraries_overview_totals_across_libraries(srv):
    m, _ = srv
    out = json.loads(m.libraries_overview())
    assert set(out["libraries"]) == {"Calibre_Library", "calibre_pdb"}
    # one book in each mock library
    assert out["all_libraries"]["total_books"] == 2
    # Main Book has EPUB+PDF, PDB Book has PDF -> pdf appears twice
    assert out["all_libraries"]["formats"]["pdf"] == 2


def test_library_stats_all_delegates_to_overview(srv):
    m, _ = srv
    out = json.loads(m.library_stats(library="all"))
    assert "all_libraries" in out


def test_all_is_refused_where_a_single_book_is_meant(srv):
    m, _ = srv
    with pytest.raises(m.CalibreError) as e:
        m.get_book(1, library="all")
    assert "one book" in str(e.value)


# --------------------------------------------------------- saved searches

def test_create_and_list_saved_search(srv):
    m, state = srv
    m.create_saved_search("School reading", "tags:school")
    assert [s for s in state.seen if s[0] == "SS_ADD"][-1][2:4] == (
        "School reading", "tags:school")
    assert json.loads(m.list_saved_searches())["School reading"] == "tags:school"


def test_saved_search_needs_name_and_expression(srv):
    m, _ = srv
    with pytest.raises(m.CalibreError):
        m.create_saved_search("", "tags:school")


def test_remove_saved_search(srv):
    m, state = srv
    m.create_saved_search("tmp", "tags:x")
    m.remove_saved_search("tmp")
    assert "tmp" not in json.loads(m.list_saved_searches())


def test_readonly_blocks_saved_search(srv):
    m, _ = srv
    m.READONLY = True
    try:
        with pytest.raises(m.CalibreError):
            m.create_saved_search("x", "tags:y")
    finally:
        m.READONLY = False
