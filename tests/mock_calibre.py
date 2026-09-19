"""Minimal stand-in for the calibre Content Server.

Implements only the endpoints calibre_mcp.py uses, with two libraries so
multi-library routing can be verified.
"""
import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

LIBS = {"Calibre_Library": "Calibre Library", "calibre_pdb": "Calibre PDB"}
DEFAULT = "Calibre_Library"


def _book(bid, title, formats):
    return {"application_id": bid, "title": title, "authors": ["Tester"],
            "tags": ["test"], "formats": list(formats), "series": None,
            "series_index": None, "pubdate": None, "rating": None,
            "languages": ["eng"]}


def fresh_db():
    return {
        "Calibre_Library": {1: _book(1, "Main Book", ["EPUB", "PDF"])},
        "calibre_pdb": {7: _book(7, "PDB Book", ["PDF"])},
    }


class State:
    def __init__(self):
        self.db = fresh_db()
        self.seen = []
        self.jobs = {}          # job_id -> dict
        self.next_job = 100
        self.polls_before_done = 1   # kolikrat vratit running=True


def _query_lib(path):
    from urllib.parse import urlparse, parse_qs
    q = parse_qs(urlparse(path).query)
    lib = (q.get("library_id") or [""])[0]
    return lib if lib in LIBS else DEFAULT, q


def _lib_after(path, prefix):
    rest = path[len(prefix):].strip("/")
    seg = rest.split("/")[0] if rest else ""
    return seg if seg in LIBS else DEFAULT


def make_handler(state):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj, raw=False, hdr=None):
            body = obj if raw else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type",
                             "application/octet-stream" if raw else "application/json")
            for k, v in (hdr or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _conversion(self, p, raw=b""):
            """Vrati True, pokud slo o /conversion/* a bylo obslouzeno."""
            lib, q = _query_lib(self.path)
            m = re.match(r"^/conversion/book-data/(\d+)$", p)
            if m:
                bid = int(m.group(1))
                if bid not in state.db[lib]:
                    self._send(404, {"error": "no such book"}); return True
                have = [f.lower() for f in state.db[lib][bid]["formats"]]
                self._send(200, {
                    "book_id": bid,
                    "title": state.db[lib][bid]["title"],
                    "authors": state.db[lib][bid]["authors"],
                    "input_formats": [f.upper() for f in have],
                    "output_formats": ["epub", "mobi", "azw3", "pdf", "docx"],
                    "profiles": {"input": [], "output": []},
                    "conversion_options": {"options": {"pretty_print": {}}},
                })
                return True
            m = re.match(r"^/conversion/start/(\d+)$", p)
            if m:
                bid = int(m.group(1))
                body = json.loads(raw) if raw else {}
                jid = state.next_job; state.next_job += 1
                state.jobs[jid] = {"book": bid, "lib": lib, "polls": 0,
                                   "out": body.get("output_fmt", "epub"),
                                   "inp": body.get("input_fmt"),
                                   "options": body.get("options")}
                state.seen.append(("CONV_START", lib, bid, body.get("input_fmt"),
                                   body.get("output_fmt"), body.get("options")))
                self._send(200, jid)
                return True
            m = re.match(r"^/conversion/status/(\d+)$", p)
            if m:
                jid = int(m.group(1))
                job = state.jobs.get(jid)
                if job is None:
                    self._send(404, {"error": "no job"}); return True
                if q.get("abort_job"):
                    del state.jobs[jid]
                    state.seen.append(("CONV_ABORT", jid))
                    self._send(200, {"running": False, "ok": False,
                                     "was_aborted": True, "traceback": None,
                                     "log": "aborted"})
                    return True
                job["polls"] += 1
                if job["polls"] <= state.polls_before_done:
                    self._send(200, {"running": True, "percent": 0.5,
                                     "msg": "converting"})
                    return True
                del state.jobs[jid]
                fmt = job["out"]
                state.db[job["lib"]][job["book"]]["formats"].append(fmt.upper())
                state.seen.append(("CONV_DONE", job["lib"], job["book"], fmt))
                self._send(200, {"running": False, "ok": True,
                                 "was_aborted": False, "traceback": None,
                                 "log": "ok", "size": 4242, "fmt": fmt})
                return True
            return False

        def do_GET(self):
            p = self.path.split("?")[0]
            state.seen.append(("GET", p))
            if p.startswith("/conversion/") and self._conversion(p):
                return
            if p == "/ajax/library-info":
                return self._send(200, {"library_map": LIBS,
                                        "default_library": DEFAULT})
            if p.startswith("/ajax/search"):
                lib = _lib_after(p, "/ajax/search")
                ids = list(state.db[lib])
                return self._send(200, {"book_ids": ids, "total_num": len(ids)})
            if p.startswith("/ajax/books"):
                lib = _lib_after(p, "/ajax/books")
                m = re.search(r"ids=([\d%2C,]+)", self.path)
                ids = ([int(x) for x in m.group(1).replace("%2C", ",").split(",")]
                       if m else list(state.db[lib]))
                return self._send(200, {str(i): state.db[lib][i]
                                        for i in ids if i in state.db[lib]})
            m = re.match(r"^/ajax/book/(\d+)(?:/(.+))?$", p)
            if m:
                lib = m.group(2) if m.group(2) in LIBS else DEFAULT
                bid = int(m.group(1))
                if bid not in state.db[lib]:
                    return self._send(404, {"error": "no such book"})
                return self._send(200, state.db[lib][bid])
            m = re.match(r"^/get/([^/]+)/(\d+)(?:/(.+))?$", p)
            if m:
                lib = m.group(3) if m.group(3) in LIBS else DEFAULT
                payload = ("FAKE:%s:%s:%s" % (lib, m.group(1), m.group(2))).encode()
                return self._send(200, payload, raw=True,
                                  hdr={"Content-Disposition":
                                       'attachment; filename="book.%s"' % m.group(1)})
            self._send(404, {"error": "no route " + p})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n)
            p = self.path.split("?")[0]
            state.seen.append(("POST", p))
            if p.startswith("/conversion/") and self._conversion(p, raw):
                return
            m = re.match(r"^/cdb/set-fields/(\d+)(?:/(.+))?$", p)
            if m:
                lib = m.group(2) if m.group(2) in LIBS else DEFAULT
                bid = int(m.group(1))
                changes = json.loads(raw).get("changes", {})
                if "added_formats" in changes:
                    for af in changes["added_formats"]:
                        blob = base64.b64decode(af["data_url"].split(",", 1)[-1])
                        state.db[lib][bid]["formats"].append(af["ext"].upper())
                        state.seen.append(("UPLOAD", lib, bid, af["ext"], blob))
                else:
                    state.db[lib][bid].update(changes)
                    state.seen.append(("EDIT", lib, bid, sorted(changes)))
                return self._send(200, {str(bid): state.db[lib][bid]})
            m = re.match(r"^/cdb/add-book/([^/]+)/([^/]+)/([^/]+)(?:/(.+))?$", p)
            if m:
                lib = m.group(4) if m.group(4) in LIBS else DEFAULT
                nid = max(state.db[lib]) + 1
                state.db[lib][nid] = _book(nid, m.group(3), ["EPUB"])
                state.seen.append(("ADD", lib, nid, len(raw)))
                return self._send(200, {"book_id": nid, "title": m.group(3)})
            m = re.match(r"^/cdb/copy-to-library/([^/]+)(?:/(.+))?$", p)
            if m:
                target = m.group(1)
                src = m.group(2) if m.group(2) in LIBS else DEFAULT
                body = json.loads(raw)
                state.seen.append(("COPY", src, target, body["book_ids"],
                                   body["move"]))
                return self._send(200, {"copied": body["book_ids"]})
            self._send(404, {"error": "no route " + p})

    return H


def start(port=0):
    """Start the mock in a background thread. Returns (base_url, state, stop)."""
    state = State()
    srv = HTTPServer(("127.0.0.1", port), make_handler(state))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    url = "http://127.0.0.1:%d" % srv.server_address[1]
    return url, state, srv.shutdown
