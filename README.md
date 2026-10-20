# WebPipe MCP Server

把 [WebPipe](https://webpipe.ai) 的网页数据能力封装成 MCP 工具，让 AI Agent 能直接抓取网页、发现站点链接、递归爬取整站，以及驱动无头浏览器交互。

## 项目结构

```text
webpipe-mcp/
├── server.py          # MCP Server 主文件（4 个工具）
└── requirements.txt   # 依赖声明
```

## 安装

```bash
pip install -r webpipe-mcp/requirements.txt
```

依赖：`fastmcp`、`webpipe-sdk`。

## 配置 API Key

在 https://webpipe.ai/dashboard/api-keys 获取 Key，通过环境变量设置：

```bash
# Windows PowerShell
$env:WEBPIPE_API_KEY = "wc-xxxxxxxx"

# macOS / Linux
export WEBPIPE_API_KEY="wc-xxxxxxxx"
```

也可以在每次调用时显式传 `api_key` 参数（优先级高于环境变量）。

可选环境变量 `WEBPIPE_API_URL` 可覆盖 API 基地址（默认 `https://api.web2json.ai`）。

## 运行

### stdio 模式（本地客户端，如 Claude Desktop / Codex）

```bash
python webpipe-mcp/server.py
```

### HTTP Streamable 模式（部署后供远程 Agent 连接）

```bash
fastmcp run webpipe-mcp/server.py:mcp --transport streamable-http --host 0.0.0.0 --port 8080
```

## 工具

### webpipe_scrape

抓取单个网页，支持 Markdown / HTML / 链接 / 元数据 / 结构化 JSON。

| 参数 | 类型 | 说明 |
|---|---|---|
| `url` | string | 目标网页 URL（必填） |
| `formats` | string[] | 输出格式：`markdown`、`html`、`rawHtml`、`links`、`metadata`、`json`，默认 `["markdown"]` |
| `json_schema` | object | `formats` 含 `json` 时生效，定义要抽取的字段，支持 `x-source` 扩展 |
| `use_proxy` | bool | 启用后端代理池（反爬场景） |
| `proxy` | string | 自定义代理地址（优先级高于 `use_proxy`） |
| `cookie` | string | Cookie 字符串（Header 格式） |
| `api_key` | string | API Key，不传则读环境变量 |
| `base_url` | string | API 基地址 |

### webpipe_map

发现网站所有 URL（robots.txt → sitemap → 页内链接），返回链接列表。

| 参数 | 类型 | 说明 |
|---|---|---|
| `url` | string | 站点根 URL（必填） |
| `limit` | int | 返回数量上限，默认 5000，最大 50000 |
| `search` | string | 关键字过滤（只返回标题或描述包含该词的链接） |
| `sitemap` | string | sitemap 策略：`include` / `only` / `exclude`，默认 `include` |
| `same_domain` | bool | 是否只返回同域名链接，默认 `true` |
| `use_proxy` / `proxy` / `cookie` / `api_key` / `base_url` | | 同上 |

### webpipe_crawl

递归爬取整个网站。内部自动轮询到爬取完成再返回（与 SDK 的阻塞行为一致），结果保留 24 小时。

| 参数 | 类型 | 说明 |
|---|---|---|
| `url` | string | 起始 URL（必填） |
| `limit` | int | 页面数量上限，默认 100，最大 10000 |
| `poll_interval` | float | 轮询间隔秒数，默认 2.0 |
| `timeout` | float | 总超时秒数，默认不限制 |
| `max_discovery_depth` | int | URL 发现深度 |
| `include_paths` / `exclude_paths` | string[] | 正则数组，只爬 / 跳过匹配的路径 |
| `allow_subdomains` | bool | 是否允许子域名，默认 `false` |
| `allow_external_links` | bool | 是否允许外链，默认 `false` |
| `crawl_entire_domain` | bool | 是否爬整个域名 |
| `ignore_query_params` | bool | 是否忽略 URL 查询参数 |
| `sitemap` | string | sitemap 策略：`include` / `skip` / `only` |
| `delay` | int | 每次请求延迟毫秒数，默认 0 |
| `max_concurrency` | int | 最大并发数 1-10，默认 3 |
| `scrape_formats` | string[] | 每页抓取的格式列表，默认 `["markdown"]` |
| `use_proxy` / `proxy` / `cookie` / `api_key` / `base_url` | | 同上 |

### webpipe_browser

无头浏览器会话：创建 → 交互 → 抓取。

三种用法：

- 不传 `session_id`：自动创建会话 → 执行 actions → 自动关闭 → 返回结果
- 传 `session_id`：在已有会话上继续操作
- `auto_close=false`：保持会话不关闭，返回 `session_id` 供后续复用

| 参数 | 类型 | 说明 |
|---|---|---|
| `url` | string | 创建会话时的起始 URL（`session_id` 为空时生效） |
| `session_id` | string | 已有会话 ID，传入则跳过创建 |
| `actions` | object[] | 交互操作列表，见下表 |
| `navigate_url` | string | 跳转到该 URL 并抓取 |
| `formats` | string[] | 最终 scrape 的输出格式，默认 `["markdown"]` |
| `scroll_to_bottom` | bool | 抓取前是否滚动到底部 |
| `max_scrolls` | int | 最大滚动次数（≤30） |
| `scroll_wait` | int | 每次滚动等待毫秒数 |
| `scroll_step` | int | 每次滚动像素 |
| `cookie` | string | 创建会话时注入的 Cookie |
| `proxy` | string | 创建会话时的自定义代理 |
| `session_name` | string | 会话持久化名称（配合 save_session 复用登录态） |
| `auto_close` | bool | 完成后是否自动关闭会话，默认 `true` |
| `api_key` / `base_url` | | 同上 |

`actions` 支持的 `type`：

| type | 参数 |
|---|---|
| `click` | `selector`, `wait_after`? |
| `fill` | `selector`, `value` |
| `select` | `selector`, `value` |
| `press` | `key`, `selector`?, `wait_after`? |
| `scroll` | `direction`?, `amount`? |
| `wait` | `ms` |
| `wait_for` | `selector`, `timeout`? |
| `hover` | `selector` |
| `check` | `selector` |
| `screenshot` | `full_page`? |
| `evaluate` | `expression` |

## 错误处理

所有 SDK 异常都会转成可读文字作为工具返回值（不抛异常，避免中断 Agent）：

| 场景 | 返回 |
|---|---|
| API Key 无效或过期 | `❌ API Key 无效或已过期` |
| 触发限流 | `⚠️ 超出频率限制（429）` |
| 资源不存在 | `❌ 资源不存在或已过期` |
| 参数错误 | `❌ 参数错误` |
| 无权访问 | `❌ 无权访问` |
| 服务端错误 | `🔴 服务端错误` |
| 网络异常（SDK 已自动重试仍失败） | `🌐 网络连接异常` |

## 接入 MCP 客户端

以 Claude Desktop 为例，编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "webpipe": {
      "command": "python",
      "args": ["<绝对路径>/webpipe-mcp/server.py"],
      "env": {
        "WEBPIPE_API_KEY": "wc-xxxxxxxx"
      }
    }
  }
}
```

## 实现说明

- `webpipe_browser` 的 `session_id` 复用严格遵循 webpipe-sdk 的构造方式：`BrowserSession(http, SessionInfo.model_validate(data))`，与 SDK 内部 `BrowserResource.create()` 完全一致
- 4 个工具均已通过真实 API 验证，包含 `session_id` 会话恢复
