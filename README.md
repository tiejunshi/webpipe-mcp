# WebPipe MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes the [WebPipe](https://webpipe.ai) web data extraction API as MCP tools, built with [FastMCP](https://github.com/jlowin/fastmcp) and [webpipe-sdk](https://pypi.org/project/webpipe-sdk/).

The WebPipe MCP server provides five tools:

- **webpipe_verify_auth** — the cheapest real API call; confirms the API key is valid before spending quota
- **webpipe_scrape** — fetch a single page as Markdown, HTML, links, metadata, or schema-guided JSON
- **webpipe_map** — discover all URLs on a site (robots.txt → sitemap → in-page links)
- **webpipe_crawl** — recursively crawl a site; the server polls until the job finishes; results are kept for 24 hours
- **webpipe_browser** — a persistent headless browser session that can click, fill forms, scroll, screenshot, and scrape JavaScript-heavy pages

Every tool returns errors as a readable string with a recommended recovery step instead of raising — including hints for auth (401), rate limit (429), bad request (400), expired crawl results, and transient network failures.

## Get your API key

Sign up at [webpipe.ai](https://webpipe.ai) and create a key at https://webpipe.ai/api-keys.html.

The key resolves in this order (highest first):

1. The explicit `api_key` argument passed to a tool call.
2. The `WEBPIPE_API_KEY` environment variable.

## Local MCP

### Prerequisites

- Python 3 with `pip`
- A WebPipe API key (see above)

### Installation

```bash
git clone https://github.com/tiejunshi/webpipe-mcp.git
cd webpipe-mcp
pip install -r requirements.txt
```

This installs two dependencies: `webpipe-sdk>=0.1.2` and `fastmcp>=4.0`.

### Running

**stdio mode** (the only supported mode; local MCP clients launch it via command + args):

```bash
export WEBPIPE_API_KEY=your-webpipe-api-key   # Windows: set WEBPIPE_API_KEY=...
python server.py
```

### Connect to Claude Code (local)

```bash
claude mcp add webpipe --env WEBPIPE_API_KEY=your-webpipe-api-key \
  -- python /absolute/path/to/webpipe-mcp/server.py
```

### JSON-configured clients

The template below follows **Claude Desktop's config file format** (`mcpServers` + `command`/`args`/`env` — the de-facto stdio template most clients copy):

```json
{
  "mcpServers": {
    "webpipe": {
      "command": "python",
      "args": ["/absolute/path/to/webpipe-mcp/server.py"],
      "env": { "WEBPIPE_API_KEY": "your-webpipe-api-key" }
    }
  }
}
```

Other agents use the same shape but different file locations:

| Agent | Config file |
| --- | --- |
| Claude Code | `.mcp.json` (project) or `~/.claude.json` |
| Cursor | `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project) |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` (Windows) / `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) |
| Cline | "MCP Servers" panel in the Cline UI |
| VS Code (Copilot) | `.vscode/mcp.json` — note the top-level key is `"servers"`, not `"mcpServers"` |
| Gemini CLI | `~/.gemini/settings.json` |

### Verify

After connecting, run:

```text
webpipe_verify_auth()
```

Expected: `{"authenticated": true, ...}`. Then try:

```text
webpipe_scrape(url="https://example.com")
```

Expected: a payload whose Markdown contains "Example Domain".

## Tools

### webpipe_verify_auth

Confirms the API key is valid with one minimal real call (fetches metadata of example.com). Use it first during setup, and after fixing any authentication problem.

| Parameter | Type | Notes |
| --- | --- | --- |
| `api_key` | string | Overrides `WEBPIPE_API_KEY` |
| `base_url` | string | Overrides `WEBPIPE_API_URL` |

### webpipe_scrape

Fetch a single page. Returns a `Document` with the requested formats filled in and the rest `null`.

| Parameter | Type | Notes |
| --- | --- | --- |
| `url` | string | Target page URL (required) |
| `formats` | string[] | Any of `markdown`, `html`, `rawHtml`, `links`, `metadata`, `json`. Default `["markdown"]` |
| `json_schema` | object | When `formats` includes `json`: JSON Schema defining the fields to extract. Supports the `x-source` extension |
| `use_proxy` | bool | Enable the backend proxy pool (anti-bot sites) |
| `proxy` | string | Custom proxy address (takes precedence over `use_proxy`) |
| `cookie` | string | Cookie string in header format |
| `api_key` / `base_url` | string | Same as above |

### webpipe_map

Discover all URLs on a site. Returns a list of `{ url, title, description }`.

| Parameter | Type | Notes |
| --- | --- | --- |
| `url` | string | Site root URL (required) |
| `limit` | int | Max results. Default 5000, max 50000 |
| `search` | string | Keyword filter on link title or description |
| `sitemap` | string | `include` / `only` / `exclude`. Default `include` |
| `same_domain` | bool | Only same-domain links. Default `true` |
| `use_proxy` / `proxy` / `cookie` / `api_key` / `base_url` | | Same as above |

### webpipe_crawl

Crawl a site recursively. Blocks until the job finishes (same behavior as the SDK); results are kept for 24 hours. Returns a `CrawlJob` (`status`, `total`, `completed`, `data`, `errors`, `expires_at`).

| Parameter | Type | Notes |
| --- | --- | --- |
| `url` | string | Start URL (required) |
| `limit` | int | Max pages. Default 100, max 10000 |
| `poll_interval` | float | Polling interval in seconds. Default 2.0 |
| `timeout` | float | Overall timeout in seconds; unlimited by default |
| `max_discovery_depth` | int | URL discovery depth |
| `include_paths` / `exclude_paths` | string[] | Regex arrays: only crawl / skip matching paths |
| `allow_subdomains` | bool | Default `false` |
| `allow_external_links` | bool | Default `false` |
| `crawl_entire_domain` | bool | Crawl the entire domain |
| `ignore_query_params` | bool | Ignore URL query parameters |
| `sitemap` | string | `include` / `skip` / `only` |
| `delay` | int | Delay per request in milliseconds. Default 0 |
| `max_concurrency` | int | 1–10. Default 3 |
| `scrape_formats` | string[] | Formats scraped per page. Default `["markdown"]` |
| `use_proxy` / `proxy` / `cookie` / `api_key` / `base_url` | | Same as above |

### webpipe_browser

A persistent headless browser. Three usage patterns:

- **One-shot:** omit `session_id` — a session is created at `url`, all `actions` run in order, the page is scraped, and the session closes automatically.
- **Persistent session:** set `auto_close=false` — the response carries a `session_id`; pass it on the next call to keep browser state (login, cookies) across calls.
- **Reusable login:** pass `session_name` together with `auto_close=false` — the logged-in state is saved under that name and can be restored later.

| Parameter | Type | Notes |
| --- | --- | --- |
| `url` | string | Start URL when creating the session (ignored if `session_id` is given) |
| `session_id` | string | Operate on an existing session instead of creating one |
| `actions` | list[object] | Interactions executed in order, e.g. `{"type": "click", "selector": "#more"}` |
| `navigate_url` | string | Navigate to this URL and scrape it |
| `formats` | string[] | Formats of the final scrape. Default `["markdown"]` |
| `scroll_to_bottom` | bool | Scroll to the bottom before scraping |
| `max_scrolls` | int | Max scroll rounds (≤ 30) |
| `scroll_wait` | int | Wait in milliseconds after each scroll |
| `scroll_step` | int | Pixels per scroll |
| `cookie` | string | Cookie injected when creating the session |
| `proxy` | string | Custom proxy used when creating the session |
| `session_name` | string | Persistence name for saving/reusing a logged-in session |
| `auto_close` | bool | Close the session when done. Default `true` |
| `api_key` / `base_url` | string | Same as above |

Supported action types: `click`, `fill`, `select`, `press`, `scroll`, `wait`, `wait_for`, `hover`, `check`, `screenshot`, `evaluate`. Actions run sequentially; a failing action records an `error` for that step and the remaining actions still run.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `WEBPIPE_API_KEY` | API key used when a tool call does not pass `api_key` explicitly |
| `WEBPIPE_API_URL` | API base URL override. Default: `https://api.web2json.ai` |

## Error handling

Tools never raise. Failures return a single readable string prefixed with:

- `❌` — auth (401), bad request (400), forbidden (403), not found (404), server error
- `⚠️` — rate limit (429)
- `🌐` — network error reaching `api.web2json.ai` (the SDK already retried internally)
- `🔴` — backend / crawl / poll-timeout errors

Each message includes a `Recovery:` line, e.g. fix `WEBPIPE_API_KEY` and confirm with `webpipe_verify_auth` after a 401, or back off and lower `max_concurrency`/`limit` after a 429.

## Acknowledgments

- [Model Context Protocol](https://modelcontextprotocol.io) for the MCP specification
- [FastMCP](https://github.com/jlowin/fastmcp) for the Python MCP framework
