# Claude Desktop: calibre in six steps

This guide uses the documented local MCP configuration for **Claude Desktop
on macOS and Windows**. Docker starts a dedicated stdio container when Claude
connects. Python 3.14 and all dependencies are inside the image; you do not
need Python, Node.js, or calibre inside that container.

## 1. Prepare calibre

In calibre, open **Preferences → Sharing over the net**. Enable the Content
Server, note its port (normally `8080`), and create an account under **User
accounts**. Enable write access only if you intend to edit or convert books.
Start the Content Server and keep calibre running.

From a terminal, check the API (curl prompts for the password):

```sh
curl --digest --user YOUR_USER http://localhost:8080/ajax/library-info
```

Use an ID from `library_map`, such as `Calibre_Library`, rather than its display
name. A container connects through Docker networking, so calibre must accept
connections on the host interface, not only `127.0.0.1`. If calibre is on
another machine, use that machine's hostname instead.

## 2. Install Docker and download the image

Install and start Docker Desktop, then run:

```sh
docker pull ghcr.io/opentreecz/calibre-mcp:latest
```

To pin the initial release, use `:0.1.0` in this command and in the configuration
below. Tags on GitHub use `v0.1.0`; image tags use `0.1.0`.

For a local build from a checkout:

```sh
docker build -t calibre-mcp:local .
```

Use `calibre-mcp:local` instead of the GHCR image in that case.

## 3. Create a credentials file

Create an absolute-path file, for example `/Users/alice/calibre-mcp.env` on
macOS or `C:\Users\Alice\calibre-mcp.env` on Windows:

```dotenv
CALIBRE_URL=http://host.docker.internal:8080
CALIBRE_LIBRARY_ID=Calibre_Library
CALIBRE_USER=YOUR_USER
CALIBRE_PASSWORD=YOUR_PASSWORD
CALIBRE_AUTH=digest
CALIBRE_READONLY=1
```

Use literal values without shell expansion. This file is passed directly to
`docker run --env-file`; do not commit it. Restrict access to your user account.
`host.docker.internal` is the host **from inside Docker**, whereas `localhost`
inside a container means the container itself.

## 4. Configure Claude Desktop

Open **Settings → Developer → Edit Config**. Merge the `calibre` entry into
your existing `mcpServers` object.

Configuration locations:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

macOS example:

```json
{
  "mcpServers": {
    "calibre": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "--env-file", "/Users/alice/calibre-mcp.env",
        "-e", "MCP_TRANSPORT=stdio",
        "ghcr.io/opentreecz/calibre-mcp:latest"
      ]
    }
  }
}
```

On Windows, change the credentials path to
`"C:\\Users\\Alice\\calibre-mcp.env"`. JSON requires doubled backslashes.
If Claude cannot find Docker, use its absolute executable path, obtained with
`command -v docker` on macOS or `where.exe docker` on Windows.

Keep `-i`; do not add `-t`, `-d`, or a port mapping. Claude communicates through
stdin/stdout and owns the container's lifecycle. `docker compose up` is not
needed for this setup.

The official Desktop guide lists macOS and Windows. For Linux, use Claude Code
or another supported MCP client; unofficial Desktop ports can have different
configuration locations. Linux Docker clients typically also need
`"--add-host", "host.docker.internal:host-gateway"` in the arguments.

## 5. Restart and try it

Fully quit and reopen Claude Desktop. In a new chat, open the connectors/tool
menu and check that `calibre` exposes tools such as `list_libraries` and
`search_books`. Try:

- “Use calibre to list my libraries and show their IDs.”
- “Search the Calibre_Library library for author:Čapek. Show titles and formats.”
- “Show the book counts and format breakdowns across all libraries.”

To enable edits, change `CALIBRE_READONLY=0` in the credentials file and fully
restart Claude Desktop so Docker receives the updated environment. Then try:

- “Read book 42 in Calibre_Library, then add the tag reviewed while preserving its existing tags.”
- “Show conversion options for book 42, then convert it to EPUB.”

Book IDs are local to a library. Use real IDs from your search results.
`set_metadata` replaces a tag list; clients adding a tag must merge existing tags.
For long conversions, ask Claude to start with `wait=False` and poll the job ID.

## 6. Optional: files and HTTP service

Downloads and uploads refer to paths **inside the container**. To exchange
files with your computer, add a bind mount before the image argument:

```json
"--mount", "type=bind,source=/Users/alice/BooksExchange,target=/exchange"
```

Create the host directory first and allow Docker access to it. The image runs
as UID 10001; the directory must be writable by that UID for downloads. Ask
Claude to use `/exchange` as the download directory or upload file location.

For Claude Code or another local HTTP client, use the Compose service instead:

```sh
cp .env.example .env
# Edit credentials in .env and calibre URL/library in docker-compose.yml.
docker compose pull
docker compose up -d --no-build
claude mcp add --transport http calibre http://127.0.0.1:8765/mcp
python3 scripts/smoke.py
```

Cloud/remote Claude connectors are a separate feature configured through
**Settings → Connectors**, subject to account and organization availability.
They require an endpoint reachable by the remote service; your local Compose
URL is not reachable from the cloud. This project does not implement incoming
MCP authentication, and the Compose port is intentionally bound to localhost.
Do not use a `type`/`url` entry in the local Desktop JSON as a substitute for
the documented `command`/`args` setup.

## Troubleshooting

| Symptom | Check |
|---|---|
| `docker` not found / `ENOENT` | Use the full Docker executable path. |
| Docker daemon unavailable | Start Docker Desktop before Claude. |
| Image pull denied | Check the image name and release publication; a private GHCR package requires `docker login ghcr.io`. |
| Server missing | Validate JSON, use absolute paths, and fully restart Desktop. |
| Connection refused | Check calibre's listen address, actual port, and host firewall. |
| HTTP 401 | Check credentials and `digest` versus `basic` auth. |
| HTTP 403 | Grant the calibre account write access; localhost write exceptions do not cover Docker. |
| Read-only error | Change the credentials file to `CALIBRE_READONLY=0` and restart Desktop. |
| Wrong books | Call `list_libraries` and use an explicit library ID. |
| Download permission denied | Check the bind mount and UID 10001 permissions. |

MCP logs: `~/Library/Logs/Claude/` on macOS and `%APPDATA%\Claude\logs` on
Windows. Inspect `mcp.log` and `mcp-server-calibre.log`.

For stdio, a manually started container waiting silently for input is normal.
For Compose HTTP, inspect `docker compose logs --tail=100 calibre-mcp` and run
the smoke script. It exits nonzero on tool errors.

Sources: [official local-server guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers)
and [remote-server guide](https://modelcontextprotocol.io/docs/develop/connect-remote-servers).
