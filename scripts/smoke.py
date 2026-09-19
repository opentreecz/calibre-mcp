#!/usr/bin/env python3
"""End-to-end smoke test against a running calibre-mcp over streamable-http.

Speaks raw JSON-RPC so it needs nothing but the standard library.

    python3 scripts/smoke.py                       # default http://127.0.0.1:8765/mcp
    python3 scripts/smoke.py http://host:8765/mcp
"""
import json
import sys
import urllib.error
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/mcp"
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

print("\nDone. Read-only tools only — nothing was modified.")
