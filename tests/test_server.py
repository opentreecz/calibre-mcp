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


# ------------------------------------------- tags must be merged, not lost

def test_set_metadata_replaces_the_tag_list(srv):
    """calibre's set-fields REPLACES the list — this is why any caller
    adding a tag has to merge client-side. Pinning the behaviour here so
    the reading-list script's merge is not mistaken for belt and braces."""
    m, state = srv
    assert state.db["Calibre_Library"][1]["tags"] == ["test"]
    m.set_metadata(1, {"tags": ["maturita"]})
    assert state.db["Calibre_Library"][1]["tags"] == ["maturita"]


def test_merging_keeps_existing_tags(srv):
    """What the script actually does: read, append, write back."""
    m, state = srv
    before = json.loads(m.get_book(1))["tags"]
    assert "test" in before
    m.set_metadata(1, {"tags": list(before) + ["maturita"]})
    after = json.loads(m.get_book(1))["tags"]
    assert after == ["test", "maturita"]
    assert "test" in after, "pre-existing tag was lost"


def test_reading_list_script_merges_rather_than_overwrites():
    """Read the script and assert it appends to the existing list instead
    of assigning a bare [TAG]."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "scripts" / "reading_list.py"
    text = src.read_text(encoding="utf-8")
    assert 'tags = list(b.get("tags") or [])' in text
    assert "tags.append(TAG)" in text
    assert 'fields={"tags": tags}' in text
    assert 'fields={"tags": [TAG]}' not in text


def _reading_list_prefix():
    """Exec the pure top half of scripts/reading_list.py (everything above
    the first network call) so its helpers can be tested on their own."""
    import sys
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "scripts" / "reading_list.py"
    text = src.read_text(encoding="utf-8")
    head = text.split('rpc("initialize"')[0]
    ns = {"__name__": "reading_list_prefix"}
    argv = sys.argv
    sys.argv = ["reading_list.py"]          # the prefix parses sys.argv
    try:
        exec(compile(head, str(src), "exec"), ns)
    finally:
        sys.argv = argv
    return ns


def test_pair_by_author_trusts_a_hand_added_tag():
    """Golet v udoli sits in calibre under a title the matcher cannot see,
    but it carries the tag — one Olbracht book, one missing Olbracht work,
    so it counts as present."""
    ns = _reading_list_prefix()
    missing = [(55, "Golet v údolí", "olbracht", [])]
    stray = [{"id": 321, "title": "Hory a staletí", "authors": ["Ivan Olbracht"]}]
    paired, rest, leftover = ns["pair_by_author"](missing, stray)
    assert [(p[0], p[2]["id"]) for p in paired] == [(55, 321)]
    assert rest == [] and leftover == []


def test_pair_by_author_refuses_to_guess_between_two_candidates():
    ns = _reading_list_prefix()
    missing = [(55, "Golet v údolí", "olbracht", [])]
    stray = [{"id": 1, "title": "A", "authors": ["Ivan Olbracht"]},
             {"id": 2, "title": "B", "authors": ["Ivan Olbracht"]}]
    paired, rest, leftover = ns["pair_by_author"](missing, stray)
    assert paired == []
    assert [m[0] for m in rest] == [55]
    assert len(leftover) == 2


def test_pair_by_author_ignores_a_different_author():
    ns = _reading_list_prefix()
    missing = [(55, "Golet v údolí", "olbracht", [])]
    stray = [{"id": 9, "title": "Saturnin", "authors": ["Zdeněk Jirotka"]}]
    paired, rest, leftover = ns["pair_by_author"](missing, stray)
    assert paired == []
    assert [m[0] for m in rest] == [55]
    assert [b["id"] for b in leftover] == [9]


def test_pair_by_author_does_not_mutate_its_input():
    ns = _reading_list_prefix()
    stray = [{"id": 321, "title": "Hory a staletí", "authors": ["Ivan Olbracht"]}]
    ns["pair_by_author"]([(55, "Golet v údolí", "olbracht", [])], stray)
    assert len(stray) == 1
