"""
WebPipe MCP Server — 把 WebPipe API 包装成 AI Agent 可调用的 MCP 工具。

使用：
  # stdio 模式（本地开发/测试）
  python server.py

  # HTTP Streamable 模式（部署后给 Agent 连）
  fastmcp run server.py:mcp --transport streamable-http --host 0.0.0.0 --port 8080

认证：
  环境变量 WEBPIPE_API_KEY，或调用时显式传入 api_key 参数。
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
# MCP 实例
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="webpipe",
    instructions="WebPipe.ai 网页数据抓取 — Scrape / Map / Crawl / Browser Session",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

_API_KEY_ENV = "WEBPIPE_API_KEY"
_BASE_URL_ENV = "WEBPIPE_API_URL"


def _get_client(api_key: Optional[str] = None, base_url: Optional[str] = None) -> WebpipeClient:
    """构造 WebpipeClient：显式参数 > 环境变量 > 默认 base_url。"""
    key = api_key or os.environ.get(_API_KEY_ENV)
    if not key:
        raise ValueError(
            f"缺少 API Key。请设置环境变量 {_API_KEY_ENV} 或在调用时传入 api_key 参数。\n"
            f"去 WebPipe 控制台获取 Key：https://webpipe.ai/api-keys.html"
        )
    url = base_url or os.environ.get(_BASE_URL_ENV)  # SDK 默认 https://api.web2json.ai
    kwargs: dict[str, Any] = {"api_key": key}
    if url:
        kwargs["base_url"] = url
    return WebpipeClient(**kwargs)


def _friendly_error(e: Exception) -> str:
    """把 SDK 异常转成 Agent 友好的文字。"""
    if isinstance(e, AuthenticationError):
        return f"❌ API Key 无效或已过期：{e}"
    if isinstance(e, RateLimitError):
        return f"⚠️ 超出频率限制（429）：{e}"
    if isinstance(e, NotFoundError):
        return f"❌ 资源不存在或已过期：{e}"
    if isinstance(e, BadRequestError):
        return f"❌ 参数错误：{e}"
    if isinstance(e, ForbiddenError):
        return f"❌ 无权访问：{e}"
    if isinstance(e, (ServerError, CrawlError, PollTimeoutError)):
        return f"🔴 服务端错误：{e}"
    if isinstance(e, WebpipeError):
        return f"❌ WebPipe API 错误：{e}"
    try:
        import httpx

        if isinstance(e, httpx.TransportError):
            return (
                f"🌐 网络连接异常（SDK 已自动重试仍失败）：{type(e).__name__}: {e}\n"
                f"建议：稍等后重试；若持续出现，请检查本机网络/代理到 api.web2json.ai 的连通性。"
            )
    except ImportError:
        pass
    if isinstance(e, ValueError):
        return str(e)
    return f"❌ 未知错误：{type(e).__name__}: {e}"


def _plain(obj: Any) -> Any:
    """递归把 pydantic 模型 / list / dict 转成可 JSON 序列化的普通对象。

    SDK 的返回值既有单个模型（如 Document、CrawlJob），
    也有模型列表（如 map 返回 list[MapLink]），需要统一处理。
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, (list, tuple)):
        return [_plain(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    return obj


def _to_text(obj: Any) -> str:
    """把 SDK 返回值转成 Agent 能读的 JSON 字符串。"""
    return json.dumps(_plain(obj), ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# MCP 工具 1: webpipe_scrape
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
    """抓取单个网页，返回 Markdown / HTML / 链接 / 元数据 / 结构化 JSON。

    Args:
        url: 目标网页 URL（必填）
        formats: 输出格式列表，可选值：markdown, html, rawHtml, links, metadata, json。默认 ["markdown"]
        json_schema: 当 formats 含 "json" 时生效，JSON Schema 定义要提取的字段。支持 "x-source" 扩展
        use_proxy: 是否启用后端代理池（反爬场景）
        proxy: 自定义代理地址（优先级高于 use_proxy）
        cookie: Cookie 字符串（Header 格式）
        api_key: WebPipe API Key。不传则读环境变量 WEBPIPE_API_KEY
        base_url: API 基地址。默认 https://api.web2json.ai
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
# MCP 工具 2: webpipe_map
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
    """发现网站所有 URL（通过 robots.txt → sitemap → 页内链接），返回链接列表。

    Args:
        url: 目标网站根 URL（必填）
        limit: 返回数量上限，默认 5000，最大 50000
        search: 关键字过滤（只返回标题或描述包含该词的链接）
        sitemap: sitemap 处理策略：include / only / exclude（默认 include）
        same_domain: 是否只返回同域名链接，默认 true
        use_proxy: 是否启用后端代理池
        proxy: 自定义代理地址
        cookie: Cookie 字符串
        api_key: WebPipe API Key
        base_url: API 基地址
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
# MCP 工具 3: webpipe_crawl
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
    """递归爬取整个网站（异步任务，MCP Server 内部自动轮询到完成再返回）。

    与 SDK 行为一致：默认阻塞等待，直到爬完或超时。结果保留 24 小时。

    Args:
        url: 起始 URL（必填）
        limit: 爬取页面数量上限，默认 100，最大 10000
        poll_interval: 轮询间隔秒数，默认 2.0
        timeout: 总超时秒数，默认不限制
        max_discovery_depth: URL 发现深度
        include_paths: 正则数组，只爬匹配的路径
        exclude_paths: 正则数组，跳过匹配的路径
        allow_subdomains: 是否允许子域名，默认 false
        allow_external_links: 是否允许外链，默认 false
        crawl_entire_domain: 是否爬整个域名
        ignore_query_params: 是否忽略 URL 查询参数
        sitemap: sitemap 策略：include / skip / only
        delay: 每次请求延迟毫秒数，默认 0
        max_concurrency: 最大并发数 1-10，默认 3
        scrape_formats: 每页抓取的格式列表，默认 ["markdown"]
        use_proxy: 是否启用代理池
        proxy: 自定义代理
        cookie: Cookie 字符串
        api_key: WebPipe API Key
        base_url: API 基地址
    """
    try:
        client = _get_client(api_key, base_url)

        # 组装 crawl 的 options（**options 透传给 SDK）
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
# MCP 工具 4: webpipe_browser
# ---------------------------------------------------------------------------

# Browser Session 支持的 action 类型 + 参数定义
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
    """持久化无头浏览器 — 创建会话、执行交互操作、抓取页面内容。

    简单场景：不传 session_id → 自动创建会话 → 执行 actions → 自动关闭 → 返回结果。
    持久化场景：传 session_id → 在已有会话上继续操作。设置 auto_close=false 可保持会话。

    Args:
        url: 创建会话时的起始 URL（session_id 为空时生效）
        session_id: 已有会话 ID。传入则跳过创建，直接在该会话上操作
        actions: 交互操作列表，每项形如 {"type": "click", "selector": "..."}, 支持的 type:
                 click(selector, wait_after), fill(selector, value), select(selector, value),
                 press(key, selector?, wait_after?), scroll(direction?, amount?), wait(ms),
                 wait_for(selector, timeout?), hover(selector), check(selector),
                 screenshot(full_page?), evaluate(expression)
        navigate_url: 跳转到此 URL 并抓取（会触发 navigate 操作）
        formats: 最后 scrape 时的输出格式，默认 ["markdown"]
        scroll_to_bottom: 抓取前是否滚动到底部
        max_scrolls: 最大滚动次数（≤30）
        scroll_wait: 每次滚动等待毫秒数
        scroll_step: 每次滚动像素
        cookie: 创建会话时注入的 Cookie 字符串
        proxy: 创建会话时的自定义代理
        session_name: 会话持久化名称（配合 save_session 复用登录态）
        auto_close: 完成后是否自动关闭会话，默认 true。设为 false 可保持会话供后续调用
        api_key: WebPipe API Key
        base_url: API 基地址
    """
    try:
        client = _get_client(api_key, base_url)
        formats = formats or ["markdown"]
        auto_close = True if auto_close is None else auto_close

        # --- 获取 / 创建会话 ---
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

        # --- 执行 navigate ---
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

        # --- 执行 actions 列表 ---
        if actions:
            for i, act in enumerate(actions):
                atype = act.get("type")
                if not atype:
                    result["steps"].append({"index": i, "error": "缺少 type 字段"})
                    continue
                params = {k: v for k, v in act.items() if k != "type" and v is not None}
                try:
                    r = _dispatch_action(session, atype, params)
                    result["steps"].append({"index": i, "type": atype, "result": r})
                except Exception as ae:
                    result["steps"].append({"index": i, "type": atype, "error": str(ae)})

        # --- 最终 scrape（如果没 navigate 且有 formats）---
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

        # --- 持久化登录态 ---
        if session_name is not None:
            try:
                save_r = session.save_session()
                result["save_session"] = save_r
            except Exception:
                pass

        # --- 关闭 ---
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
        return f"{_friendly_error(e)}\n\n详情：{tb}"


def _dispatch_action(session, atype: str, params: dict[str, Any]) -> Any:
    """把 action dict 分发到 SDK 的对应方法。严格对齐 BrowserSession 源码。"""
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
    # 未知类型 → 走通用 action 兜底
    return session.action(atype, **params)


def _session_by_id(client: WebpipeClient, session_id: str):
    """通过 session_id 恢复 BrowserSession。

    严格遵循 SDK 模式：BrowserResource.create() 内部也是
    BrowserSession(self._http, SessionInfo.model_validate(data))。
    这里直接复用同样的构造器。
    """
    from webpipe.browser import BrowserSession
    from webpipe.types import SessionInfo

    info_dict = client._http.get(f"/api/v1/browser/{session_id}")
    info = SessionInfo.model_validate(info_dict)
    return BrowserSession(client._http, info)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 默认 stdio 模式，方便本地调试
    mcp.run(transport="stdio")
