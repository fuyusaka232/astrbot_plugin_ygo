# 游戏王助手（astrbot_plugin_ygo）

> GitHub: https://github.com/rentaoyan0628-collab/astrbot_plugin_ygo

基于 AstrBot 的游戏王主题插件：卡牌效果查询、MC 前 100 卡组、每日一卡、卡片裁定问答。

## 指令

统一入口 `/ygo`，中文别名 `/游戏王`。

| 指令 | 说明 |
| --- | --- |
| `/ygo card <卡名或卡号>` | 查卡；纯数字按卡号查询 |
| `/ygo mc` | MC 前 100 的第 1 页（1–10 名） |
| `/ygo mc <1-10>` | 查看指定页 |
| `/ygo mc <关键词>` | 按卡组名或类型筛选前 100 |
| `/ygo mc stats` | MC 类型分布 |
| `/ygo mc link <排名>` | 查看指定排名的卡表链接 |
| `/ygo daily` | 查看今天的每日一卡（不推送） |
| `/ygo daily now` | 立即把今天的卡推到当前会话 |
| `/ygo daily target` | 管理员：把当前会话加入推送目标 |
| `/ygo daily target clear` | 管理员：清空推送目标 |
| `/ygo daily targets` | 查看推送目标 |
| `/ygo daily push` | 管理员：推送到所有目标 |
| `/ygo ruling <问题>` | 裁定问答 |
| `/ygo ruling <卡名或卡号> :: <问题>` | 带卡文上下文的裁定问答 |
| `/ygo help` | 帮助 |

示例：

```text
/ygo card 青眼白龙
/ygo card 89631139
/ygo mc
/ygo mc 3
/ygo mc 城之内
/ygo mc stats
/ygo daily now
/ygo ruling 青眼白龙 :: 这张卡被效果破坏时能发动吗
```

## 文件结构

```text
astrbot_plugin_ygo/
├─ main.py            # Star 注册、命令路由、权限、限频、消息发送
├─ api_ygocdb.py      # ygocdb 卡查/按 ID 查询/随机卡/重试
├─ api_mc.py          # MC 前 100 分页、聚合、类型统计
├─ formatter.py       # 卡牌、卡组、统计的纯文本格式化
├─ ai_ruling.py       # AstrBot Provider / OpenAI 兼容接口
├─ state_store.py     # state.json 原子读写（每日目标、历史）
├─ metadata.yaml      # 插件元数据
├─ _conf_schema.json  # 可视化配置项
├─ requirements.txt   # aiohttp
└─ README.md
```

## 配置

AstrBot 插件配置页会自动读取 `_conf_schema.json`。

- `api.ygocdb_base`：默认 `https://ygocdb.com/api/v0`
- `api.mc_base`：默认 `https://ygo.xyk.one/mc/api`
- `api.timeout_seconds` / `api.retries`：网络超时与重试
- `api.cache_ttl_seconds`：卡查缓存，默认 6 小时
- `api.mc_cache_ttl_seconds`：MC 缓存，默认 10 分钟
- `daily.timezone`：每日一卡的日期时区，默认 `Asia/Shanghai`
- `daily.avoid_recent_days`：避免最近 N 天重复
- `daily.max_chars`：单条输出最大字符数
- `ai.mode`：`astrbot`（默认，复用当前会话模型）或 `openai`
- `ai.base_url` / `ai.api_key` / `ai.model`：独立 OpenAI 兼容接口
- `ai.timeout_seconds` / `ai.max_question_chars`
- `limits.max_search_results` / `cooldown_seconds` / `ruling_cooldown_seconds`

## 关键实现

### 卡查

- `^\d{1,10}$` 走 `/card/<id>`，否则 `?search=<query>`。
- 搜索结果优先精确匹配中文名/日文名；否则列出候选（卡号 + 名称），提示用卡号查看详情。
- 超时、HTTP 错误、JSON 解析失败统一返回“卡牌数据库暂时不可用”。
- 结果按查询词缓存，避免重复请求。

### MC 前 100

- 站点本身是 Astro 前端，插件直接使用其内部 JSON 接口：
  - `/mc/api/leaderboard?page=1..10`
  - `/mc/api/deck-stats`
- 并发拉 10 页并缓存；每页 10 条，共 100 条。
- 支持按页码、卡组名/类型关键词过滤、类型统计和卡表链接。
- MC 的 `deck` 只含卡组名、类型标签和 `deck.ourygo.top` 链接，不含完整卡表；插件不抓取外站卡表。

### 每日一卡

- ygocdb 没有随机接口，使用日期种子 + 卡号范围 `1..20000` 随机，最多 8 次重试；失败则从常见关键词的搜索结果里兜底。
- 同一天多次查看返回同一张卡；记录最近 N 天历史避免连续重复。
- 按需求只做**手动触发**：`/ygo daily now` 推当前会话，`/ygo daily push` 推管理员配置的目标列表；没有后台定时任务。

### 裁定问答

- `ai.mode=astrbot`：调用 `context.get_using_provider(umo)`，复用当前会话的 DeepSeek 模型，不需要额外 Key。
- `ai.mode=openai`：请求 `base_url + /chat/completions`，适配 DeepSeek 等 OpenAI 兼容接口。
- Prompt 要求区分“有明确依据”和“不确定”，禁止编造 FAQ/判例编号。
- 支持 `卡名 :: 问题` 把 ygocdb 卡文一并交给模型。

## 异常与权限

- 卡名/关键词/问题都有长度限制；页码限定 1–10。
- 普通指令和裁定分别限频，避免刷接口。
- `/ygo daily target`、`/ygo daily target clear`、`/ygo daily push` 仅管理员可用（AstrBot `event.is_admin()`）。
- 网络异常、空结果、AI 未配置都会返回中文提示，不抛裸异常。

## 安装

把目录放到 AstrBot 插件目录：

```text
/AstrBot/data/plugins/astrbot_plugin_ygo
```

依赖：

```bash
pip install -r requirements.txt
```

重启 AstrBot 或在插件页重载。当前已在 AstrBot 4.29.3 上加载并完成接口测试。

## License

MIT

