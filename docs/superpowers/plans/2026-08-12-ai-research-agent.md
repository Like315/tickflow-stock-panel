# AI 研究 Agent Implementation Plan

> **执行约束：** 本仓库明确不默认使用 `superpowers:*`，因此本计划由当前会话内联执行；每个批次以实际测试结果作为完成门槛，不自动提交或推送 Git。

**Goal:** 在全局顶部交付一个面向 A 股的 AI 研究 Agent，支持术语解释、指定股票分析、每日最多 5 只选股，以及对历史推荐的逐交易日复盘。

**Architecture:** 后端新增独立 research-agent 领域模块：知识库提供确定性解释，筛选器通过现有 `KlineRepository` 构建候选和证据，AI 服务复用 `ai_provider`，SQLite Store 保存不可变推荐及追加复盘。前端复用现有全局流式任务模式，在 `Layout` 顶部增加紧凑入口并通过右侧抽屉承载问答、选股和复盘。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、Polars、SQLite；React 18、TypeScript、TanStack Query、Tailwind CSS。

## Global Constraints

- 首版只覆盖 A 股，默认保守型，研究周期为 5～20 个交易日。
- 每日正式推荐最多 5 只；证据不足时允许少于 5 只，禁止凑数。
- 量化预筛只缩小候选范围，不能成为固定买卖规则。
- 所有行情通过 `KlineRepository` 获取，不在 API 或 Agent 服务中直接调用 TickFlow。
- 指标和收益使用前复权价格，窗口使用实际交易日；涨跌幅内部保持小数制。
- 原始推荐不可修改；重新分析创建新版本；复盘以唯一键幂等追加。
- AI、公告或部分数据失败时明确降级，不影响现有行情流水线。
- 不接入券商或自动交易，不记录密钥，不提交运行时数据库。
- 保留工作区已有美股看板改动，不格式化或重构无关代码。

---

### Task 1: 研究术语知识库与公共数据契约

**Files:**
- Create: `backend/app/services/research_agent_terms.py`
- Create: `backend/app/services/research_agent_models.py`
- Test: `backend/tests/test_research_agent_terms.py`

**Interfaces:**
- Produces: `list_terms() -> list[ResearchTerm]`
- Produces: `find_term(query: str) -> ResearchTerm | None`
- Produces: Pydantic models `RecommendationPick`, `RecommendationBatch`, `DailyReview`

- [x] 建立中文术语字典，覆盖现有指标和信号别名。
- [x] 每条知识包含定义、解读、限制和组合观察建议。
- [x] 建立严格的推荐、证据、复盘 Pydantic 契约，并限制倾向、置信度和阶段状态枚举。
- [x] 验证精确术语、别名、包含问法和未知术语路径。

Run:

```powershell
cd backend
uv run pytest tests/test_research_agent_terms.py -q
uv run ruff check app/services/research_agent_terms.py app/services/research_agent_models.py tests/test_research_agent_terms.py
```

Expected: all tests pass and Ruff reports no errors.

### Task 2: 不可变推荐与幂等复盘存储

**Files:**
- Create: `backend/app/services/research_agent_store.py`
- Test: `backend/tests/test_research_agent_store.py`

**Interfaces:**
- Consumes: validated dictionaries from `research_agent_models.py`
- Produces: `ResearchAgentStore(data_dir: Path)`
- Produces: `save_batch`, `latest_batch`, `list_batches`, `save_daily_review`, `list_reviews`, `record_run`, `get_status`

- [x] 使用 `data/user_data/ai_research_agent.db` 和显式事务初始化 schema。
- [x] 为推荐批次、批次内股票、每日复盘、阶段复盘和运行状态建立表与唯一约束。
- [x] 同一批次写入必须原子完成；完成后的推荐记录不能更新。
- [x] 相同 `batch_id + symbol + trade_date` 的复盘执行幂等 upsert。
- [x] 测试回滚、版本关系、分页、唯一约束和数据库路径。

Run:

```powershell
cd backend
uv run pytest tests/test_research_agent_store.py -q
uv run ruff check app/services/research_agent_store.py tests/test_research_agent_store.py
```

Expected: all tests pass; interrupted batch leaves no partial picks.

### Task 3: 全 A 股候选预筛与证据聚合

**Files:**
- Create: `backend/app/services/research_agent_screening.py`
- Create: `backend/app/services/research_agent_evidence.py`
- Test: `backend/tests/test_research_agent_screening.py`
- Test: `backend/tests/test_research_agent_evidence.py`

**Interfaces:**
- Consumes: `KlineRepository.get_enriched_latest()`, `get_enriched_range()`, `get_instruments()`, `get_name_map()`
- Produces: `screen_candidates(repo, limit=25) -> CandidateScreenResult`
- Produces: `build_stock_evidence(repo, symbol, as_of=None) -> StockEvidence`

- [x] 资格过滤 ST、停牌、历史不足、无效价格和低流动性股票，并统计每类排除数量。
- [x] 用 Polars 横截面分位数组合趋势、动量、量价、波动和过热惩罚，稳定选出最多 25 只研究候选。
- [x] 聚合最近 60 个交易日的价格、指标、信号、关键位、量价和市场横截面信息。
- [x] 从 instruments 获取名称与上市信息，从已有财务目录只读取可用的轻量摘要；缺失维度显式列出。
- [x] 测试百分比小数口径、排序稳定性、前复权口径、交易日窗口和缺失数据降级。

Run:

```powershell
cd backend
uv run pytest tests/test_research_agent_screening.py tests/test_research_agent_evidence.py -q
uv run ruff check app/services/research_agent_screening.py app/services/research_agent_evidence.py tests/test_research_agent_screening.py tests/test_research_agent_evidence.py
```

Expected: deterministic candidates and evidence without provider calls.

### Task 4: AI 问答、每日推荐和复盘服务

**Files:**
- Create: `backend/app/services/research_agent.py`
- Test: `backend/tests/test_research_agent.py`

**Interfaces:**
- Consumes: terms, screening, evidence, store, `generate_ai_text`, `stream_ai_text`
- Produces: `ResearchAgentService`
- Produces: `chat_stream`, `run_recommendations`, `run_daily_reviews`, `status`

- [x] 术语精确命中直接输出内置 Markdown；其他问答使用结构化证据流式生成。
- [x] 每日推荐提示词只包含候选摘要，要求 JSON 输出并校验候选范围、反向证据和最多 5 只。
- [x] JSON 首次校验失败时进行一次修复请求，第二次失败不保存。
- [x] AI 未配置时返回候选预筛降级状态，不创建正式推荐。
- [x] 复盘用个股与沪深 300 相同起止日期计算累计收益、最大上涨、最大回撤和相对表现，并保存客观结果；AI 复盘失败不丢失客观指标。
- [x] 第 5、10、20 个交易日生成阶段标记，历史缺口按实际行情日期补算。

Run:

```powershell
cd backend
uv run pytest tests/test_research_agent.py -q
uv run ruff check app/services/research_agent.py tests/test_research_agent.py
```

Expected: AI is fully mockable; invalid outputs never become official recommendations.

### Task 5: FastAPI 契约、生命周期与盘后触发

**Files:**
- Create: `backend/app/api/research_agent.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/jobs/daily_pipeline.py`
- Test: `backend/tests/test_research_agent_api.py`
- Test: `backend/tests/test_research_agent_pipeline.py`

**Interfaces:**
- Produces: `/api/research-agent/terms`, `/chat`, `/recommendations/latest`, `/recommendations`, `/recommendations/run`, `/reviews`, `/reviews/run`, `/status`

- [x] 在 lifespan 创建服务并挂到 `app.state.research_agent_service`，关闭时停止后台执行器。
- [x] API 保持薄层；流式协议使用逐行 JSON `{type, content/meta/error}`。
- [x] 手动推荐支持 `force`；同日默认复用，强制运行创建父子版本。
- [x] 在每日流水线成功刷新 repository 后提交非阻塞幂等 Agent 任务；失败只更新 Agent 状态。
- [x] API 测试覆盖成功、无数据、AI 未配置、运行中、分页和强制新版本。

Run:

```powershell
cd backend
uv run pytest tests/test_research_agent_api.py tests/test_research_agent_pipeline.py -q
uv run ruff check app/api/research_agent.py app/main.py app/jobs/daily_pipeline.py tests/test_research_agent_api.py tests/test_research_agent_pipeline.py
```

Expected: route contracts pass and pipeline success is not changed by Agent failure.

### Task 6: 全局顶部入口、右侧抽屉与客户端状态

**Files:**
- Create: `frontend/src/lib/researchAgentStore.ts`
- Create: `frontend/src/components/research-agent/ResearchAgentHost.tsx`
- Create: `frontend/src/components/research-agent/ResearchAgentDrawer.tsx`
- Create: `frontend/src/components/research-agent/ResearchAgentTopBar.tsx`
- Create: `frontend/src/components/research-agent/RecommendationCard.tsx`
- Create: `frontend/src/components/research-agent/ReviewTimeline.tsx`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/queryKeys.ts`
- Modify: `frontend/src/components/Layout.tsx`

**Interfaces:**
- Consumes: Task 5 API contracts and existing `MarkdownRenderer`
- Produces: global top bar and drawer tabs `问答 / 今日选股 / 推荐复盘`

- [x] 添加 TypeScript 类型、请求方法、NDJSON 流解析和集中 query keys。
- [x] 全局 Store 保持流式任务在抽屉关闭后继续，支持恢复、错误和重试。
- [x] 顶部栏支持自然语言、股票代码/名称、快捷问题和“今日选股”。
- [x] 推荐卡展示倾向、置信度、多维证据、反向证据、风险、区间、来源和缺失项。
- [x] 复盘页展示今日总览、逐日收益轨迹、判断状态及 5/10/20 日阶段总结。
- [x] 保留现有 `Layout` 美股入口与其他未提交修改，窄屏抽屉改为全屏。

Run:

```powershell
cd frontend
pnpm build
```

Expected: TypeScript and Vite production build succeed.

### Task 7: 集成验证与文档同步

**Files:**
- Modify: `docs/features.md`
- Modify: `docs/superpowers/specs/2026-08-12-ai-research-agent-design.md` only if implementation evidence changes a contract

- [x] 运行所有新增后端测试及受影响的 AI/provider/pipeline 测试。
- [x] 运行本次 Python 文件 Ruff、前端生产构建和 `git diff --check`。
- [x] 检查最终 diff 未包含数据库、密钥、缓存、日志或无关改动。
- [x] 记录已验证路径和公告/新闻仍处于降级接口的剩余风险。

验证记录（2026-08-12）：后端全量 `628 passed`；研究 Agent 核心专项 `30 passed`；新模块 Ruff 通过；前端生产构建通过；本地真实数据预筛、证据聚合和 Playwright 桌面/窄屏交互通过。历史证据按交易日截断，正式结论绑定证据路径，阶段复盘支持中断后自愈。普通新闻 Provider 仍为明确降级接口。

Run:

```powershell
cd backend
uv run pytest tests/test_research_agent_*.py tests/test_ai_provider.py tests/test_pipeline_and_monitor_fixes.py -q
uv run ruff check app/services/research_agent*.py app/api/research_agent.py tests/test_research_agent_*.py
cd ..\frontend
pnpm build
cd ..
git diff --check
git status --short
```

Expected: all selected tests, Ruff and build pass; status contains only intentional source/docs changes plus the pre-existing US dashboard work.
