# calibre-mcp

An MCP server that talks to a running **calibre Content Server** over its
HTTP API. Read your library, edit metadata, and convert formats from any
MCP client.

Because it speaks HTTP rather than driving `calibredb` against the library
directory, the calibre GUI can stay open and the library can live on a
different machine.

## Features

- **Multiple libraries** — every tool takes a `library` argument; no need to
  run one server per library
- **Read** — search in calibre query syntax, full metadata, per-library and
  cross-library search, format/stat breakdown, file download
- **Edit** — set metadata, add books, add formats, copy or move books
  between libraries
- **Convert** — epub / azw3 / mobi / pdf / docx …, result uploaded back to
  the book as an additional format

## Important: conversion is not an API feature

The calibre Content Server exposes **no conversion endpoint** — conversion in
calibre is a GUI job-queue and `ebook-convert` feature, and is not available
over HTTP. `convert_book` therefore downloads the source via `/get`, runs a
**local** `ebook-convert`, and uploads the result back through
`/cdb/set-fields`.

So the machine running this MCP server needs calibre installed for
`ebook-convert`. Every other tool is pure HTTP and needs nothing local.

## Requirements

- Python 3.10+
- A running calibre Content Server with write access enabled
- calibre installed locally — only if you want `convert_book`

## Install

```bash
git clone git@github.com:opentreecz/calibre-mcp.git
cd calibre-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Configuration

| Variable | Meaning |
|---|---|
| `CALIBRE_URL` | **required** — e.g. `http://localhost:8080` |
| `CALIBRE_LIBRARY_ID` | default library; override per call with `library` |
| `CALIBRE_USER` / `CALIBRE_PASSWORD` | if the server requires auth |
| `CALIBRE_AUTH` | `digest` (default) \| `basic` \| `none` |
| `EBOOK_CONVERT` | path to `ebook-convert` (default: from `PATH`) |
| `CALIBRE_READONLY` | `1` disables all write tools |
| `CALIBRE_TIMEOUT` | HTTP timeout in seconds (default 120) |
| `CALIBRE_VERIFY_TLS` | `0` to skip certificate checks (self-signed on a LAN) |

calibre defaults to **digest** auth. Behind a reverse proxy you normally
switch it to basic (`--auth-mode=basic`) — then set `CALIBRE_AUTH=basic`.

### Starting a Content Server

```bash
calibre-server --port 8080 --enable-local-write ~/Calibre\ Library
```

To keep the GUI open instead, use the server built into it:
Preferences → Sharing over the net. Never run both against one library.

### MCP client

```json
{
  "mcpServers": {
    "calibre": {
      "command": "/path/to/calibre-mcp/.venv/bin/python",
      "args": ["/path/to/calibre-mcp/calibre_mcp.py"],
      "env": {
        "CALIBRE_URL": "http://localhost:8080",
        "CALIBRE_LIBRARY_ID": "Calibre_Library"
      }
    }
  }
}
```

## Tools

| Tool | Pure API? | Purpose |
|---|---|---|
| `list_libraries` | yes | libraries on the server and their ids |
| `search_books` | yes | search one library, returns metadata |
| `search_all_libraries` | yes | search every library at once |
| `get_book` | yes | full metadata for one book |
| `library_stats` | yes | book count and format breakdown |
| `download_book` | yes | download a book file to a directory |
| `set_metadata` | yes | title, authors, tags, series, rating, custom columns |
| `add_book` | yes | upload a file as a new book |
| `add_format` | yes | attach another format to an existing book |
| `copy_to_library` | yes | copy or move books between libraries |
| `convert_book` | hybrid | download → local `ebook-convert` → upload back |

Deleting books is deliberately not implemented, even though
`/cdb/delete-books` exists. An irreversible operation triggered by accident
from a chat is a bad idea.

## Endpoints used

Verified against the calibre sources (`src/calibre/srv/`):

```
GET  /ajax/library-info
GET  /ajax/search/{lib}?query=&num=&offset=&sort=&sort_order=
GET  /ajax/books/{lib}?ids=1,2,3
GET  /ajax/book/{id}/{lib}
GET  /get/{fmt}/{id}/{lib}
POST /cdb/set-fields/{id}/{lib}
POST /cdb/add-book/{job}/{dup}/{filename}/{lib}
POST /cdb/copy-to-library/{target}/{lib}
```

Adding a format has no endpoint of its own — it goes through `set-fields`
with the special `added_formats` field, the file passed as a base64 data URL.

## Tests

`tests/` contains a mock Content Server implementing the endpoints above,
plus a smoke test covering both single- and multi-library routing.

```bash
.venv/bin/python -m pytest tests/ -v
```

The mock verifies client logic — URL construction, JSON shapes, base64
encoding. It is not a substitute for testing against a real calibre server.
Back up `metadata.db` and start with `CALIBRE_READONLY=1`.

## Docs

- [Návod v češtině](docs/navod-cs.md) — deployment on Debian

## License

AGPL-3.0
