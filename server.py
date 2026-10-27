"""
WebPipe MCP Server — exposes the WebPipe API (https://webpipe.ai) as MCP tools
for AI agents.

Usage:
  # stdio mode (local development / testing)
  python server.py

  # HTTP Streamable mode (deploy for agents to connect)
  fastmcp run server.py:mcp --transport streamable-http --host 0.0.0.0 --port 8080

Auth:
  Set the WEBPIPE_API_KEY environment variable, or pass api_key per call.
  Get a key at https://webpipe.ai/api-keys.html
"""

from __future__ import annotations

import os
import json
import traceback
from typing import Any, Optional, Sequence

from fastmcp import FastMCP
from webpipe import (
    WebpipeClient,
    WebpipeError,
    AuthenticationError,
    RateLimitError,
    NotFoundError,
    BadRequestError,
    ForbiddenError,
    ServerError,
    CrawlError,
    PollTimeoutError,
)

# ---------------------------------------------------------------------------
# MCP instance
# ---------------------------------------------------------------------------

_INSTRUCTIONS = """WebPipe (https://webpipe.ai) turns any web page into clean, agent-ready data
through one hosted API — no browser infrastructure, no proxy management, no
HTML parsing on your side.

WHY WEBPIPE
- One API key, four tools: everything runs server-side at api.web2json.ai.
- Handles JavaScript-rendered pages, anti-bot sites (optional proxy pool), and
  sitemap-based site discovery for you.
- Structured output: Markdown by default, or schema-guided JSON extraction.
- Persistent headless browser sessions: log in once, reuse the session.

WHICH TOOL FOR WHICH TASK
1. webpipe_verify_auth — call this FIRST when unsure whether the API key is
   valid. It is the cheapest way to confirm authentication works.
2. webpipe_scrape — you know the exact URL and need its content once.
   Returns Markdown/HTML/links/metadata, or JSON fields guided by json_schema.
3. webpipe_map — you know the site but not which pages you need. Discovers
   URLs via robots.txt -> sitemap -> in-page links before scraping anything.
4. webpipe_crawl — you need many pages from a site or section. Runs as an
   async job; this server polls until it finishes (default limit 100 pages).
   Results are kept for 24 hours.
5. webpipe_browser — the page needs JavaScript interaction: login, clicking,
   filling forms, screenshots, infinite scroll. Sessions can persist across
   calls via session_id / session_name.

OUTPUT FORMAT
Every tool returns a JSON string. Successful calls return the model payload
shown in each tool's description. Failed calls return a single human-readable
error string prefixed with one of: ❌ (auth/param/server), ⚠️ (rate limit),
🌐 (network), 🔴 (backend). Tools never raise; inspect the returned text.

ERRORS AND RECOMMENDED RECOVERY
- ❌ API Key invalid or expired (HTTP 401): the key is missing/wrong. Fix the
  WEBPIPE_API_KEY env var (or pass api_key), then confirm with
  webpipe_verify_auth before retrying the original call.
- ⚠️ Rate limit (HTTP 429): wait a few seconds and retry; back off
  exponentially if it repeats. Reduce crawl concurrency (max_concurrency) or
  page count (limit) for crawl-heavy workloads.
- ❌ Bad request (HTTP 400): a tool argument is invalid. Re-check required
  `url` and optional parameter formats, then retry with corrected arguments.
- ❌ Not found (HTTP 404): a crawl job ID or resource does not exist or has
  expired (crawl results are kept 24 hours). Re-run the crawl if needed.
- ❌ Forbidden (HTTP 403): the key has no access to that resource/plan.
- 🌐 Network error: transient SSL/proxy flakiness between this machine and
  api.web2json.ai. The SDK already retried internally; wait briefly and retry
  once. If it persists, check outbound connectivity/proxy settings.
- 🔴 Server/crawl/poll-timeout errors: backend-side problem. Retry later, or
  reduce scope (smaller `limit`, shallower depth, exclude heavy paths).

TYPICAL FLOW
webpipe_verify_auth -> webpipe_map (find pages) -> webpipe_scrape (get data).
Escalate to webpipe_crawl for bulk needs, webpipe_browser for JS-gated pages.
"""

mcp = FastMCP(
    name="webpipe",
    instructions=_INSTRUCTIONS,
    version="0.2.0",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_API_KEY_ENV = "WEBPIPE_API_KEY"
_BASE_URL_ENV = "WEBPIPE_API_URL"


def _get_client(api_key: Optional[str] = None, base_url: Optional[str] = None) -> WebpipeClient:
    """Build a WebpipeClient: explicit args > env vars > SDK default base_url."""
    key = api_key or os.environ.get(_API_KEY_ENV)
    if not key:
        raise ValueError(
            f"Missing API key. Set the {_API_KEY_ENV} environment variable or pass api_key.\n"
            f"Get a key at https://webpipe.ai/api-keys.html"
        )
    url = base_url or os.environ.get(_BASE_URL_ENV)  # SDK default: https://api.web2json.ai
    kwargs: dict[str, Any] = {"api_key": key}
    if url:
        kwargs["base_url"] = url
    return WebpipeClient(**kwargs)


def _friendly_error(e: Exception) -> str:
    """Convert SDK exceptions into agent-friendly text with recovery hints."""
    if isinstance(e, AuthenticationError):
        return (
            f"❌ API key is invalid or expired: {e}\n"
            f"Recovery: fix the {_API_KEY_ENV} environment variable (or pass api_key), "
            f"then confirm with webpipe_verify_auth before retrying. "
            f"Get a key at https://webpipe.ai/api-keys.html"
        )
    if isinstance(e, RateLimitError):
        return (
            f"⚠️ Rate limit exceeded (429): {e}\n"
            f"Recovery: wait a few seconds and retry with backoff. For crawl-heavy "
            f"workloads, lower max_concurrency or limit."
        )
    if isinstance(e, NotFoundError):
        return (
            f"❌ Resource not found or expired: {e}\n"
            f"Recovery: crawl results are kept 24 hours — re-run the crawl if the job expired."
        )
    if isinstance(e, BadRequestError):
        return (
            f"❌ Bad request: {e}\n"
            f"Recovery: re-check the required `url` and optional parameter formats, then retry."
        )
    if isinstance(e, ForbiddenError):
        return (
            f"❌ Forbidden: {e}\n"
            f"Recovery: your API key has no access to this resource or feature. "
            f"Check your plan at https://webpipe.ai"
        )
    if isinstance(e, (ServerError, CrawlError, PollTimeoutError)):
        return (
            f"🔴 Server-side error: {e}\n"
            f"Recovery: retry later, or reduce scope (smaller limit, shallower depth, "
            f"exclude heavy paths)."
        )
    if isinstance(e, WebpipeError):
        return f"❌ WebPipe API error: {e}"
    try:
        import httpx

        if isinstance(e, httpx.TransportError):
            return (
                f"🌐 Network error (SDK already retried internally): {type(e).__name__}: {e}\n"
                f"Recovery: wait briefly and retry once. If it persists, check outbound "
                f"connectivity/proxy to api.web2json.ai from this machine."
            )
    except ImportError:
        pass
    if isinstance(e, ValueError):
        return str(e)
    return f"❌ Unknown error: {type(e).__name__}: {e}"


def _plain(obj: Any) -> Any:
    """Recursively convert pydantic models / lists / dicts into JSON-safe objects.

    SDK returns both single models (Document, CrawlJob) and model lists
    (map returns list[MapLink]), so handle all shapes.
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, (list, tuple)):
        return [_plain(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    return obj


def _to_text(obj: Any) -> str:
    """Serialize SDK results into a JSON string for the agent."""
    return json.dumps(_plain(obj), ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# MCP tool 0: webpipe_verify_auth
# ---------------------------------------------------------------------------

@mcp.tool()
def webpipe_verify_auth(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """Confirm that the WebPipe API key is valid and the service is reachable.

    Makes one minimal real API call (fetch metadata of example.com). Use this
    FIRST during setup, or after fixing authentication problems, before
    spending quota on larger calls.

    Args:
        api_key: WebPipe API key. Defaults to env var WEBPIPE_API_KEY.
        base_url: API base URL. Defaults to https://api.web2json.ai.

    Returns:
        On success, a JSON confirmation, e.g.:
        {
          "authenticated": true,
          "message": "WebPipe authentication OK",
          "checked_url": "https://example.com",
          "page_title": "Example Domain"
        }
        On failure, an error string prefixed with ❌ (see recovery hints inside).
    """
    try:
        client = _get_client(api_key, base_url)
        doc = client.scrape("https://example.com", formats=["metadata"])
        meta = doc.metadata if doc else None
        return json.dumps(
            {
                "authenticated": True,
                "message": "WebPipe authentication OK",
                "checked_url": "https://example.com",
                "page_title": getattr(meta, "title", None),
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        return _friendly_error(e)


# ---------------------------------------------------------------------------
# MCP tool 1: webpipe_scrape
# ---------------------------------------------------------------------------

@mcp.tool()
def webpipe_scrape(
    url: str,
    formats: Optional[list[str]] = None,
    json_schema: Optional[dict[str, Any]] = None,
    use_proxy: Optional[bool] = None,
    proxy: Optional[str] = None,
    cookie: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """Scrape a single web page into Markdown / HTML / links / metadata / structured JSON.

    Use when you know the exact URL and need its content once. For
    JavaScript-heavy pages or login-gated content, prefer webpipe_browser.

    Args:
        url: Target page URL (required).
        formats: Output formats, any of: markdown, html, rawHtml, links, metadata, json. Default ["markdown"].
        json_schema: When formats includes "json": JSON Schema defining fields to extract. Supports the "x-source" extension.
        use_proxy: Enable the backend proxy pool (for anti-bot sites).
        proxy: Custom proxy address (takes precedence over use_proxy).
        cookie: Cookie string in header format.
        api_key: WebPipe API key. Defaults to env var WEBPIPE_API_KEY.
        base_url: API base URL. Defaults to https://api.web2json.ai.

    Returns:
        JSON string of a Document. With default formats, e.g.:
        {
          "markdown": "# Example Domain\\n\\nThis domain is for use in illustrative examples...",
          "html": null,
          "rawHtml": null,
          "links": null,
          "metadata": {
            "title": "Example Domain",
            "description": null,
            "sourceURL": "https://example.com",
            "statusCode": 200
          },
          "json": null
        }
        Requested formats are filled in; others stay null. On failure, an
        error string with recovery hints (see server instructions).
    """
    try:
        client = _get_client(api_key, base_url)
        doc = client.scrape(
            url,
            formats=formats,
            json_schema=json_schema,
            use_proxy=use_proxy,
            proxy=proxy,
            cookie=cookie,
        )
        return _to_text(doc)
    except Exception as e:
        return _friendly_error(e)


# ---------------------------------------------------------------------------
# MCP tool 2: webpipe_map
# ---------------------------------------------------------------------------

@mcp.tool()
def webpipe_map(
    url: str,
    limit: Optional[int] = None,
    search: Optional[str] = None,
    sitemap: Optional[str] = None,
    same_domain: Optional[bool] = None,
    use_proxy: Optional[bool] = None,
    proxy: Optional[str] = None,
    cookie: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """Discover all URLs on a site (robots.txt -> sitemap -> in-page links).

    Use when you know the site but not which pages you need. Run this before
    scraping to pick the right URLs.

    Args:
        url: Target site root URL (required).
        limit: Max number of links returned. Default 5000, max 50000.
        search: Keyword filter (only links whose title/description contain it).
        sitemap: Sitemap strategy: include / only / exclude. Default include.
        same_domain: Only return same-domain links. Default true.
        use_proxy: Enable the backend proxy pool.
        proxy: Custom proxy address.
        cookie: Cookie string in header format.
        api_key: WebPipe API key.
        base_url: API base URL.

    Returns:
        JSON array of link objects, e.g.:
        [
          {
            "url": "https://example.com/docs/getting-started",
            "title": "Getting Started",
            "description": "How to install and make your first request"
          },
          {
            "url": "https://example.com/pricing",
            "title": "Pricing",
            "description": null
          }
        ]
        `title`/`description` may be null. On failure, an error string with
        recovery hints (see server instructions).
    """
    try:
        client = _get_client(api_key, base_url)
        links = client.map(
            url,
            limit=limit,
            search=search,
            sitemap=sitemap,  # type: ignore[arg-type]
            same_domain=same_domain,
            use_proxy=use_proxy,
            proxy=proxy,
            cookie=cookie,
        )
        return _to_text(links)
    except Exception as e:
        return _friendly_error(e)


# ---------------------------------------------------------------------------
# MCP tool 3: webpipe_crawl
# ---------------------------------------------------------------------------

@mcp.tool()
def webpipe_crawl(
    url: str,
    limit: Optional[int] = None,
    poll_interval: Optional[float] = None,
    timeout: Optional[float] = None,
    max_discovery_depth: Optional[int] = None,
    include_paths: Optional[list[str]] = None,
    exclude_paths: Optional[list[str]] = None,
    allow_subdomains: Optional[bool] = None,
    allow_external_links: Optional[bool] = None,
    crawl_entire_domain: Optional[bool] = None,
    ignore_query_params: Optional[bool] = None,
    sitemap: Optional[str] = None,
    delay: Optional[int] = None,
    max_concurrency: Optional[int] = None,
    scrape_formats: Optional[list[str]] = None,
    use_proxy: Optional[bool] = None,
    proxy: Optional[str] = None,
    cookie: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """Crawl an entire site recursively (async job; this tool polls until done).

    Use when you need many pages from a site or section. Blocks until the
    crawl finishes or times out (same behavior as the SDK). Results are kept
    for 24 hours. For large sites, cap `limit` and use include/exclude_paths.

    Args:
        url: Start URL (required).
        limit: Max pages to crawl. Default 100, max 10000.
        poll_interval: Polling interval in seconds. Default 2.0.
        timeout: Overall timeout in seconds. Unlimited by default.
        max_discovery_depth: URL discovery depth.
        include_paths: Regex array; only crawl matching paths.
        exclude_paths: Regex array; skip matching paths.
        allow_subdomains: Allow subdomains. Default false.
        allow_external_links: Allow external links. Default false.
        crawl_entire_domain: Crawl the entire domain.
        ignore_query_params: Ignore URL query parameters.
        sitemap: Sitemap strategy: include / skip / only.
        delay: Delay per request in milliseconds. Default 0.
        max_concurrency: Max concurrency 1-10. Default 3.
        scrape_formats: Formats scraped per page. Default ["markdown"].
        use_proxy: Enable the proxy pool.
        proxy: Custom proxy.
        cookie: Cookie string in header format.
        api_key: WebPipe API key.
        base_url: API base URL.

    Returns:
        JSON string of a CrawlJob, e.g.:
        {
          "status": "completed",
          "total": 12,
          "completed": 12,
          "data": [
            {
              "markdown": "# Pricing\\n\\n...",
              "html": null,
              "rawHtml": null,
              "links": null,
              "metadata": { "title": "Pricing", "sourceURL": "https://example.com/pricing", "statusCode": 200 },
              "json": null
            }
          ],
          "errors": [],
          "expires_at": "2026-10-27T12:00:00Z"
        }
        On failure, an error string with recovery hints (see server instructions).
    """
    try:
        client = _get_client(api_key, base_url)

        # Assemble crawl options (**options are passed through to the SDK)
        options: dict[str, Any] = {}
        if limit is not None:
            options["limit"] = limit
        if max_discovery_depth is not None:
            options["max_discovery_depth"] = max_discovery_depth
        if include_paths is not None:
            options["include_paths"] = include_paths
        if exclude_paths is not None:
            options["exclude_paths"] = exclude_paths
        if allow_subdomains is not None:
            options["allow_subdomains"] = allow_subdomains
        if allow_external_links is not None:
            options["allow_external_links"] = allow_external_links
        if crawl_entire_domain is not None:
            options["crawl_entire_domain"] = crawl_entire_domain
        if ignore_query_params is not None:
            options["ignore_query_params"] = ignore_query_params
        if sitemap is not None:
            options["sitemap"] = sitemap
        if delay is not None:
            options["delay"] = delay
        if max_concurrency is not None:
            options["max_concurrency"] = max_concurrency
        if scrape_formats is not None:
            options["scrape_options"] = {"formats": scrape_formats}
        if use_proxy is not None:
            options["use_proxy"] = use_proxy
        if proxy is not None:
            options["proxy"] = proxy
        if cookie is not None:
            options["cookie"] = cookie

        kwargs: dict[str, Any] = {}
        if poll_interval is not None:
            kwargs["poll_interval"] = poll_interval
        if timeout is not None:
            kwargs["timeout"] = timeout

        if options:
            job = client.crawl(url, **kwargs, **options)
        else:
            job = client.crawl(url, **kwargs)

        return _to_text(job)
    except Exception as e:
        return _friendly_error(e)


# ---------------------------------------------------------------------------
# MCP tool 4: webpipe_browser
# ---------------------------------------------------------------------------

# Action types supported by Browser Session + their parameter definitions
_ACTION_SCHEMA = {
    "click": {"selector": str, "wait_after": Optional[int]},
    "fill": {"selector": str, "value": str},
    "select": {"selector": str, "value": str},
    "press": {"key": str, "selector": Optional[str], "wait_after": Optional[int]},
    "scroll": {"direction": Optional[str], "amount": Optional[int]},
    "wait": {"ms": int},
    "wait_for": {"selector": str, "timeout": Optional[int]},
    "hover": {"selector": str},
    "check": {"selector": str},
    "screenshot": {"full_page": Optional[bool]},
    "evaluate": {"script": str},
}


@mcp.tool()
def webpipe_browser(
    url: Optional[str] = None,
    session_id: Optional[str] = None,
    actions: Optional[list[dict[str, Any]]] = None,
    navigate_url: Optional[str] = None,
    formats: Optional[list[str]] = None,
    scroll_to_bottom: Optional[bool] = None,
    max_scrolls: Optional[int] = None,
    scroll_wait: Optional[int] = None,
    scroll_step: Optional[int] = None,
    cookie: Optional[str] = None,
    proxy: Optional[str] = None,
    session_name: Optional[str] = None,
    auto_close: Optional[bool] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> str:
    """Persistent headless browser — create a session, run interactions, scrape the page.

    Use when the page needs JavaScript to render, or requires interaction:
    login, clicking, filling forms, dropdowns, screenshots, infinite scroll.

    HOW TO USE (three patterns):

    Pattern A — one-shot (most common). Omit session_id: a session is created
    at `url`, all `actions` run in order, the page is scraped, and the session
    closes automatically.
        webpipe_browser(url="https://example.com",
                        actions=[{"type": "click", "selector": "#more"},
                                 {"type": "wait", "ms": 500}],
                        formats=["markdown"])

    Pattern B — persistent session. Set auto_close=false: the session stays
    open and the response contains its session_id. Pass that session_id on
    the next call to continue operating on the SAME browser (state, cookies
    and login survive across calls). Close is automatic only when
    auto_close is left at true.
        # Call 1: open and keep alive
        webpipe_browser(url="https://app.example.com/login", auto_close=false,
                        actions=[{"type": "fill", "selector": "#user", "value": "me"},
                                 {"type": "fill", "selector": "#pass", "value": "***"},
                                 {"type": "click", "selector": "button[type=submit]"},
                                 {"type": "wait_for", "selector": ".dashboard"}])
        # Call 2: reuse the session_id from call 1's response
        webpipe_browser(session_id="<session_id from call 1>",
                        navigate_url="https://app.example.com/settings")

    Pattern C — reusable login state. Pass session_name together with
    auto_close=false: the session state is saved under that name so a later
    call can restore the logged-in browser without redoing the login.

    TIPS:
    - After click/fill that triggers loading, add {"type": "wait_for",
      "selector": "..."} or {"type": "wait", "ms": 800} before scraping.
    - Infinite-scroll pages: set scroll_to_bottom=true (optionally max_scrolls,
      scroll_wait, scroll_step) and the page is scrolled before the final scrape.
    - screenshots return per-step results inside "steps"; the final page
      content is under "final_page" (or inside the navigate step result).
    - actions run sequentially; a failing action records {"error": ...} for
      that step and the remaining actions still run.

    Args:
        url: Start URL used when creating the session (ignored if session_id is given).
        session_id: Existing session ID; skips creation and operates on that session.
        actions: Interaction list executed in order. Each item is like
            {"type": "click", "selector": "..."}; supported types:
            click(selector, wait_after?), fill(selector, value),
            select(selector, value), press(key, selector?, wait_after?),
            scroll(direction?, amount?), wait(ms), wait_for(selector, timeout?),
            hover(selector), check(selector), screenshot(full_page?),
            evaluate(expression).
        navigate_url: Navigate to this URL and scrape it (runs a navigate step).
        formats: Output formats of the final scrape. Default ["markdown"].
        scroll_to_bottom: Scroll to the bottom before scraping.
        max_scrolls: Max scroll rounds (<= 30).
        scroll_wait: Wait in milliseconds after each scroll.
        scroll_step: Pixels per scroll.
        cookie: Cookie string injected when creating the session.
        proxy: Custom proxy used when creating the session.
        session_name: Persistence name for saving/reusing a logged-in session.
        auto_close: Close the session when done. Default true; set false to
            keep it open and reuse session_id later.
        api_key: WebPipe API key.
        base_url: API base URL.

    Returns:
        JSON string, e.g.:
        {
          "session_id": "bs-3f8a2c",
          "steps": [
            { "index": 0, "type": "fill", "result": { "ok": true } },
            { "index": 1, "type": "click", "result": { "ok": true } }
          ],
          "final_page": {
            "markdown": "# Dashboard\\n\\n...",
            "html": null,
            "rawHtml": null,
            "links": null,
            "metadata": { "title": "Dashboard", "sourceURL": "https://app.example.com/", "statusCode": 200 },
            "json": null
          },
          "closed": true
        }
        "final_page" appears when the page was scraped without navigate_url;
        navigate results are nested under "steps". "closed" is false when
        auto_close=false (keep the session_id for the next call). On failure,
        an error string with recovery hints (see server instructions).
    """
    try:
        client = _get_client(api_key, base_url)
        formats = formats or ["markdown"]
        auto_close = True if auto_close is None else auto_close

        # --- Get / create session ---
        if session_id:
            session = _session_by_id(client, session_id)
        else:
            create_kwargs: dict[str, Any] = {}
            if url is not None:
                create_kwargs["url"] = url
            if cookie is not None:
                create_kwargs["cookie"] = cookie
            if proxy is not None:
                create_kwargs["proxy"] = proxy
            if session_name is not None:
                create_kwargs["session_name"] = session_name
            session = client.browser.create(**create_kwargs)
            session.wait_until_running()

        result: dict[str, Any] = {"session_id": session.id, "steps": []}

        # --- Navigate ---
        if navigate_url:
            nav_kwargs: dict[str, Any] = {"url": navigate_url, "formats": formats}
            if scroll_to_bottom is not None:
                nav_kwargs["scroll_to_bottom"] = scroll_to_bottom
            if max_scrolls is not None:
                nav_kwargs["max_scrolls"] = max_scrolls
            if scroll_wait is not None:
                nav_kwargs["scroll_wait"] = scroll_wait
            if scroll_step is not None:
                nav_kwargs["scroll_step"] = scroll_step
            doc = session.navigate(**nav_kwargs)
            result["steps"].append({"action": "navigate", "url": navigate_url, "result": doc.model_dump()})

        # --- Run actions list ---
        if actions:
            for i, act in enumerate(actions):
                atype = act.get("type")
                if not atype:
                    result["steps"].append({"index": i, "error": "missing `type` field"})
                    continue
                params = {k: v for k, v in act.items() if k != "type" and v is not None}
                try:
                    r = _dispatch_action(session, atype, params)
                    result["steps"].append({"index": i, "type": atype, "result": r})
                except Exception as ae:
                    result["steps"].append({"index": i, "type": atype, "error": str(ae)})

        # --- Final scrape (when there was no navigate and formats are requested) ---
        if not navigate_url and (formats or scroll_to_bottom or max_scrolls):
            scrape_kwargs: dict[str, Any] = {"formats": formats}
            if scroll_to_bottom is not None:
                scrape_kwargs["scroll_to_bottom"] = scroll_to_bottom
            if max_scrolls is not None:
                scrape_kwargs["max_scrolls"] = max_scrolls
            if scroll_wait is not None:
                scrape_kwargs["scroll_wait"] = scroll_wait
            if scroll_step is not None:
                scrape_kwargs["scroll_step"] = scroll_step
            doc = session.scrape(**scrape_kwargs)
            result["final_page"] = doc.model_dump()

        # --- Persist login state ---
        if session_name is not None:
            try:
                save_r = session.save_session()
                result["save_session"] = save_r
            except Exception:
                pass

        # --- Close ---
        if auto_close:
            try:
                session.close()
                result["closed"] = True
            except Exception:
                result["closed"] = False
        else:
            result["closed"] = False
            result["session_id"] = session.id

        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        tb = traceback.format_exc()
        return f"{_friendly_error(e)}\n\nDetails:\n{tb}"


def _dispatch_action(session, atype: str, params: dict[str, Any]) -> Any:
    """Dispatch an action dict to the matching SDK method. Strictly aligned with BrowserSession source."""
    if atype == "click":
        return session.click(params["selector"], wait_after=params.get("wait_after"))
    if atype == "fill":
        return session.fill(params["selector"], params["value"])
    if atype == "select":
        return session.select(params["selector"], params["value"])
    if atype == "press":
        return session.press(
            params["key"],
            selector=params.get("selector"),
            wait_after=params.get("wait_after"),
        )
    if atype == "scroll":
        return session.scroll(
            direction=params.get("direction", "down"),
            amount=params.get("amount"),
        )
    if atype == "wait":
        return session.wait(ms=params.get("ms", 1000))
    if atype == "wait_for":
        return session.wait_for(params["selector"], timeout=params.get("timeout"))
    if atype == "hover":
        return session.hover(params["selector"])
    if atype == "check":
        return session.check(params["selector"])
    if atype == "screenshot":
        return session.screenshot(full_page=params.get("full_page", False))
    if atype == "evaluate":
        return session.evaluate(params["expression"])
    # Unknown type -> fall back to the generic action passthrough
    return session.action(atype, **params)


def _session_by_id(client: WebpipeClient, session_id: str):
    """Restore a BrowserSession by session_id.

    Follows the SDK pattern strictly: BrowserResource.create() internally does
    BrowserSession(self._http, SessionInfo.model_validate(data)). Reuse the
    same constructor here.
    """
    from webpipe.browser import BrowserSession
    from webpipe.types import SessionInfo

    info_dict = client._http.get(f"/api/v1/browser/{session_id}")
    info = SessionInfo.model_validate(info_dict)
    return BrowserSession(client._http, info)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Default stdio mode for local debugging
    mcp.run(transport="stdio")
