# 配置详解

所有配置从根目录 `.env` 读取(复制 `.env.example` 开始),也可在面板 **设置** 页面可视化修改。本文件解释每个配置项的作用。

部署相关配置(端口/密码/老 CPU 兼容)的实操见 [deployment.md](./deployment.md)。

---

## 数据源:TickFlow

```ini
TICKFLOW_API_KEY=              # 留空 = None 模式(历史日K免费);填 Key = 按订阅档位解锁
```

本项目基于 [TickFlow](https://tickflow.org) 数据源。

- **留空(None 模式)**:通过 free-api 使用历史日 K(当日数据盘后 1-2 小时可用),**无需付费**即可体验核心选股/回测功能
- **填入 API Key**:按你的订阅档位解锁更多能力

### 实时行情按档位

| 档位     | 实时能力                                 |
| :------- | :--------------------------------------- |
| Free     | 自选页前 5 个标的实时监控(最低 6 秒刷新) |
| Starter+ | 全市场实时行情                           |
| Pro      | 分钟 K + 盘口                            |
| Expert   | WebSocket + 财务数据                     |

> 完整能力矩阵见 [tickflow.org/pricing](https://tickflow.org/pricing/),高等档位含较低档全部权益。
> 在面板 **设置 → 凭据与能力** 点「重新检测」可查看当前档位标签。

---

## AI(可选)

用于自然语言生成策略。**所有配置留空即跳过**,不影响核心功能。支持任意 OpenAI 兼容接口。

```ini
AI_PROVIDER=openai_compat              # openai_compat | ollama
AI_BASE_URL=https://api.deepseek.com/v1
AI_API_KEY=                            # 留空 = 关闭 AI
AI_MODEL=deepseek-chat
AI_DAILY_TOKEN_BUDGET=500000           # 每日 token 预算上限
```

| 配置项 | 说明 |
| :--- | :--- |
| `AI_PROVIDER` | `openai_compat`(OpenAI 兼容,支持 DeepSeek / 通义 / OpenAI 等)或 `ollama`(本地模型) |
| `AI_BASE_URL` | 接口地址,如 DeepSeek `https://api.deepseek.com/v1` |
| `AI_API_KEY` | 留空则关闭 AI 功能 |
| `AI_MODEL` | 模型名,如 `deepseek-chat` |
| `AI_DAILY_TOKEN_BUDGET` | 每日 token 预算,超限后当日不再调用 |

接入示例见 [strategy.md](./strategy.md) 的「AI 生成策略」章节。

---

## 市场新闻(可选)

盘后 AI 复盘默认读取东方财富公开网页使用的全球财经快讯，并支持用 RSS 2.0 或 Atom 订阅补充其他来源。快讯接口没有稳定性承诺，因此始终按可降级 Provider 处理；来源失败时，复盘会继续使用行情数据生成，不阻塞主流程。

```ini
# 默认启用零配置市场快讯；不希望访问该来源时可关闭
NEWS_EASTMONEY_ENABLED=true

# 可选 RSS：多个地址用分号或换行分隔，最多读取 10 个订阅源
NEWS_RSS_URLS=https://example.com/feed.xml;https://example.org/atom.xml
```

- 只读取不晚于复盘截止日的标题、摘要、来源、发布时间和原文链接，不持久化新闻正文；RSS 保留最近 7 个自然日，东方财富快讯覆盖复盘日及前一自然日。
- 东方财富快讯会按游标向前翻页，覆盖复盘日早盘、盘中和收盘后时段；再按领涨/领跌概念、行业及代表个股筛选，不再直接使用时间最新的 8 条。
- “消息催化”必须引用入选新闻的完整标题、来源、时间和新闻序号，并区分“已兑现候选”和“待发酵”；没有直接匹配时会明确说明，不用量价异动反推具体政策消息。
- 单个来源超时、返回异常数据/XML 或超过 1 MB 时会被跳过；其他有效来源仍可参与复盘。
- 结果在进程内缓存 10 分钟，同一次定时复盘的 AI 重试不会反复请求订阅站点。
- 订阅内容属于外部不可信输入。提示词会忽略新闻中的指令性文本，但仍应只配置可信、允许使用的来源。

当前 RSS 新闻只注入“盘后 AI 复盘”。AI 研究 Agent 的个股新闻仍保持未配置状态，避免逐股抓取产生 N+1 请求和不受控的历史数据泄漏。

---

## 服务

```ini
HOST=0.0.0.0          # 监听地址
PORT=3018             # 服务端口
LOG_LEVEL=INFO        # DEBUG | INFO | WARNING | ERROR
```

- `HOST`:`0.0.0.0` 监听所有网卡(容器/公网部署需要);仅本机用可设 `127.0.0.1`
- `PORT`:默认 `3018`,改端口后 Docker 映射、SSH 转发命令里的端口也要同步改
- `LOG_LEVEL`:排查问题时改 `DEBUG`

---

## 数据

```ini
DATA_DIR=./data       # Parquet / DuckDB 数据存储目录
```

整个 `data/` 目录都不纳入 git —— 行情 K线、财务、自选、回测、监控记录,乃至概念/行业扩展数据,全部是程序运行时生成/拉取的用户数据。

如需迁移数据,直接拷贝整个 `data/` 目录即可。详见 [deployment.md → 更新代码](./deployment.md#更新代码已部署用户必读)。

---

## 访问密码(公网部署)

```ini
AUTH_PASSWORD=你的密码    # 至少 6 位;仅首次生效,已设过则不覆盖
```

面板首次设置访问密码时,出于安全考虑**仅允许本机或内网访问**(防公网陌生人抢先设置锁死面板)。公网服务器部署可通过此环境变量预置首个密码。

详细步骤、SSH 转发方案、重置密码方法见 [deployment.md → 访问密码设置](./deployment.md#访问密码设置公网部署必读)。

---

## 后端依赖 Extras(可选)

```ini
BACKEND_EXTRAS=             # 留空默认;legacy-cpu 兼容老 CPU
```

老 CPU 无 AVX2/FMA 支持时设为 `legacy-cpu`,会给 Polars 切到 `rtcompat` 运行时;需回测则 `legacy-cpu backtest`。Docker 构建和 `./dev.sh` / `.\dev.ps1` 都会读取此值并同步依赖。详见 [deployment.md → 老 CPU 兼容](./deployment.md#老-cpu-兼容avx2fma-缺失)。

---

## 配置优先级

1. **面板设置页**(`设置 → ...`):UI 修改后立即生效,持久化到 `data/`
2. **`.env` 文件**:启动时读取
3. **环境变量**:Docker / 系统环境变量,优先级最高

> 多数配置可在面板设置页修改,无需手动编辑 `.env`。仅 AI Key、API Key 等敏感项建议放 `.env`(不提交到 git)。
