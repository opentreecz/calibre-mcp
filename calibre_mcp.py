#!/usr/bin/env python3
"""
Calibre MCP server — komunikace přes HTTP API Content Serveru.

Nevolá calibredb. Mluví přímo s běžícím Calibre Content Serverem, takže
grafické Calibre může být zapnuté a knihovna může běžet na jiném stroji.

Podporuje víc knihoven najednou: každý nástroj má parametr `library`.
Prázdný = výchozí z CALIBRE_LIBRARY_ID, a když ani ta není nastavená,
výchozí knihovna serveru. `list_libraries` vypíše, co server nabízí.

Použité endpointy (ověřeno proti zdrojovým kódům calibre):
  GET  /ajax/library-info
  GET  /ajax/search/{lib}?query=&num=&offset=&sort=&sort_order=
  GET  /ajax/books/{lib}?ids=1,2,3
  GET  /ajax/book/{id}/{lib}
  GET  /get/{fmt}/{id}/{lib}
  POST /cdb/set-fields/{id}/{lib}      {"changes": {...}}
  POST /cdb/add-book/{job}/{dup}/{filename}/{lib}   (tělo = binární soubor)
  POST /cdb/copy-to-library/{cil}/{lib}

POZOR — převod formátů API neumí. Content Server žádný konverzní endpoint
nemá (conversion je v calibre věc GUI fronty a nástroje ebook-convert).
`convert_book` proto stáhne zdroj přes API, převede ho lokálním
ebook-convert a výsledek nahraje zpět jako nový formát. Na stroji, kde běží
tenhle MCP server, tedy musí být ebook-convert. Všechno ostatní je čisté API.

Konfigurace přes proměnné prostředí:
  CALIBRE_URL        základ, např. http://localhost:8080   (povinné)
  CALIBRE_LIBRARY_ID výchozí id knihovny; prázdné = výchozí knihovna serveru
  CALIBRE_USER       uživatel (volitelné)
  CALIBRE_PASSWORD   heslo (volitelné)
  CALIBRE_AUTH       digest (výchozí) | basic | none
  EBOOK_CONVERT      cesta k ebook-convert (výchozí: z PATH)
  CALIBRE_READONLY   "1" = zakáže zápisové nástroje
  CALIBRE_TIMEOUT    timeout HTTP v sekundách (výchozí 120)
  CALIBRE_VERIFY_TLS "0" = nekontrolovat certifikát (jen pro self-signed v LAN)
"""
import base64
import json
import mimetypes
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

# SDK mcp 1.x mel FastMCP, 2.x ho prejmenoval na MCPServer. Podporujeme obe.
try:
    from mcp.server.fastmcp import FastMCP as _MCPServer  # mcp < 2
except ModuleNotFoundError:  # pragma: no cover
    from mcp.server.mcpserver import MCPServer as _MCPServer  # mcp >= 2

BASE = os.environ.get("CALIBRE_URL", "").strip().rstrip("/")
LIB = os.environ.get("CALIBRE_LIBRARY_ID", "").strip()
USER = os.environ.get("CALIBRE_USER", "").strip()
PASSWORD = os.environ.get("CALIBRE_PASSWORD", "")
AUTH_MODE = os.environ.get("CALIBRE_AUTH", "digest").strip().lower()
EBOOK_CONVERT = os.environ.get("EBOOK_CONVERT", "ebook-convert")
READONLY = os.environ.get("CALIBRE_READONLY", "") == "1"
TIMEOUT = int(os.environ.get("CALIBRE_TIMEOUT", "120"))
VERIFY = os.environ.get("CALIBRE_VERIFY_TLS", "1") != "0"

mcp = _MCPServer("calibre-api")


class CalibreError(RuntimeError):
    pass


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
        raise CalibreError("Není nastavena proměnná CALIBRE_URL.")
    return BASE + path


def _req(method: str, path: str, **kw):
    try:
        r = _sess().request(method, _url(path), timeout=TIMEOUT, **kw)
    except requests.RequestException as e:
        raise CalibreError("Spojení selhalo: %s" % e) from e
    if r.status_code == 401:
        raise CalibreError(
            "401 — server vyžaduje přihlášení. Zkontroluj CALIBRE_USER/"
            "CALIBRE_PASSWORD a CALIBRE_AUTH (digest vs basic)."
        )
    if r.status_code == 403:
        raise CalibreError(
            "403 — uživatel nemá právo zápisu do této knihovny. Ve správě "
            "uživatelů povol zápis, nebo spusť server s --enable-local-write."
        )
    if r.status_code == 404:
        raise CalibreError(
            "404 — neexistující cesta nebo knihovna. Ověř id přes "
            "list_libraries. (%s)" % path)
    if r.status_code >= 400:
        raise CalibreError("HTTP %s: %s" % (r.status_code, r.text[:400]))
    return r


def _json(method: str, path: str, **kw):
    r = _req(method, path, **kw)
    try:
        return r.json()
    except ValueError as e:
        raise CalibreError("Server nevrátil JSON: %s" % r.text[:300]) from e


# ------------------------------------------------------ výběr knihovny

_lib_cache = None


def _known_libraries() -> dict:
    """Mapa {library_id: zobrazovaný název}, načtená jednou a uložená."""
    global _lib_cache
    if _lib_cache is None:
        info = _json("GET", "/ajax/library-info")
        _lib_cache = info.get("library_map") or {}
    return _lib_cache


def _resolve(library: str = "") -> str:
    """Id knihovny pro tohle volání: parametr > env > výchozí serveru."""
    lib = (library or LIB or "").strip()
    if not lib:
        return ""
    known = _known_libraries()
    if known and lib not in known:
        raise CalibreError(
            "Knihovna '%s' na serveru není. Dostupné: %s"
            % (lib, ", ".join(sorted(known)) or "(žádná)"))
    return lib


def _libseg(library: str = "") -> str:
    """Koncový segment cesty s ID knihovny (prázdný = výchozí knihovna)."""
    lib = _resolve(library)
    return ("/" + quote(lib, safe="")) if lib else ""


def _guard_write() -> None:
    if READONLY:
        raise CalibreError("Server běží v režimu jen pro čtení (CALIBRE_READONLY=1).")


def _slim(meta: dict) -> dict:
    """Zmenší záznam knihy na to podstatné, ať odpověď neexploduje."""
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


# ---------------------------------------------------------------- čtení

@mcp.tool()
def list_libraries() -> str:
    """Vypíše knihovny dostupné na serveru a která je výchozí.
    Id odsud se dává do parametru `library` ostatních nástrojů."""
    global _lib_cache
    info = _json("GET", "/ajax/library-info")
    _lib_cache = info.get("library_map") or {}
    return json.dumps(
        {"knihovny": _lib_cache,
         "vychozi_na_serveru": info.get("default_library"),
         "vychozi_pro_tenhle_mcp": LIB or info.get("default_library")},
        ensure_ascii=False, indent=2)


@mcp.tool()
def search_books(query: str = "", limit: int = 25, offset: int = 0,
                 sort: str = "timestamp", sort_order: str = "desc",
                 library: str = "") -> str:
    """Vyhledá knihy v syntaxi Calibre a vrátí jejich metadata.

    Příklady dotazu: 'author:Čapek', 'title:Máj', 'tags:maturita',
    'format:epub', 'author:Hrabal and format:epub'.
    Prázdný dotaz = celá knihovna (do limitu).
    `library` = id knihovny; prázdné = výchozí."""
    seg = _libseg(library)
    params = {"num": max(0, int(limit)), "offset": max(0, int(offset)),
              "sort": sort, "sort_order": sort_order}
    if query:
        params["query"] = query
    res = _json("GET", "/ajax/search" + seg, params=params)
    ids = res.get("book_ids") or []
    out = {"knihovna": _resolve(library) or "(výchozí)",
           "total": res.get("total_num"), "vraceno": len(ids), "knihy": []}
    if ids:
        meta = _json("GET", "/ajax/books" + seg,
                     params={"ids": ",".join(str(i) for i in ids)})
        out["knihy"] = [_slim(m) for m in meta.values() if m]
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def search_all_libraries(query: str, limit_per_library: int = 10) -> str:
    """Prohledá **všechny** knihovny na serveru najednou.
    Hodí se, když nevíš, ve které knihovně kniha je."""
    libs = sorted(_known_libraries()) or [""]
    out = {}
    for lib_id in libs:
        try:
            res = json.loads(
                search_books(query=query, limit=limit_per_library,
                             library=lib_id))
            out[lib_id or "(výchozí)"] = {"total": res.get("total"),
                                          "knihy": res.get("knihy")}
        except CalibreError as e:
            out[lib_id or "(výchozí)"] = {"chyba": str(e)}
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def get_book(book_id: int, library: str = "") -> str:
    """Vrátí kompletní metadata jedné knihy včetně seznamu formátů."""
    meta = _json("GET", "/ajax/book/%d%s" % (int(book_id), _libseg(library)))
    return json.dumps(meta, ensure_ascii=False, indent=2)


@mcp.tool()
def library_stats(max_books: int = 5000, library: str = "") -> str:
    """Počet knih a rozpad podle formátů. U velkých knihoven omez `max_books`."""
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
        {"knihovna": _resolve(library) or "(výchozí)",
         "pocet_knih_celkem": res.get("total_num"),
         "zapocteno": len(ids),
         "formaty": dict(sorted(counts.items()))},
        ensure_ascii=False, indent=2)


@mcp.tool()
def download_book(book_id: int, to_dir: str, fmt: str = "",
                  library: str = "") -> str:
    """Stáhne soubor knihy přes API do adresáře `to_dir`.
    `fmt` např. 'epub'; prázdné = první dostupný formát."""
    fmt = _pick_format(int(book_id), fmt, library)
    dest = Path(to_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    r = _req("GET", "/get/%s/%d%s" % (quote(fmt), int(book_id),
                                      _libseg(library)), stream=True)
    name = _filename_from(r, "kniha-%d.%s" % (int(book_id), fmt.lower()))
    target = dest / name
    with open(target, "wb") as fh:
        for chunk in r.iter_content(65536):
            fh.write(chunk)
    return json.dumps({"ok": True, "soubor": str(target),
                       "bajtu": target.stat().st_size, "format": fmt,
                       "knihovna": _resolve(library) or "(výchozí)"},
                      ensure_ascii=False)


def _pick_format(book_id: int, fmt: str, library: str = "") -> str:
    meta = _json("GET", "/ajax/book/%d%s" % (book_id, _libseg(library)))
    formats = [str(f).lower() for f in (meta.get("formats") or [])]
    if not formats:
        raise CalibreError("Kniha %d nemá žádný formát." % book_id)
    if fmt:
        f = fmt.lower().lstrip(".")
        if f not in formats:
            raise CalibreError("Kniha %d nemá formát %s. Má: %s"
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


# -------------------------------------------------------------- úpravy

@mcp.tool()
def set_metadata(book_id: int, fields: dict, library: str = "") -> str:
    """Upraví metadata knihy přes API (POST /cdb/set-fields).

    `fields` je slovník, např.
    {"title": "Máj", "authors": ["Karel Hynek Mácha"],
     "tags": ["maturita", "poezie"], "series": "Povinná četba", "rating": 8}.
    Autoři, tagy a jazyky se zadávají jako seznam. Vlastní sloupce s mřížkou,
    např. {"#status": "přečteno"}."""
    _guard_write()
    if not fields:
        raise CalibreError("Nebylo zadáno žádné pole.")
    for bad in ("added_formats", "removed_formats", "cover"):
        if bad in fields:
            raise CalibreError(
                "Pole '%s' sem nepatří — použij add_format / convert_book." % bad)
    body = {"changes": fields, "loaded_book_ids": [], "all_dirtied": False}
    res = _json("POST",
                "/cdb/set-fields/%d%s" % (int(book_id), _libseg(library)),
                json=body)
    return json.dumps({"ok": True, "id": book_id,
                       "knihovna": _resolve(library) or "(výchozí)",
                       "zmeneno": list(fields), "odpoved": res},
                      ensure_ascii=False, indent=2)


@mcp.tool()
def add_book(path: str, duplicates: bool = False, library: str = "") -> str:
    """Nahraje soubor do knihovny jako novou knihu (POST /cdb/add-book)."""
    _guard_write()
    src = Path(path).expanduser()
    if not src.is_file():
        raise CalibreError("Není soubor: %s" % src)
    job = uuid.uuid4().hex
    ctype = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
    with open(src, "rb") as fh:
        res = _json(
            "POST",
            "/cdb/add-book/%s/%s/%s%s" % (
                job, "y" if duplicates else "n", quote(src.name, safe=""),
                _libseg(library)),
            data=fh, headers={"Content-Type": ctype})
    return json.dumps({"ok": True, "knihovna": _resolve(library) or "(výchozí)",
                       "vysledek": res}, ensure_ascii=False, indent=2)


@mcp.tool()
def add_format(book_id: int, path: str, library: str = "") -> str:
    """Přidá další formát k existující knize (přes added_formats v set-fields).
    Ostatní formáty zůstanou."""
    _guard_write()
    src = Path(path).expanduser()
    if not src.is_file():
        raise CalibreError("Není soubor: %s" % src)
    ext = src.suffix.lstrip(".").lower()
    if not ext:
        raise CalibreError("Soubor nemá příponu, nevím jaký je to formát.")
    return _upload_format(int(book_id), ext, src.read_bytes(), src.name, library)


def _upload_format(book_id: int, ext: str, data: bytes, label: str,
                   library: str = "") -> str:
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
                       "knihovna": _resolve(library) or "(výchozí)",
                       "soubor": label, "bajtu": len(data)}, ensure_ascii=False)


@mcp.tool()
def copy_to_library(book_ids: list, target_library: str,
                    move: bool = False, library: str = "") -> str:
    """Zkopíruje (nebo přesune) knihy mezi dvěma knihovnami na serveru.

    `library` je zdrojová knihovna, `target_library` cílová.
    `move=True` knihy ze zdrojové knihovny **odstraní** — používej opatrně."""
    _guard_write()
    if not book_ids:
        raise CalibreError("Nebyly zadány žádné knihy.")
    target = _resolve(target_library)
    if not target:
        raise CalibreError("Musíš zadat cílovou knihovnu.")
    if target == (_resolve(library) or ""):
        raise CalibreError("Zdrojová a cílová knihovna jsou stejné.")
    body = {"book_ids": [int(b) for b in book_ids], "move": bool(move),
            "preserve_date": True, "duplicate_action": "add",
            "automerge_action": "overwrite"}
    res = _json("POST", "/cdb/copy-to-library/%s%s"
                % (quote(target, safe=""), _libseg(library)), json=body)
    return json.dumps({"ok": True, "z": _resolve(library) or "(výchozí)",
                       "do": target, "presun": bool(move), "vysledek": res},
                      ensure_ascii=False, indent=2)


# ------------------------------------------------------- převod formátu

@mcp.tool()
def convert_book(book_id: int, to_format: str, from_format: str = "",
                 extra_args: str = "", save_to: str = "",
                 library: str = "") -> str:
    """Převede knihu do jiného formátu.

    API Calibre převod neumí, takže tenhle nástroj: stáhne zdroj přes /get,
    pustí lokální ebook-convert a výsledek nahraje zpět jako nový formát.
    Na stroji s tímhle MCP serverem tedy musí být ebook-convert.

    Když zadáš `save_to`, výsledek se jen uloží do toho adresáře a do
    knihovny se nic nezapíše. `extra_args` jdou rovnou do ebook-convert."""
    to_fmt = to_format.lower().lstrip(".")
    if not to_fmt.isalnum():
        raise CalibreError("Nepřípustný formát: %s" % to_format)
    if not save_to:
        _guard_write()

    src_fmt = _pick_format(int(book_id), from_format, library)
    if src_fmt == to_fmt and not save_to:
        raise CalibreError("Kniha už ve formátu %s je." % to_fmt)

    with tempfile.TemporaryDirectory(prefix="calibre-api-mcp-") as tmp:
        tmpd = Path(tmp)
        r = _req("GET", "/get/%s/%d%s" % (quote(src_fmt), int(book_id),
                                          _libseg(library)), stream=True)
        src = tmpd / ("zdroj." + src_fmt)
        with open(src, "wb") as fh:
            for chunk in r.iter_content(65536):
                fh.write(chunk)

        out = tmpd / ("vystup." + to_fmt)
        cmd = [EBOOK_CONVERT, str(src), str(out)]
        if extra_args:
            cmd += extra_args.split()
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=max(TIMEOUT, 600), check=False)
        except FileNotFoundError as e:
            raise CalibreError(
                "Nenalezen ebook-convert (%s). Převod bez něj nejde — API "
                "Calibre konverzi nenabízí." % EBOOK_CONVERT) from e
        if p.returncode != 0 or not out.exists():
            err = (p.stderr or p.stdout or "").strip()
            raise CalibreError("ebook-convert selhal: %s" % err[-800:])

        if save_to:
            dest = Path(save_to).expanduser()
            dest.mkdir(parents=True, exist_ok=True)
            final = dest / out.name
            final.write_bytes(out.read_bytes())
            return json.dumps({"ok": True, "ulozeno": str(final),
                               "zdroj_format": src_fmt,
                               "bajtu": final.stat().st_size},
                              ensure_ascii=False)

        return _upload_format(int(book_id), to_fmt, out.read_bytes(),
                              out.name, library)


if __name__ == "__main__":
    mcp.run()
