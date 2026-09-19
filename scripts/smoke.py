#!/usr/bin/env python3
"""End-to-end smoke test against a running calibre-mcp over streamable-http.

Speaks raw JSON-RPC so it needs nothing but the standard library.

    python3 scripts/smoke.py                       # read-only checks
    python3 scripts/smoke.py --convert 896:azw3    # also convert one book
    python3 scripts/smoke.py --url http://host:8765/mcp

Without --convert nothing is modified. With it, the named book gains the
named format (the calibre server adds it itself once the job finishes).
"""
import json
import sys
import urllib.error
import urllib.request

args = sys.argv[1:]
CONVERT = None
URL = "http://127.0.0.1:8765/mcp"
i = 0
while i < len(args):
    if args[i] == "--convert" and i + 1 < len(args):
        CONVERT = args[i + 1]; i += 2
    elif args[i] == "--url" and i + 1 < len(args):
        URL = args[i + 1]; i += 2
    elif not args[i].startswith("--"):
        URL = args[i]; i += 1
    else:
        raise SystemExit("unknown argument: %s" % args[i])
SESSION = None
_id = 0


def rpc(method, params=None, notify=False):
    """One JSON-RPC call over streamable-http. Returns the parsed result."""
    global SESSION, _id
    body = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        body["params"] = params
    if not notify:
        _id += 1
        body["id"] = _id
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 **({"mcp-session-id": SESSION} if SESSION else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            SESSION = SESSION or r.headers.get("mcp-session-id")
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        raise SystemExit("HTTP %s from %s\n%s" % (e.code, URL, e.read().decode()[:500]))
    except urllib.error.URLError as e:
        raise SystemExit("Cannot reach %s: %s" % (URL, e.reason))
    if notify:
        return None
    # the body is SSE: "event: message\ndata: {...}"
    for line in raw.splitlines():
        if line.startswith("data:"):
            msg = json.loads(line[5:].strip())
            if "error" in msg:
                raise SystemExit("MCP error: %s" % msg["error"])
            return msg.get("result")
    raise SystemExit("Unexpected response:\n" + raw[:500])


def call(tool, **args):
    res = rpc("tools/call", {"name": tool, "arguments": args})
    if res.get("isError"):
        return "TOOL ERROR: " + "".join(
            c.get("text", "") for c in res.get("content", []))
    return "".join(c.get("text", "") for c in res.get("content", []))


def head(t):
    print("\n" + "=" * 62 + "\n" + t + "\n" + "=" * 62)


info = rpc("initialize", {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "smoke", "version": "1"}})
rpc("notifications/initialized", notify=True)
print("connected to:", info["serverInfo"]["name"],
      "| protocol:", info["protocolVersion"], "| session:", (SESSION or "-")[:8])

head("1. tools/list")
tools = rpc("tools/list")["tools"]
print(len(tools), "tools:", ", ".join(t["name"] for t in tools))

head("2. list_libraries  (does the container reach calibre?)")
print(call("list_libraries"))

head("3. library_stats")
print(call("library_stats", max_books=2000))

head("4. search_books (5 newest)")
out = call("search_books", limit=5)
print(out[:1500])

head("5. conversion_options for the first book found")
try:
    first = json.loads(out)["knihy"][0]
    print("book:", first["id"], first["title"], first["formats"])
    print(call("conversion_options", book_id=first["id"]))
except Exception as e:
    print("skipped:", e)

if not CONVERT:
    print("\nDone. Read-only tools only — nothing was modified.")
    raise SystemExit(0)

try:
    bid_s, fmt = CONVERT.split(":", 1)
    bid = int(bid_s)
except ValueError:
    raise SystemExit("--convert expects BOOK_ID:FORMAT, e.g. 896:azw3")

head("6. formats BEFORE conversion")
before = json.loads(call("get_book", book_id=bid))
print(bid, before.get("title"), "->", before.get("formats"))

head("7. convert %d to %s  (runs on the calibre server)" % (bid, fmt))
print(call("convert_book", book_id=bid, to_format=fmt))

head("8. formats AFTER conversion")
after = json.loads(call("get_book", book_id=bid))
print(bid, after.get("title"), "->", after.get("formats"))
gained = set(map(str.lower, after.get("formats") or [])) - set(
    map(str.lower, before.get("formats") or []))
print("\ngained:", sorted(gained) or "NOTHING - something went wrong")
