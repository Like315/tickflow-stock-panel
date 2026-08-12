# 美股看板实现计划

> 日期：2026-08-10
> 设计依据：`docs/superpowers/specs/2026-08-10-us-market-dashboard-design.md`

## 目标

在不改动现有 A 股行情、选股、回测和监控主链路的前提下，新增一个独立的美股市场看板。看板继续使用现有 TickFlow 数据源：有美股实时权限时展示实时市场宽度和排行榜；没有权限时优先展示最近一次聚合快照，首次运行则展示 ETF 日线代理行情。

## 实施约束

- 不扩展现有 `AssetType`，不把 1.2 万只美股写入现有 Parquet 数据仓库。
- 后端仅缓存和持久化聚合结果，不持久化全市场原始报价。
- TickFlow 百分比字段在后端统一按小数处理，前端显示时乘以 100。
- 美股使用绿色上涨、红色下跌，与现有 A 股页面的颜色语义隔离。
- 所有数据降级都必须明确标识，不能用零值伪装成真实市场数据。
- 不自动提交或推送 Git。

## 任务 1：后端聚合服务

新增 `backend/app/services/us_market_overview.py`。

实现内容：

1. 定义基准 ETF（SPY、QQQ、DIA、IWM）和 11 个行业 ETF。
2. 将 TickFlow 实时报价归一化为稳定内部结构：
   - 代码、名称、最新价、前收、涨跌额、涨跌幅；
   - 成交量、成交额、时间戳和交易时段；
   - 成交额缺失时使用 `last_price * volume` 估算，并标记估算状态。
3. 从 `US_Equity` 实时全市场报价计算：
   - 上涨、下跌、平盘和有效样本数；
   - 涨跌幅区间分布；
   - 涨幅榜、跌幅榜和成交活跃榜；
   - 基准 ETF 与行业 ETF 表现。
4. 排行榜过滤异常数据：价格、前收和涨跌幅必须有限；涨跌榜要求价格不低于 1 美元；成交榜要求成交量大于 0。
5. 实现四级数据策略：
   - 有权限：拉取实时全市场并生成 `live` 聚合结果；
   - 实时失败且有本地快照：返回 `snapshot`；
   - 没有快照：按服务端实际限制每组 5 个标的拉取 ETF 实时报价，生成 `partial` 结果；
   - 没有实时报价权限：使用 TickFlow 单标的日线接口生成 ETF `partial` 结果（免费档不开放批量日线端点）。
6. 在 `data/us_market/overview_snapshot.json` 原子写入实时聚合快照。
7. 使用条件变量实现单飞刷新，网络请求期间不持有状态锁。
8. 实时结果缓存 15 秒；快照和日线降级结果缓存 5 分钟。
9. 对外返回深拷贝，避免调用方修改服务缓存。

## 任务 2：后端 API 与生命周期

新增 `backend/app/api/us_market.py`，并修改 `backend/app/main.py`。

接口：

- `GET /api/us-market/overview`：读取当前缓存或按策略刷新。
- `POST /api/us-market/refresh`：强制发起一次刷新。

集成要求：

1. 在应用 lifespan 中初始化并挂载 `UsMarketOverviewService`。
2. 注册美股路由。
3. 服务完全不可用时返回 HTTP 503 和稳定的中文错误信息。
4. API 响应不暴露 TickFlow 密钥、SDK 异常栈或原始全市场报价。

## 任务 3：前端页面

新增 `frontend/src/pages/UsMarketDashboard.tsx`，修改：

- `frontend/src/lib/api.ts`
- `frontend/src/lib/queryKeys.ts`
- `frontend/src/router.tsx`
- `frontend/src/components/Layout.tsx`

页面结构：

1. 顶部状态栏：数据模式、交易时段、纽约时间、北京时间、手动刷新按钮。
2. 基准卡片：SPY、QQQ、DIA、IWM。
3. 市场宽度：有效样本数、上涨/下跌/平盘数量和占比。
4. 涨跌分布：使用现有 Tailwind/CSS 绘制简单横条，不新增图表依赖。
5. 行业 ETF 表现列表。
6. 涨幅榜、跌幅榜和成交活跃榜。
7. `partial` 模式不显示虚假的市场宽度和排行榜，改为明确的不可用提示。
8. 页面使用绿色表示上涨、红色表示下跌，不复用 A 股 `bull/bear` 颜色类。
9. 查询默认每 30 秒刷新；手动刷新调用强制刷新接口并更新查询缓存。

## 任务 4：测试与验证

新增 `backend/tests/test_us_market_overview.py`，覆盖：

1. TickFlow 实时报价字段归一化和百分比单位。
2. 市场宽度、分布与排行榜计算。
3. 无实时权限时通过单标的日线接口完成 `partial` 降级。
4. 实时失败时读取本地聚合快照。
5. 快照文件损坏时继续尝试日线降级。
6. 返回值修改不会污染内部缓存。

执行验证：

```powershell
cd D:\tickflow-stock\backend
.\.venv\Scripts\python.exe -m pytest tests\test_us_market_overview.py -q
.\.venv\Scripts\python.exe -m ruff check app\services\us_market_overview.py app\api\us_market.py tests\test_us_market_overview.py

cd D:\tickflow-stock\frontend
pnpm build

cd D:\tickflow-stock
git diff --check
git status --short
```

如果本地服务可正常启动，再访问 `/us-market` 和 `/api/us-market/overview` 做一次真实 TickFlow 降级链路检查。

## 完成标准

- 新页面能从导航进入并正常构建。
- 有实时权限时能显示全市场宽度和排行榜。
- 没有实时权限时页面仍可用，并明确显示快照或 ETF 日线代理状态。
- 不保存原始全市场报价，不影响现有 A 股页面与接口。
- 聚焦测试、静态检查、前端构建和差异检查均通过；无法执行的验证需如实记录。
