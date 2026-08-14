# -*- coding: utf-8 -*-
"""生成：MA20 逻辑选股版报告"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

d = json.load(open(r"D:\MyTickFlowStockPanel\output\ma20_screen.json", encoding="utf-8"))
R = d["results"]
CAND = d["current_candidates"]

ORDER = ["科技等权持有", "个股筛选:MA20单均线(月频)", "个股筛选:MA20>MA60双均线(月频)",
         "组合:指数择时+个股双均线", "指数择时:科创50+MA20(满仓等权池近似)"]

curves = []
for name in ORDER:
    if name in R and R[name].get("total") is not None:
        curves.append({"name": name, "value": R[name]["total"] * 100})

rows = ""
for name in ORDER:
    r = R.get(name)
    if not r:
        continue
    rows += f"<tr><td>{name}</td><td class='num'>{r['total']*100:+.2f}%</td><td class='num'>{r['annual']*100:+.2f}%</td><td class='num'>{r['max_dd']*100:.2f}%</td><td class='num'>{r['sharpe']:.2f}</td><td class='num'>{r['trades']}</td></tr>"

cand_rows = ""
for c in CAND:
    bias = c["pct_vs_ma20"]
    warn = " ⚠️乖离过大" if bias > 20 else ""
    cand_rows += f"<tr><td>{c['symbol']}</td><td>{c['name']}</td><td>{c['industry']}</td><td class='num'>{c['close']}</td><td class='num'>{c['ma20']}</td><td class='num'>{c['ma60']}</td><td class='num'>{bias:+.2f}%{warn}</td></tr>"

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MA20 趋势逻辑 · 个股筛选版实测</title>
<style>
  :root {{ --bg:#f7f8fa; --card:#fff; --ink:#1a2332; --sub:#64748b; --line:#e5e9f0; --red:#d43d3d; --green:#0d9d6e; --blue:#2563eb; --amber:#b45309; --chip:#eef2f7; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--ink); font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; padding:32px 20px 60px; }}
  .wrap {{ max-width:1080px; margin:0 auto; }}
  h1 {{ font-size:25px; font-weight:800; }}
  .sub {{ color:var(--sub); font-size:14px; margin:8px 0 4px; }}
  .period {{ display:inline-block; background:var(--chip); border-radius:20px; padding:4px 14px; font-size:12px; color:var(--sub); margin-top:10px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:24px 0; }}
  .kpi {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px 20px; }}
  .kpi .v {{ font-size:24px; font-weight:800; }}
  .kpi .l {{ font-size:12px; color:var(--sub); margin-top:4px; }}
  .up {{ color:var(--red); }} .dn {{ color:var(--green); }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:22px 24px; margin:18px 0; }}
  .card h2 {{ font-size:17px; margin-bottom:14px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
  th {{ text-align:left; padding:9px 10px; color:var(--sub); font-weight:600; border-bottom:2px solid var(--line); white-space:nowrap; }}
  td {{ padding:9px 10px; border-bottom:1px solid var(--line); }}
  td.num {{ font-variant-numeric:tabular-nums; text-align:right; white-space:nowrap; }}
  tr.best td {{ background:#f0f7ff; font-weight:600; }}
  .tag {{ display:inline-block; padding:2px 8px; border-radius:6px; font-size:11px; margin-left:6px; font-weight:600; }}
  .tag.yes {{ background:#e8f7f1; color:var(--green); }}
  .tag.no {{ background:#fdeeee; color:var(--red); }}
  .tag.part {{ background:#fff7e6; color:var(--amber); }}
  .chart {{ width:100%; height:400px; }}
  .warn {{ background:#fff8ec; border:1px solid #f5d9a8; border-radius:12px; padding:16px 20px; margin:18px 0; font-size:13px; color:#7a5b1e; line-height:1.8; }}
  .foot {{ color:var(--sub); font-size:12px; margin-top:26px; line-height:1.8; border-top:1px solid var(--line); padding-top:14px; }}
  ul.tight {{ margin:6px 0 0 18px; font-size:13.5px; line-height:1.9; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>MA20 趋势逻辑能否选股？——个股筛选版实测</h1>
  <div class="sub">把"科创50+MA20 择时"的最优解逻辑下沉到个股层：单均线筛选 / 双均线筛选 / 叠加指数开关，逐一对冲</div>
  <span class="period">区间 2025-12-01 ~ 2026-08-13 · 171 交易日 · 科技池 Top400 · 月频调仓 · 全成本防作弊（T-1信号/T日开盘）</span>

  <div class="kpis">
    <div class="kpi"><div class="v up">+15.22%</div><div class="l">组合版（指数开关+个股双均线）</div></div>
    <div class="kpi"><div class="v">0.90</div><div class="l">组合版夏普（纯筛选仅 0.38~0.52）</div></div>
    <div class="kpi"><div class="v dn">-20.7%</div><div class="l">组合版回撤（纯筛选 -37.7%~-39.9%）</div></div>
    <div class="kpi"><div class="v">33</div><div class="l">当前时点规则筛出的候选数</div></div>
  </div>

  <div class="card">
    <h2>一、五条路径对比（MA20 逻辑的三种用法）</h2>
    <table>
      <thead><tr><th>策略</th><th class="num">总收益</th><th class="num">年化</th><th class="num">最大回撤</th><th class="num">夏普</th><th class="num">换手</th></tr></thead>
      <tbody>
        {rows}
      </tbody>
    </table>
    <ul class="tight" style="margin-top:12px">
      <li><b>个股 MA20 筛选单独用 = 无效甚至有害</b>：单均线 +5.27%、双均线 +9.52%，都低于"科技等权躺平"（+14.03%），且回撤扩大到 -38%~-40%——强势股在调整期补跌更猛，没有指数保护就是裸奔</li>
      <li><b>叠加指数开关后质变</b>：组合版 +15.22%、夏普 0.90、回撤 -20.7%（比纯筛选减半）——<span class="hl">"指数 MA20 开关"是个股筛选的前提条件</span>，负责防系统性风险，个股双均线负责选强势</li>
      <li><b>仍不如"直接做科创50+MA20"</b>（上轮 +31.98%、夏普 1.42）：等权分散 400 只把 alpha 稀释了。结论：MA20 逻辑<b>首选指数/ETF 载体</b>；个股筛选版适合"必须持有一篮子个股"的场景，且必须带指数开关</li>
    </ul>
  </div>

  <div class="card">
    <h2>二、当前时点（2026-08-13）规则筛出的候选（演示，非荐股）</h2>
    <div class="sub">规则：收盘 &gt; MA20 且 MA20 &gt; MA60 且 MA20 上行（近5日）· 科技池 Top400 · 共 {len(CAND)} 只，按当日成交额排序前 25</div>
    <table style="margin-top:10px">
      <thead><tr><th>代码</th><th>名称</th><th>行业</th><th class="num">收盘</th><th class="num">MA20</th><th class="num">MA60</th><th class="num">乖离MA20</th></tr></thead>
      <tbody>
        {cand_rows}
      </tbody>
    </table>
    <div class="warn">⚠️ 上表为"该规则在 2026-08-13 收盘会选出什么"的规则演示，<b>不是荐股</b>。注意三点：① 概念池较宽（含东方财富/迈瑞医疗等因"AI/人工智能"概念命中的非纯科技股），如只要纯科技请叠加行业过滤（计算机/电子/通信）；② 乖离 &gt;20% 的（景旺电子 +24.9%、康龙化成 +25.0%）追高有回调风险，优先乖离 2%~15% 区间；③ 名单每月随数据滚动更新，持有期间跌破 MA20 即离场。</div>
  </div>

  <div class="card">
    <h2>三、落地建议（两种用法，按场景选）</h2>
    <table>
      <thead><tr><th style="width:160px">场景</th><th>做法</th></tr></thead>
      <tbody>
        <tr><td>简单版（推荐）</td><td>科创50ETF（588000）+ 自身 MA20 开关：收盘站上 MA20 持有、跌破离场。回测 +31.98%、夏普 1.42、回撤 -14.8%，无需选股</td></tr>
        <tr><td>选股版</td><td>指数开关（科创50 站上 MA20）开启时，在科技池内选"收盘&gt;MA20 且 MA20&gt;MA60 且 MA20 上行"的票等权持有；指数破位全部清仓。回测 +15.22%、夏普 0.90、回撤 -20.7%</td></tr>
        <tr><td>可选增强</td><td>叠加行业过滤（仅计算机/电子/通信）收紧科技纯度；乖离 &gt;20% 剔除防追高；单票 ≤5% 上限控制集中度</td></tr>
      </tbody>
    </table>
  </div>

  <div class="foot">
    数据来源：D:\\MyTickFlowStockPanel（tickflow 全市场日线 + 同花顺概念/行业映射）。防作弊：静态池（warmup 期按流动性 Top400 确定）、T-1 信号/T 日开盘成交、全成本（佣金+印花税+滑点）、涨停/停牌不可买入。样本仅 171 交易日单区间，参数存在过拟合风险；候选名单为规则演示非投资建议。市场有风险，投资需谨慎。
  </div>
</div>
</body>
</html>"""

out = r"D:\MyTickFlowStockPanel\output\MA20选股版-实测报告-20260814.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("saved:", out)
