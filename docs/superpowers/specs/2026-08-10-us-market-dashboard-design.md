# 美股看板设计

日期：2026-08-10

## 1. 目标与范围

新增独立的美股市场看板，复用当前 TickFlow 凭证和 SDK 能力，为用户提供美股市场概览，不改变现有 A 股数据口径和功能链路。

首版包含：

- SPY、QQQ、DIA、IWM 四个指数 ETF 代理；页面明确标注为 ETF 代理，不冒充官方指数。
- 全市场涨跌家数、平均涨跌幅、中位涨跌幅、强势/弱势数量和涨跌分布。
- 涨幅榜、跌幅榜和成交活跃榜。
- 11 个美股行业 ETF 的表现排行。
- 实时、缓存快照和部分降级三种数据状态。
- 美股惯例的绿涨红跌配色。

首版不包含：

- 美股自选、选股、回测、监控和财务分析。
- 全量美股日 K 落盘、指标计算或 A 股盘后流水线改造。
- 盘前和盘后单独排行；首版按 TickFlow 返回的当前行情快照展示，并显示 session 状态。
- 用户自定义标的池或行业分类。

## 2. 架构与模块边界

数据流：

```text
TickFlow US_Equity / 美股 ETF
  -> UsMarketOverviewService
  -> 内存缓存 + data/us_market/overview_snapshot.json
  -> GET /api/us-market/overview
  -> POST /api/us-market/refresh
  -> /us-market 美股看板
```

### 2.1 后端服务

新增 `backend/app/services/us_market_overview.py`：

- 通过 `app.tickflow.client.get_client()` 复用现有 TickFlow 客户端和密钥存储。
- 调用 `quotes.get_by_universes(["US_Equity"])` 获取全市场行情。
- 单独查询指数 ETF 与行业 ETF，避免依赖它们是否出现在全市场快照中。
- 把 TickFlow 的嵌套 `ext` 字段规范化为内部行结构。
- 在服务层完成聚合，只向 API 返回摘要和有限榜单。
- 使用互斥锁和 single-flight 状态避免并发请求重复刷新。
- 成功刷新后先写临时文件，再原子替换摘要快照。

该服务是明确的 TickFlow 美股专用能力，不扩展现有 `AssetType`，不让美股数据进入 A 股 provider、repository、enriched 或策略链路。

### 2.2 API

新增 `backend/app/api/us_market.py`：

- `GET /api/us-market/overview`：返回当前内存缓存；缓存过期时触发一次同步刷新。
- `POST /api/us-market/refresh`：手动强制刷新；并发刷新存在时复用正在进行的结果，不再发起第二次请求。
- API 层不直接调用 TickFlow，不包含聚合业务逻辑。

在 `backend/app/main.py` 中注册路由，并在 lifespan 中创建服务、挂载到 `app.state.us_market_overview_service`。关闭应用时不需要额外后台线程清理，因为首版采用按需刷新。

### 2.3 前端

- 新增 `frontend/src/pages/UsMarketDashboard.tsx`。
- 在 `frontend/src/router.tsx` 中懒加载 `/us-market`。
- 在 `frontend/src/components/Layout.tsx` 中新增“美股看板”入口。
- 在 `frontend/src/lib/api.ts` 中维护响应类型和请求方法。
- 在 `frontend/src/lib/queryKeys.ts` 中集中维护查询键。

## 3. 页面布局

页面采用现有看板的紧凑卡片视觉语言，但不复用 A 股涨停、连板、概念和情绪评分组件。

从上到下：

1. 标题栏：`美股看板`、数据状态、纽约市场 session、数据时间、北京时间、刷新按钮。
2. 指数代理卡：SPY、QQQ、DIA、IWM，显示名称、价格、涨跌幅和成交量。
3. 市场宽度 KPI：上涨/平盘/下跌、平均涨跌、中位涨跌、强势/弱势、有效样本数。
4. 中部两栏：涨跌分布条形图、11 个行业 ETF 表现排行。
5. 底部三栏：涨幅榜、跌幅榜、成交活跃榜。

响应式要求：

- 宽屏保持三栏榜单与双栏中部区域。
- 窄屏降为单列，表格允许横向滚动。
- 加载、空数据、错误和降级状态均有明确文本，不显示伪造的零值。

## 4. 标的与字段口径

### 4.1 指数 ETF 代理

| Symbol | 展示名称 |
| --- | --- |
| `SPY.US` | 标普 500 ETF |
| `QQQ.US` | 纳斯达克 100 ETF |
| `DIA.US` | 道琼斯 ETF |
| `IWM.US` | 罗素 2000 ETF |

### 4.2 行业 ETF

| Symbol | 行业 |
| --- | --- |
| `XLK.US` | 信息技术 |
| `XLC.US` | 通信服务 |
| `XLY.US` | 可选消费 |
| `XLP.US` | 必需消费 |
| `XLF.US` | 金融 |
| `XLV.US` | 医疗保健 |
| `XLI.US` | 工业 |
| `XLE.US` | 能源 |
| `XLB.US` | 原材料 |
| `XLRE.US` | 房地产 |
| `XLU.US` | 公用事业 |

### 4.3 行情归一化

规范化字段：

- `symbol`、`name`
- `last_price`、`prev_close`
- `open`、`high`、`low`
- `volume`、`amount`
- `change_amount`、`change_pct`
- `timestamp`、`session`

TickFlow `ext.change_pct` 按项目现有契约视为小数制，例如 `0.0366` 表示 `3.66%`。若缺失，则只在 `last_price` 和非零 `prev_close` 都有效时计算 `(last_price - prev_close) / prev_close`。服务端响应继续保持小数制，前端仅在展示时乘 100。

若 `amount` 缺失或不大于零，成交活跃榜使用 `last_price * volume` 作为估算成交额，并在响应中用 `amount_estimated: true` 标记；不能把估算值标记为精确成交额。

### 4.4 样本过滤

- 市场宽度纳入 `US_Equity` 中 `last_price > 0`、`prev_close > 0` 且涨跌幅有限的行情。
- 榜单额外要求 `volume > 0`、名称和 symbol 存在。
- 为避免极低价股票长期占据榜首，涨跌榜要求 `last_price >= 1 USD`。
- 强势定义为涨幅不低于 `+2%`，弱势定义为跌幅不高于 `-2%`。
- 平盘使用绝对涨跌幅小于 `0.005%`；其余分别计入上涨和下跌。
- 不对数据源返回的 `US_Equity` 再按代码形态猜测 NYSE、NASDAQ 或 AMEX。

## 5. API 响应

`GET /api/us-market/overview` 返回：

```json
{
  "status": "live",
  "source": "tickflow",
  "as_of": 1786075200000,
  "market_timezone": "America/New_York",
  "market_time": "2026-08-07T16:00:00-04:00",
  "beijing_time": "2026-08-08T04:00:00+08:00",
  "session": "regular",
  "stale": false,
  "message": null,
  "benchmarks": [],
  "breadth": {},
  "distribution": [],
  "sectors": [],
  "top_gainers": [],
  "top_losers": [],
  "active_leaders": []
}
```

`status` 取值：

- `live`：本次请求成功获得 TickFlow 实时行情。
- `snapshot`：实时请求失败，返回最近一次成功摘要快照。
- `partial`：没有全市场快照，只能返回通过历史日 K 获得的指数/行业 ETF 数据；市场宽度和三个榜单为空。

`stale` 只表示响应来自历史快照或其数据时间明显早于当前 session，不把休市后的最后收盘行情错误标记为盘中实时。

## 6. 缓存、快照与降级

- 实时内存缓存 TTL 为 15 秒。
- 休市或 snapshot 状态缓存 TTL 为 5 分钟。
- 用户点击刷新走 `POST /api/us-market/refresh`，仍受 single-flight 保护。
- 成功获得全市场行情后，仅持久化聚合结果和有限榜单，不持久化约 1.2 万条原始行情。
- 快照路径固定为 `data/us_market/overview_snapshot.json`，写入使用临时文件与 `Path.replace()` 原子替换。
- 快照读取失败时记录 warning 并继续尝试 TickFlow，不让损坏快照阻止应用启动。
- 实时权限不足、网络失败或限流时优先返回内存中的上次成功数据，其次返回磁盘快照。
- 首次运行且无可用快照时，先按每组 5 个标的获取指数 ETF 和行业 ETF 实时报价；若无实时报价权限，再通过 TickFlow 单标的日 K 接口逐个获取最近两根日线（免费档不开放批量日线端点）。两种情况均返回 `partial`，全市场宽度和榜单明确为空并解释原因。
- 如果实时和日 K 都失败，API 返回可识别的服务不可用错误，不返回全零看板。

## 7. 时间与交易状态

- 美股时间使用 `America/New_York`，北京时间使用 `Asia/Shanghai`。
- `as_of` 取响应中有效行情的最大 timestamp。
- `session` 优先使用 TickFlow 返回的 session；不同标的 session 不一致时，使用有效行情中出现次数最多的值。
- 不用服务器本地时间推断美股是否开盘，也不自行维护美国节假日日历。
- 前端同时显示纽约时间和北京时间，避免跨日误读。

## 8. 错误处理与安全

- 不在日志、响应或快照中写入 TickFlow API Key。
- TickFlow 权限错误、限流、超时和空响应分别记录简洁原因，前端展示用户可理解的降级说明。
- 原始上游异常文本不直接返回浏览器，避免泄漏请求细节。
- 非有限数字在服务端转换为 `null`，确保响应为合法 JSON。
- 快照 schema 带 `schema_version: 1`；未来字段变化时允许忽略未知字段，缺少必填字段则拒绝读取并重新刷新。

## 9. 验证标准

后端测试：

- 行情嵌套字段归一化及 `change_pct` 小数口径。
- 宽度、涨跌分布、强弱计数和榜单排序。
- 低价股榜单过滤、无效价格过滤和估算成交额标记。
- 指数/行业 ETF 映射。
- live、snapshot、partial 和完全失败四条路径。
- TTL 命中、强制刷新、并发 single-flight 和原子快照读写。
- 纽约时间、北京时间及 session 聚合。
- API 成功、降级和错误响应。

前端验证：

- `pnpm build`。
- 页面加载、空数据、错误、snapshot、partial、手动刷新状态。
- 绿涨红跌、ETF 代理标签和估算成交额提示。
- 常用桌面宽度及窄屏布局。

仓库验证：

- 后端定向 `pytest`。
- 新增 Python 文件定向 `ruff check`。
- `git diff --check`。
- 最终状态不包含密钥、运行数据、日志或无关生成文件。

## 10. 成功标准

- 用户可从侧边栏进入独立美股看板。
- 有 TickFlow 实时权限时，可看到全市场宽度、行业 ETF 和三个排行榜。
- 无实时权限或网络异常时，页面明确展示快照或部分降级状态，不误报为实时。
- A 股看板、存储、策略、回测和监控行为不变。
- 全市场原始行情不发送到前端、不写入现有 Parquet，也不暴露 API Key。
