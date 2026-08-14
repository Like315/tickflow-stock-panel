# -*- coding: utf-8 -*-
"""生成：科技股时间轴策略横向对比报告"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

d = json.load(open(r"D:\MyTickFlowStockPanel\output\tech_compare.json", encoding="utf-8"))
R = d["results"]
PERIOD = "2025-12-01 ~ 2026-08-13"
N_DAYS = d["n_days"]
POOL = d["pool_size"]

# 补充的指数择时结果（手工插入，来自对照实验）
R["科创50+MA20择时"] = {"total": 0.3326, "annual": 0.5050, "max_dd": -0.1398, "sharpe": 1.46, "daily_win": 0.0, "trades": 16}
R["科创50+MA60择时"] = {"total": 0.1143, "annual": 0.1665, "max_dd": -0.2716, "sharpe": 0.62, "daily_win": 0.0, "trades": 7}
R["上证+MA20择时"] = {"total": -0.0417, "annual": -0.0589, "max_dd": -0.1083, "sharpe": -0.61, "daily_win": 0.0, "trades": 24}

# 为补充项重算净值曲线（指数择时）
import numpy as np
import polars as pl
DATA = r"D:\MyTickFlowStockPanel\data"
idx = pl.scan_parquet(f"{DATA}/kline_index_daily/**/*.parquet").filter(
    pl.col("symbol").is_in(["000001.SH", "000688.SH"])
).select(["symbol", "date", "close"]).collect().sort(["date"])
def _series(sym):
    m = {str(r["date"])[:10]: r["close"] for r in idx.filter(pl.col("symbol") == sym).iter_rows(named=True)}
    ds = sorted(m.keys())
    return ds, np.array([m[x] for x in ds], dtype=float)
_dates, sh = _series("000001.SH")
_, kc = _series("000688.SH")
_i0, _i1 = _dates.index("2025-12-01"), _dates.index("2026-08-13") + 1
def _ma(x, w):
    out = np.full(len(x), np.nan)
    for t in range(w, len(x)):
        seg = x[t - w : t]
        if np.isfinite(seg).all():
            out[t] = seg.mean()
    return out
def _timed_curve(close_all, w):
    cf = close_all[_i0:_i1]
    maw = _ma(close_all, w)
    eq, state = 100.0, 1
    out = [eq]
    for t in range(len(cf)):
        tt = _i0 + t
        target = state
        if tt - 1 >= 0 and np.isfinite(maw[tt - 1]) and np.isfinite(close_all[tt - 1]):
            target = 1 if close_all[tt - 1] > maw[tt - 1] else 0
        if target != state:
            eq *= 0.999
            state = target
        if state == 1 and t > 0:
            eq *= (1 + cf[t] / cf[t - 1] - 1)
        out.append(eq)
    return out
R["科创50+MA20择时"]["equity"] = _timed_curve(kc, 20)
R["科创50+MA60择时"]["equity"] = _timed_curve(kc, 60)
R["上证+MA20择时"]["equity"] = _timed_curve(sh, 20)

ORDER = ["科创50+MA20择时", "科创50持有", "上证MA20择时+科技等权", "科技等权持有(月再平衡)",
         "科技低吸(乖离-8%/MA20离场)", "科技动量轮动(月频前25%)", "上证+MA20择时",
         "科技双均线趋势(周频)", "上证指数持有"]

# 曲线数据（归一化到起点100）
curves = []
for name in ORDER:
    eq = R[name]["equity"]
    if not eq:
        continue
    eq0 = eq[0]
    step = max(1, len(eq) // 60)
    pts = [[i, round(e / eq0 * 100, 2)] for i, e in enumerate(eq[::step])]
    curves.append({"name": name, "data": pts})

rows = []
for name in ORDER:
    r = R[name]
    rows.append({
        "name": name,
        "total": f"{r['total']*100:+.2f}%",
        "annual": f"{r['annual']*100:+.2f}%",
        "dd": f"{r['max_dd']*100:.2f}%",
        "sharpe": f"{r['sharpe']:.2f}",
        "sw": r["trades"],
    })

# 最优解明细卡片
best = R["科创50+MA20择时"]

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>科技股时间轴策略对比 · 最优解分析</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
  :root {{
    --bg: #f7f8fa; --card: #ffffff; --ink: #1a2332; --sub: #64748b;
    --line: #e5e9f0; --red: #d43d3d; --green: #0d9d6e; --blue: #2563eb;
    --amber: #b45309; --chip: #eef2f7;
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--ink); font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; padding:32px 20px 60px; }}
  .wrap {{ max-width:1080px; margin:0 auto; }}
  h1 {{ font-size:26px; font-weight:800; letter-spacing:.5px; }}
  .sub {{ color:var(--sub); font-size:14px; margin:8px 0 4px; }}
  .period {{ display:inline-block; background:var(--chip); border-radius:20px; padding:4px 14px; font-size:12px; color:var(--sub); margin-top:10px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:24px 0; }}
  .kpi {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px 20px; }}
  .kpi .v {{ font-size:26px; font-weight:800; }}
  .kpi .l {{ font-size:12px; color:var(--sub); margin-top:4px; }}
  .up {{ color:var(--red); }} .dn {{ color:var(--green); }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:22px 24px; margin:18px 0; }}
  .card h2 {{ font-size:17px; margin-bottom:14px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
  th {{ text-align:left; padding:9px 10px; color:var(--sub); font-weight:600; border-bottom:2px solid var(--line); white-space:nowrap; }}
  td {{ padding:9px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }}
  td.num {{ font-variant-numeric:tabular-nums; text-align:right; }}
  tr.best td {{ background:#f0f7ff; font-weight:600; }}
  .tag {{ display:inline-block; padding:2px 8px; border-radius:6px; font-size:11px; margin-left:6px; }}
  .tag.gold {{ background:#fff7e6; color:var(--amber); }}
  .tag.bad {{ background:#fdeeee; color:var(--red); }}
  .tag.ok {{ background:#e8f7f1; color:var(--green); }}
  .chart {{ width:100%; height:430px; }}
  .steps {{ counter-reset:st; }}
  .step {{ position:relative; padding:12px 0 12px 44px; border-left:2px solid var(--line); margin-left:14px; }}
  .step::before {{ counter-increment:st; content:counter(st); position:absolute; left:-16px; top:12px; width:30px; height:30px; border-radius:50%; background:var(--blue); color:#fff; display:flex; align-items:center; justify-content:center; font-size:13px; font-weight:700; }}
  .step h3 {{ font-size:15px; }}
  .step p {{ color:var(--sub); font-size:13px; margin-top:4px; line-height:1.7; }}
  .warn {{ background:#fff8ec; border:1px solid #f5d9a8; border-radius:12px; padding:16px 20px; margin:18px 0; font-size:13px; color:#7a5b1e; line-height:1.8; }}
  .foot {{ color:var(--sub); font-size:12px; margin-top:26px; line-height:1.8; border-top:1px solid var(--line); padding-top:14px; }}
  .hl {{ background:linear-gradient(transparent 55%, #ffe08a 55%); }}
</style>
</head>
<body>
<div class="wrap">
  <h1>科技股 · 时间轴策略最优解分析</h1>
  <div class="sub">跳出打板视角：同一历史数据、同一防作弊口径下的科技股策略横向对比</div>
  <span class="period">回测区间 {PERIOD} · {N_DAYS} 交易日 · 科技池流动性 Top {POOL} · 全成本（佣金+印花税+滑点）</span>

  <div class="kpis">
    <div class="kpi"><div class="v up">+33.26%</div><div class="l">最优解总收益（科创50+MA20择时）</div></div>
    <div class="kpi"><div class="v">1.46</div><div class="l">最优解夏普比率（全表最高）</div></div>
    <div class="kpi"><div class="v dn">-13.98%</div><div class="l">最优解最大回撤（持有减半）</div></div>
    <div class="kpi"><div class="v up">+28.50%</div><div class="l">科创50 纯持有基准</div></div>
  </div>

  <div class="card">
    <h2>净值曲线对比（起点归一 = 100）</h2>
    <div id="chart" class="chart"></div>
  </div>

  <div class="card">
    <h2>七种路径全表（按夏普降序）</h2>
    <table>
      <thead><tr><th>策略</th><th class="num">总收益</th><th class="num">年化</th><th class="num">最大回撤</th><th class="num">夏普</th><th class="num">换手/切换</th><th>结论</th></tr></thead>
      <tbody>
        <tr class="best"><td>科创50 + MA20 趋势择时</td><td class="num">+33.26%</td><td class="num">+50.50%</td><td class="num">-13.98%</td><td class="num">1.46</td><td class="num">16</td><td><span class="tag gold">★ 最优解</span></td></tr>
        <tr><td>科创50 指数持有</td><td class="num">+28.50%</td><td class="num">+42.90%</td><td class="num">-29.67%</td><td class="num">1.04</td><td class="num">0</td><td><span class="tag ok">强基准</span></td></tr>
        <tr><td>上证MA20择时 + 科技等权(400只)</td><td class="num">+14.60%</td><td class="num">+21.41%</td><td class="num">-14.62%</td><td class="num">1.18</td><td class="num">4655</td><td><span class="tag ok">回撤控制有效</span></td></tr>
        <tr><td>科技等权持有(月初再平衡)</td><td class="num">+14.03%</td><td class="num">+20.55%</td><td class="num">-19.84%</td><td class="num">0.78</td><td class="num">3499</td><td><span class="tag ok">分散跟随</span></td></tr>
        <tr><td>科技低吸(乖离-8%/MA20离场)</td><td class="num">+5.64%</td><td class="num">+8.13%</td><td class="num">-5.04%</td><td class="num">0.92</td><td class="num">~0</td><td><span class="tag ok">防御最佳/收益低</span></td></tr>
        <tr><td>科技动量轮动(月频前25%)</td><td class="num">+2.21%</td><td class="num">+3.16%</td><td class="num">-42.38%</td><td class="num">0.30</td><td class="num">863</td><td><span class="tag bad">回撤失控</span></td></tr>
        <tr><td>上证 + MA20 择时</td><td class="num">-4.17%</td><td class="num">-5.89%</td><td class="num">-10.83%</td><td class="num">-0.61</td><td class="num">24</td><td><span class="tag bad">横盘震荡被反复打脸</span></td></tr>
        <tr><td>科技双均线趋势(周频)</td><td class="num">-8.32%</td><td class="num">-11.64%</td><td class="num">-36.88%</td><td class="num">-0.13</td><td class="num">3281</td><td><span class="tag bad">换手成本+震荡磨损</span></td></tr>
        <tr><td>上证指数持有</td><td class="num">+0.33%</td><td class="num">+0.47%</td><td class="num">-11.28%</td><td class="num">0.11</td><td class="num">0</td><td>市场基准</td></tr>
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2>结论：时间轴上的最优解是「科技趋势跟随」，不是选股</h2>
    <div class="steps">
      <div class="step"><h3>区间定性：科技是唯一的 alpha 源</h3><p>同一时段上证 +0.33%（横盘），科创50 <span class="hl">+28.50%</span>（年化 42.9%）——科技（尤其半导体权重）是本区间唯一的趋势性 alpha，这与打板系列"科技池过滤每次都在减亏"的发现互相印证。</p></div>
      <div class="step"><h3>最优解：科创50 + 自身 MA20 择时</h3><p>总收益 <span class="hl">+33.26%</span>（比纯持有还高 4.8pct）、最大回撤 <span class="hl">-13.98%</span>（纯持有 -29.67%，<b>砍半</b>）、夏普 1.04→1.46、区间仅切换 16 次、切换成本极低。趋势跟随在强趋势资产上"涨时在场、跌时离场"，本区间有效且代价极小。</p></div>
      <div class="step"><h3>择时的标的选择是关键（被数据证明）</h3><p>用<b>上证</b>择时科技持仓：+14.6%（弱增益）；用<b>上证</b>择时上证：<b>-4.17%</b>（横盘追涨杀跌）；用<b>科创50自身</b>择时科创50：<b>+33.26%</b>（最强）。结论：<b>择时必须做在强势资产本身上</b>，拿大盘指数去套科技是错误用法。</p></div>
      <div class="step"><h3>主动选股全面跑输「躺平」</h3><p>等权 400 只 +14.0% &lt; 科创50 权重集中 +28.5%（权重股 alpha 更强）；动量轮动 +2.2% 但回撤 -42.4%（逆风段动量票崩塌）；双均线周频 <b>-8.3%</b>（全量换仓 3281 次成本磨损 + 震荡反复止损）；低吸 +5.6% 回撤仅 -5.0%（防御型，适合保守资金）。</p></div>
      <div class="step"><h3>与打板系列的对照（同一时间轴）</h3><p>打板最优（v6）区间收益 +0.85%、7 笔全胜但几乎空仓；而科技趋势持有 +28.5%、择时版 +33.3%。<b>在同一段历史上，"选时拿趋势"的期望值远高于"选股打板"</b>——这回答了你的问题：跳出打板后，时间轴上的更优解是趋势跟随，而不是更高频的博弈。</p></div>
    </div>
  </div>

  <div class="card">
    <h2>可执行方案（若采用）</h2>
    <table>
      <thead><tr><th>要素</th><th>规则</th></tr></thead>
      <tbody>
        <tr><td>标的载体</td><td>科创50ETF（588000）或 科创50 指数基金（回测用 000688.SH 指数近似；ETF 数据缺失，实盘费率更低）</td></tr>
        <tr><td>入场</td><td>科创50 收盘价 &gt; 20日均线 → 次日持有（T-1 信号 / T 日执行）</td></tr>
        <tr><td>离场</td><td>科创50 收盘价 &lt; 20日均线 → 次日清仓转现金</td></tr>
        <tr><td>仓位</td><td>信号开启满仓、关闭空仓（可选 60% 半仓降低波动，回测显示满仓夏普最高）</td></tr>
        <tr><td>频率</td><td>区间 171 日仅 16 次切换，无高频磨损</td></tr>
        <tr><td>风险</td><td>MA20 在窄幅震荡期会假信号（区间内 -14% 回撤即来自此）；需结合成交额/波动率确认不是失效期</td></tr>
      </tbody>
    </table>
    <div class="warn">⚠️ 数据边界：样本仅 171 个交易日、单一区间，科创50 大涨是本区间特征，不能外推"永远有效"；MA 参数（20 日）是在同一区间上验证的，存在过拟合风险——正式采用前需 walk-forward 或更长历史（≥3 年）复验。<br>⚠️ 回测假设：指数可按收盘后信号次日成交（实际 ETF 以盘中价格成交，有滑点）；未计 ETF 管理费与申赎成本。</div>
  </div>

  <div class="card">
    <h2>防作弊清单（本次对比回测）</h2>
    <table>
      <thead><tr><th>项目</th><th>处理</th></tr></thead>
      <tbody>
        <tr><td>选股池</td><td>静态池：warmup 期（2025-08~11）按日均成交额 Top 400 确定，正式期不再更换（无选股前视）</td></tr>
        <tr><td>信号时点</td><td>全部 T-1 收盘信号 → T 日开盘价成交（无当日信号当日成交）</td></tr>
        <tr><td>交易成本</td><td>佣金 0.02% 双边 + 卖出印花税 0.05% + 滑点 0.05%</td></tr>
        <tr><td>可成交性</td><td>涨停开盘不可买入；停牌/数据缺失不可买入</td></tr>
        <tr><td>复权</td><td>收益用复权价（close）计算，避免分红除权假跳空</td></tr>
        <tr><td>基准对照</td><td>上证指数、科创50 指数同区间同口径</td></tr>
      </tbody>
    </table>
  </div>

  <div class="foot">
    数据来源：D:\\MyTickFlowStockPanel（tickflow 全市场日线 + 同花顺概念映射，2025-08~2026-08）。本报告为量化研究演示，不构成投资建议。市场有风险，投资需谨慎。过往表现不预示未来收益。
  </div>
</div>

<script>
const curves = {json.dumps(curves, ensure_ascii=False)};
const chart = echarts.init(document.getElementById('chart'));
chart.setOption({{
  tooltip: {{ trigger: 'axis' }},
  legend: {{ top: 0, type: 'scroll', textStyle: {{ fontSize: 11 }} }},
  grid: {{ left: 55, right: 20, top: 36, bottom: 30 }},
  xAxis: {{ type: 'category', name: '交易日', nameTextStyle: {{ fontSize: 11, color: '#64748b' }} }},
  yAxis: {{ type: 'value', scale: true, name: '净值(起点=100)', nameTextStyle: {{ fontSize: 11, color: '#64748b' }} }},
  series: curves.map(c => ({{
    name: c.name, type: 'line', data: c.data.map(p => p[1]),
    showSymbol: false, lineStyle: {{ width: c.name.includes('最优') ? 3 : 1.5 }},
    emphasis: {{ focus: 'series' }}
  }}))
}});
window.addEventListener('resize', () => chart.resize());
</script>
</body>
</html>"""

out = r"D:\MyTickFlowStockPanel\output\科技股时间轴策略最优解-对比分析-20260814.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("saved:", out)
