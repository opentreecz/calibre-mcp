#!/usr/bin/env python3
"""Calibre MCP server — talks to a running calibre Content Server over HTTP.

It does not drive calibredb against the library directory, so the calibre
GUI can stay open and the library can live on a different machine.

Multiple libraries are supported: every tool takes a `library` argument.
Empty means CALIBRE_LIBRARY_ID, and failing that the server's own default.
`list_libraries` shows what is available.

Conversion runs on the calibre side (the /conversion/* endpoints). The
server queues the job and adds the resulting format to the book itself, so
this process needs no local calibre and no ebook-convert — it is pure HTTP.

Endpoints used (verified against the calibre sources):
  GET  /ajax/library-info
  GET  /ajax/search/{lib}?query=&num=&offset=&sort=&sort_order=
  GET  /ajax/books/{lib}?ids=1,2,3
  GET  /ajax/book/{id}/{lib}
  GET  /get/{fmt}/{id}/{lib}
  POST /cdb/set-fields/{id}/{lib}                  {"changes": {...}}
  POST /cdb/add-book/{job}/{dup}/{filename}/{lib}  (body = raw file)
  POST /cdb/copy-to-library/{target}/{lib}
  GET  /conversion/book-data/{id}?library_id=      formats and options
  POST /conversion/start/{id}?library_id=          queues a job, returns id
  GET  /conversion/status/{job}?library_id=        progress; on success the
                                                   server adds the format

Note that /conversion/* takes the library as a query parameter, while
/ajax/* and /cdb/* take it as a path segment.

Environment:
  CALIBRE_URL        base URL, e.g. http://localhost:8080   (required)
  CALIBRE_LIBRARY_ID default library; empty = the server's default
  CALIBRE_USER       content server account
  CALIBRE_PASSWORD   its password
  CALIBRE_AUTH       digest (default) | basic | none
  CALIBRE_READONLY   "1" disables every write tool
  CALIBRE_TIMEOUT    HTTP timeout in seconds (default 120)
  CALIBRE_VERIFY_TLS "0" skips certificate checks (self-signed on a LAN)
  MCP_TRANSPORT      stdio (default) | streamable-http | sse
  MCP_HOST/PORT/PATH bind settings for the HTTP transports
"""
import base64
import json
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

# mcp 1.x had FastMCP; 2.x renamed it to MCPServer. Support both.
try:
    from mcp.server.fastmcp import FastMCP as _MCPServer  # mcp < 2
except ModuleNotFoundError:  # pragma: no cover
    from mcp.server.mcpserver import MCPServer as _MCPServer  # mcp >= 2

BASE = os.environ.get("CALIBRE_URL", "").strip().rstrip("/")
LIB = os.environ.get("CALIBRE_LIBRARY_ID", "").strip()
USER = os.environ.get("CALIBRE_USER", "").strip()
PASSWORD = os.environ.get("CALIBRE_PASSWORD", "")
AUTH_MODE = os.environ.get("CALIBRE_AUTH", "digest").strip().lower()
READONLY = os.environ.get("CALIBRE_READONLY", "") == "1"
TIMEOUT = int(os.environ.get("CALIBRE_TIMEOUT", "120"))
VERIFY = os.environ.get("CALIBRE_VERIFY_TLS", "1") != "0"

mcp = _MCPServer("calibre-api")

# The SDK only forwards the message of an *anticipated* failure, i.e. a
# ToolError. An ordinary exception is masked as "Error executing tool
# <name>" and the reason never reaches the caller, so derive from ToolError.
try:
    from mcp.server.mcpserver.exceptions import ToolError as _ToolError  # mcp >= 2
except ModuleNotFoundError:  # pragma: no cover
    try:
        from mcp.server.fastmcp.exceptions import ToolError as _ToolError  # mcp < 2
    except ModuleNotFoundError:
        _ToolError = RuntimeError


class CalibreError(_ToolError):
    pass


# --------------------------------------------------------------- plumbing

def _auth():
    if not USER or AUTH_MODE == "none":
        return None
    if AUTH_MODE == "basic":
        return HTTPBasicAuth(USER, PASSWORD)
    return HTTPDigestAuth(USER, PASSWORD)


_session = None


def _sess() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        s.auth = _auth()
        s.verify = VERIFY
        s.headers.update({"Accept": "application/json"})
        _session = s
    return _session


def _url(path: str) -> str:
    if not BASE:
        raise CalibreError("CALIBRE_URL is not set.")
    return BASE + path


def _req(method: str, path: str, **kw):
    try:
        r = _sess().request(method, _url(path), timeout=TIMEOUT, **kw)
    except requests.RequestException as e:
        raise CalibreError("Connection failed: %s" % e) from e
    if r.status_code == 401:
        raise CalibreError(
            "401 — the server wants credentials. Check CALIBRE_USER, "
            "CALIBRE_PASSWORD and CALIBRE_AUTH (digest vs basic).")
    if r.status_code == 403:
        raise CalibreError(
            "403 — this account may not write to the library. Give the user "
            "write permission in the content server's user manager. Note "
            "that 'allow unauthenticated local connections to make changes' "
            "does not cover a container: it arrives from the Docker bridge "
            "network, which calibre does not treat as local.")
    if r.status_code == 404:
        raise CalibreError(
            "404 — no such path or library. Check the id with "
            "list_libraries. (%s)" % path)
    if r.status_code >= 400:
        raise CalibreError("HTTP %s: %s" % (r.status_code, r.text[:400]))
    return r


def _json(method: str, path: str, **kw):
    r = _req(method, path, **kw)
    try:
        return r.json()
    except ValueError as e:
        raise CalibreError("Server did not return JSON: %s" % r.text[:300]) from e


# ------------------------------------------------------- library selection

_lib_cache = None


def _known_libraries() -> dict:
    """{library_id: display name}, fetched once and kept."""
    global _lib_cache
    if _lib_cache is None:
        info = _json("GET", "/ajax/library-info")
        _lib_cache = info.get("library_map") or {}
    return _lib_cache


ALL_LIBRARIES = ("all", "*")


def _is_all(library: str) -> bool:
    return (library or "").strip().lower() in ALL_LIBRARIES


def _every_library() -> list:
    """Every library id on the server, in a stable order."""
    return sorted(_known_libraries()) or [""]


def _resolve(library: str = "") -> str:
    """Library for this call: argument > environment > server default."""
    if _is_all(library):
        raise CalibreError(
            "library='all' only works with search_books, library_stats and "
            "libraries_overview. Everything else acts on one book, which "
            "lives in exactly one library — name it explicitly.")
    lib = (library or LIB or "").strip()
    if not lib:
        return ""
    known = _known_libraries()
    if known and lib not in known:
        raise CalibreError(
            "No library %r on this server. Available: %s"
            % (lib, ", ".join(sorted(known)) or "(none)"))
    return lib


def _libseg(library: str = "") -> str:
    """Library as a trailing path segment; empty = the server's default."""
    lib = _resolve(library)
    return ("/" + quote(lib, safe="")) if lib else ""


def _libq(library: str = "") -> dict:
    """Library as a query parameter — how /conversion/* expects it."""
    lib = _resolve(library)
    return {"library_id": lib} if lib else {}


def _guard_write() -> None:
    if READONLY:
        raise CalibreError("Running read-only (CALIBRE_READONLY=1).")


def _slim(meta: dict) -> dict:
    """Trim a book record so responses stay readable."""
    return {
        "id": meta.get("application_id") or meta.get("id"),
        "title": meta.get("title"),
        "authors": meta.get("authors"),
        "series": meta.get("series"),
        "series_index": meta.get("series_index"),
        "tags": meta.get("tags"),
        "formats": meta.get("formats"),
        "pubdate": meta.get("pubdate"),
        "rating": meta.get("rating"),
        "languages": meta.get("languages"),
    }


# ----------------------------------------------------------------- reading

@mcp.tool()
def list_libraries() -> str:
    """List the libraries this server offers and which one is the default.

    Use an id from here as the `library` argument of the other tools."""
    global _lib_cache
    info = _json("GET", "/ajax/library-info")
    _lib_cache = info.get("library_map") or {}
    return json.dumps(
        {"libraries": _lib_cache,
         "server_default": info.get("default_library"),
         "default_for_this_mcp": LIB or info.get("default_library")},
        ensure_ascii=False, indent=2)


@mcp.tool()
def search_books(query: str = "", limit: int = 25, offset: int = 0,
                 sort: str = "timestamp", sort_order: str = "desc",
                 library: str = "") -> str:
    """Search one library using calibre query syntax and return metadata.

    Examples: 'author:Capek', 'title:Maj', 'tags:school', 'format:epub',
    'author:Hrabal and format:epub'. An empty query returns the whole
    library up to `limit`.

    Set `library` to "all" to search every library at once."""
    if _is_all(library):
        return search_all_libraries(query=query, limit_per_library=limit)
    seg = _libseg(library)
    params = {"num": max(0, int(limit)), "offset": max(0, int(offset)),
              "sort": sort, "sort_order": sort_order}
    if query:
        params["query"] = query
    res = _json("GET", "/ajax/search" + seg, params=params)
    ids = res.get("book_ids") or []
    out = {"library": _resolve(library) or "(default)",
           "total": res.get("total_num"), "returned": len(ids), "books": []}
    if ids:
        meta = _json("GET", "/ajax/books" + seg,
                     params={"ids": ",".join(str(i) for i in ids)})
        out["books"] = [_slim(m) for m in meta.values() if m]
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def search_all_libraries(query: str, limit_per_library: int = 10) -> str:
    """Search every library on the server at once.

    Useful when you do not know which library a book is in."""
    libs = sorted(_known_libraries()) or [""]
    out = {}
    for lib_id in libs:
        try:
            res = json.loads(search_books(query=query,
                                          limit=limit_per_library,
                                          library=lib_id))
            out[lib_id or "(default)"] = {"total": res.get("total"),
                                          "books": res.get("books")}
        except CalibreError as e:
            out[lib_id or "(default)"] = {"error": str(e)}
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def get_book(book_id: int, library: str = "") -> str:
    """Full metadata for one book, including the list of formats."""
    meta = _json("GET", "/ajax/book/%d%s" % (int(book_id), _libseg(library)))
    return json.dumps(meta, ensure_ascii=False, indent=2)


@mcp.tool()
def library_stats(max_books: int = 5000, library: str = "") -> str:
    """Book count and a breakdown by format. Lower `max_books` on a big
    library — metadata is fetched for every book counted.

    Set `library` to "all" for every library plus a combined total."""
    if _is_all(library):
        return libraries_overview(max_books=max_books)
    seg = _libseg(library)
    res = _json("GET", "/ajax/search" + seg,
                params={"num": max(1, int(max_books)), "offset": 0})
    ids = res.get("book_ids") or []
    counts = {}
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        meta = _json("GET", "/ajax/books" + seg,
                     params={"ids": ",".join(str(x) for x in chunk)})
        for m in meta.values():
            for f in (m or {}).get("formats") or []:
                f = str(f).lower()
                counts[f] = counts.get(f, 0) + 1
    return json.dumps(
        {"library": _resolve(library) or "(default)",
         "total_books": res.get("total_num"),
         "counted": len(ids),
         "formats": dict(sorted(counts.items()))},
        ensure_ascii=False, indent=2)


@mcp.tool()
def libraries_overview(max_books: int = 5000) -> str:
    """Book counts and format breakdowns for **every** library, plus a
    combined total. One call to see the whole calibre installation."""
    per = {}
    combined = {}
    grand = 0
    for lib_id in _every_library():
        try:
            one = json.loads(library_stats(max_books=max_books,
                                           library=lib_id))
        except CalibreError as e:
            per[lib_id or "(default)"] = {"error": str(e)}
            continue
        per[lib_id or "(default)"] = {"total_books": one.get("total_books"),
                                      "formats": one.get("formats")}
        grand += one.get("total_books") or 0
        for fmt, n in (one.get("formats") or {}).items():
            combined[fmt] = combined.get(fmt, 0) + n
    return json.dumps({"libraries": per,
                       "all_libraries": {"total_books": grand,
                                         "formats": dict(sorted(combined.items()))}},
                      ensure_ascii=False, indent=2)


@mcp.tool()
def download_book(book_id: int, to_dir: str, fmt: str = "",
                  library: str = "") -> str:
    """Download a book file into `to_dir`.

    `fmt` is e.g. 'epub'; empty picks the first format the book has."""
    fmt = _pick_format(int(book_id), fmt, library)
    dest = Path(to_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    r = _req("GET", "/get/%s/%d%s" % (quote(fmt), int(book_id),
                                      _libseg(library)), stream=True)
    name = _filename_from(r, "book-%d.%s" % (int(book_id), fmt.lower()))
    target = dest / name
    with open(target, "wb") as fh:
        for chunk in r.iter_content(65536):
            fh.write(chunk)
    return json.dumps({"ok": True, "file": str(target),
                       "bytes": target.stat().st_size, "format": fmt,
                       "library": _resolve(library) or "(default)"},
                      ensure_ascii=False)


def _pick_format(book_id: int, fmt: str, library: str = "") -> str:
    meta = _json("GET", "/ajax/book/%d%s" % (book_id, _libseg(library)))
    formats = [str(f).lower() for f in (meta.get("formats") or [])]
    if not formats:
        raise CalibreError("Book %d has no formats." % book_id)
    if fmt:
        f = fmt.lower().lstrip(".")
        if f not in formats:
            raise CalibreError("Book %d has no %s. It has: %s"
                               % (book_id, f, ", ".join(formats)))
        return f
    for pref in ("epub", "azw3", "mobi", "pdf"):
        if pref in formats:
            return pref
    return formats[0]


def _filename_from(resp, fallback: str) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    name = m.group(1) if m else fallback
    return Path(name).name or fallback


# ----------------------------------------------------------------- editing

@mcp.tool()
def set_metadata(book_id: int, fields: dict, library: str = "") -> str:
    """Change a book's metadata (POST /cdb/set-fields).

    `fields` is a mapping, e.g.
    {"title": "Maj", "authors": ["Karel Hynek Macha"],
     "tags": ["school", "poetry"], "series": "Reading list", "rating": 8}.
    Authors, tags and languages are lists. Custom columns keep their hash,
    e.g. {"#status": "read"}."""
    _guard_write()
    if not fields:
        raise CalibreError("No fields given.")
    for bad in ("added_formats", "removed_formats", "cover"):
        if bad in fields:
            raise CalibreError(
                "%r does not belong here — use add_format or convert_book."
                % bad)
    body = {"changes": fields, "loaded_book_ids": [], "all_dirtied": False}
    res = _json("POST",
                "/cdb/set-fields/%d%s" % (int(book_id), _libseg(library)),
                json=body)
    return json.dumps({"ok": True, "id": book_id,
                       "library": _resolve(library) or "(default)",
                       "changed": list(fields), "response": res},
                      ensure_ascii=False, indent=2)


@mcp.tool()
def add_book(path: str, duplicates: bool = False, library: str = "") -> str:
    """Upload a file as a new book (POST /cdb/add-book)."""
    _guard_write()
    src = Path(path).expanduser()
    if not src.is_file():
        raise CalibreError("Not a file: %s" % src)
    job = uuid.uuid4().hex
    ctype = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
    with open(src, "rb") as fh:
        res = _json(
            "POST",
            "/cdb/add-book/%s/%s/%s%s" % (
                job, "y" if duplicates else "n", quote(src.name, safe=""),
                _libseg(library)),
            data=fh, headers={"Content-Type": ctype})
    return json.dumps({"ok": True,
                       "library": _resolve(library) or "(default)",
                       "result": res}, ensure_ascii=False, indent=2)


@mcp.tool()
def add_format(book_id: int, path: str, library: str = "") -> str:
    """Attach another format to an existing book. Other formats stay."""
    _guard_write()
    src = Path(path).expanduser()
    if not src.is_file():
        raise CalibreError("Not a file: %s" % src)
    ext = src.suffix.lstrip(".").lower()
    if not ext:
        raise CalibreError("The file has no extension, so its format is "
                           "unknown.")
    return _upload_format(int(book_id), ext, src.read_bytes(), src.name,
                          library)


def _upload_format(book_id: int, ext: str, data: bytes, label: str,
                   library: str = "") -> str:
    """Formats have no endpoint of their own; set-fields takes them as a
    base64 data URL under the special `added_formats` key."""
    payload = {
        "changes": {
            "added_formats": [{
                "ext": ext,
                "data_url": "data:application/octet-stream;base64,"
                            + base64.b64encode(data).decode("ascii"),
            }]
        },
        "loaded_book_ids": [],
        "all_dirtied": False,
    }
    _json("POST", "/cdb/set-fields/%d%s" % (book_id, _libseg(library)),
          json=payload)
    return json.dumps({"ok": True, "id": book_id, "format": ext,
                       "library": _resolve(library) or "(default)",
                       "file": label, "bytes": len(data)},
                      ensure_ascii=False)


@mcp.tool()
def copy_to_library(book_ids: list, target_library: str,
                    move: bool = False, library: str = "") -> str:
    """Copy (or move) books between two libraries on the server.

    `library` is the source, `target_library` the destination.
    `move=True` removes them from the source — use with care."""
    _guard_write()
    if not book_ids:
        raise CalibreError("No books given.")
    target = _resolve(target_library)
    if not target:
        raise CalibreError("A target library is required.")
    if target == (_resolve(library) or ""):
        raise CalibreError("Source and target are the same library.")
    body = {"book_ids": [int(b) for b in book_ids], "move": bool(move),
            "preserve_date": True, "duplicate_action": "add",
            "automerge_action": "overwrite"}
    res = _json("POST", "/cdb/copy-to-library/%s%s"
                % (quote(target, safe=""), _libseg(library)), json=body)
    return json.dumps({"ok": True, "from": _resolve(library) or "(default)",
                       "to": target, "move": bool(move), "result": res},
                      ensure_ascii=False, indent=2)


# -------------------------------------------------------------- conversion

@mcp.tool()
def conversion_options(book_id: int, output_format: str = "",
                       library: str = "") -> str:
    """Which formats a book can be converted from and to, and which options
    calibre offers for that pair.

    Worth calling before `convert_book` if you are unsure what is on offer."""
    params = _libq(library)
    if output_format:
        params["output_fmt"] = output_format.lower().lstrip(".")
    data = _json("GET", "/conversion/book-data/%d" % int(book_id),
                 params=params)
    opts = data.get("conversion_options") or {}
    return json.dumps(
        {"book": {"id": data.get("book_id"), "title": data.get("title"),
                  "authors": data.get("authors")},
         "input_formats": data.get("input_formats"),
         "output_formats": data.get("output_formats"),
         "profiles": list((data.get("profiles") or {}).keys())
                     if isinstance(data.get("profiles"), dict)
                     else data.get("profiles"),
         "options": sorted(opts.get("options", {})
                           if isinstance(opts, dict) else []) or opts},
        ensure_ascii=False, indent=2)


@mcp.tool()
def convert_book(book_id: int, to_format: str, from_format: str = "",
                 options: dict = None, wait: bool = True,
                 timeout_s: int = 900, library: str = "") -> str:
    """Convert a book **on the calibre server**.

    The server queues the job and, once it finishes, adds the new format to
    the book itself — nothing is uploaded back from here, and no local
    ebook-convert is needed.

    `options` are calibre conversion options; `conversion_options` lists the
    accepted ones. With `wait=False` the job is only started and its id
    returned, to be polled with `conversion_job_status`."""
    _guard_write()
    to_fmt = to_format.lower().lstrip(".")
    if not to_fmt.isalnum():
        raise CalibreError("Bad format: %s" % to_format)

    info = _json("GET", "/conversion/book-data/%d" % int(book_id),
                 params=_libq(library))
    inputs = [str(x).lower() for x in (info.get("input_formats") or [])]
    outputs = [str(x).lower() for x in (info.get("output_formats") or [])]
    if not inputs:
        raise CalibreError("Book %d has no format to convert from." % book_id)
    src = (from_format or inputs[0]).lower().lstrip(".")
    if src not in inputs:
        raise CalibreError("Cannot convert from %s. The book offers: %s"
                           % (src, ", ".join(inputs)))
    if outputs and to_fmt not in outputs:
        raise CalibreError("calibre does not convert to %s. It offers: %s"
                           % (to_fmt, ", ".join(outputs)))

    body = {"input_fmt": src, "output_fmt": to_fmt, "options": options or {}}
    job_id = _json("POST", "/conversion/start/%d" % int(book_id),
                   params=_libq(library), json=body)
    if not isinstance(job_id, int):
        raise CalibreError("The server did not return a job id: %r" % (job_id,))

    if not wait:
        return json.dumps({"ok": True, "job_id": job_id, "status": "started",
                           "from": src, "to": to_fmt,
                           "library": _resolve(library) or "(default)"},
                          ensure_ascii=False)

    deadline = time.monotonic() + max(10, int(timeout_s))
    delay = 1.0
    while True:
        st = _json("GET", "/conversion/status/%d" % job_id,
                   params=_libq(library))
        if not st.get("running"):
            if st.get("ok"):
                return json.dumps(
                    {"ok": True, "id": book_id, "from": src,
                     "new_format": st.get("fmt", to_fmt),
                     "bytes": st.get("size"),
                     "library": _resolve(library) or "(default)",
                     "note": "The calibre server added the format itself."},
                    ensure_ascii=False, indent=2)
            raise CalibreError(
                "Conversion failed%s. %s"
                % (" (aborted)" if st.get("was_aborted") else "",
                   (st.get("traceback") or st.get("log") or "")[-800:]))
        if time.monotonic() > deadline:
            raise CalibreError(
                "Still running after %ss (job_id %d). The job continues on "
                "the server — poll it with conversion_job_status."
                % (timeout_s, job_id))
        time.sleep(delay)
        delay = min(delay * 1.5, 5.0)


@mcp.tool()
def conversion_job_status(job_id: int, abort: bool = False,
                          library: str = "") -> str:
    """Poll a job started with `wait=False`, or abort it with `abort=True`."""
    params = _libq(library)
    if abort:
        params["abort_job"] = "1"
    st = _json("GET", "/conversion/status/%d" % int(job_id), params=params)
    return json.dumps(st, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ startup

def main() -> None:
    """stdio by default, HTTP on request. A container wants
    streamable-http: stdio requires the MCP client to spawn the process,
    which a long-running service cannot offer."""
    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    if transport == "stdio":
        mcp.run()
        return
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8765"))
    path = os.environ.get("MCP_PATH", "/mcp")
    try:
        mcp.run(transport=transport, host=host, port=port,
                streamable_http_path=path)
    except TypeError:
        # mcp 1.x reads host/port from the instance, not from run()
        for attr, val in (("host", host), ("port", port)):
            try:
                setattr(mcp.settings, attr, val)
            except Exception:
                pass
        mcp.run(transport=transport)


if __name__ == "__main__":
    main()
