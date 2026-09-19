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

## Conversion runs on the calibre server

Conversion uses the Content Server's own `/conversion/*` endpoints: the
server queues the job, runs it, and **adds the resulting format to the book
itself**. This client only starts the job and polls for its status.

That means **calibre does not need to be installed alongside this server** —
everything is plain HTTP. Handy for the Docker image, which stays small.

## Requirements

- Python 3.10+
- A running calibre Content Server with write access enabled

Nothing else. No local calibre, no `ebook-convert`.

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
| `CALIBRE_USER` / `CALIBRE_PASSWORD` | content server account (see the Docker note) |
| `CALIBRE_AUTH` | `digest` (default) \| `basic` \| `none` |
| `CALIBRE_READONLY` | `1` disables all write tools |
| `CALIBRE_TIMEOUT` | HTTP timeout in seconds (default 120) |
| `CALIBRE_VERIFY_TLS` | `0` to skip certificate checks (self-signed on a LAN) |
| `MCP_TRANSPORT` | `stdio` (default) or `streamable-http` for container use |
| `MCP_HOST` / `MCP_PORT` / `MCP_PATH` | HTTP transport bind settings |

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

## Docker

The image is Debian stable (bookworm) + Python 3.12 and contains no calibre,
because conversion happens server-side.

```bash
docker compose build
docker compose up -d
docker compose logs -f
```

The container speaks **streamable-http** on `127.0.0.1:8765/mcp`. stdio is
not usable here — with stdio the MCP client has to spawn the process itself,
which a long-running service cannot provide.

`docker-compose.yml` reaches calibre on the host through
`host.docker.internal` (mapped via `extra_hosts: host-gateway`). If that does
not work on your system, switch the service to `network_mode: host` — the
file has it prepared and commented out.

Two catches, both found the hard way:

**A real account is required.** calibre's "allow unauthenticated local
connections to make changes" does **not** apply to the container. The
container reaches calibre from the Docker bridge network (172.x.x.x), which
calibre does not count as a local connection, so writes are refused however
that option is set. Create a user with write permission
(`calibre-server --manage-users`, or Preferences → Sharing over the net →
User accounts) and put it in `.env`. Reads work either way, so the symptom
is that everything looks fine until the first write.

**calibre must listen on a reachable interface.** If `calibre-server` is
bound to `127.0.0.1` only, the container will not get through — start it
with `--listen-on 0.0.0.0`, or use `network_mode: host`.

Credentials live in `.env`, which is gitignored:

```bash
cp .env.example .env    # then fill in CALIBRE_USER / CALIBRE_PASSWORD
docker compose up -d
```

Point your MCP client at the HTTP endpoint:

```json
{
  "mcpServers": {
    "calibre": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8765/mcp"
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
| `conversion_options` | yes | which input/output formats and options a book supports |
| `convert_book` | yes | queue a conversion on the server and wait for it |
| `conversion_job_status` | yes | poll or abort a job started with `wait=False` |

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
GET  /conversion/book-data/{id}?library_id=
POST /conversion/start/{id}?library_id=
GET  /conversion/status/{job}?library_id=
```

Note that `/conversion/*` takes the library as a **query parameter**, while
`/ajax/*` and `/cdb/*` take it as a path segment.

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
