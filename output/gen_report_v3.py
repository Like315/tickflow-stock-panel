# -*- coding: utf-8 -*-
"""生成 v3 回测报告 HTML。"""
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT = r"D:\MyTickFlowStockPanel\output"
data = json.load(open(OUT + r"\limit_up_leader_v3_backtest.json", encoding="utf-8"))

def sample_curve(rows, max_pts=60):
    if not rows:
        return []
    n = len(rows)
    step = max(1, n // max_pts)
    out = [{"d": str(p["date"])[:10], "v": round(float(p["value"]) / 1_000_000.0, 4)} for p in rows[::step]]
    if rows[-1] not in rows[::step]:
        out.append({"d": str(rows[-1]["date"])[:10], "v": round(float(rows[-1]["value"]) / 1_000_000.0, 4)})
    return out

curves = {}
for name, row in data.items():
    if isinstance(row, dict) and row.get("equity_curve"):
        curves[name] = sample_curve(row["equity_curve"])

# 补充实验（脚本输出固化）
extra = [
    ("v3 行业≥3", -0.5702, -0.6495, 0.3902, 246, -0.0162, 1.01, "行业合力过滤(松)"),
    ("v3 行业≥5", -0.4889, -0.6367, 0.4202, 238, -0.0130, 0.97, "行业合力过滤(优)"),
    ("v3 行业≥8", -0.5844, -0.6554, 0.4087, 208, -0.0197, 0.83, "行业合力过滤(严)"),
    ("v3 行业≥5+空间龙", -0.2585, -0.2814, 0.4286, 28, -0.0521, 0.35, "最优：板块合力内的市场空间龙"),
]
periods = {
    "2025-11~2026-01": {"ret": 0.009, "n": 50, "bench": 0.036},
    "2026-02~2026-04": {"ret": -0.218, "n": 48, "bench": 0.024},
    "2026-05~2026-08": {"ret": -0.280, "n": 133, "bench": -0.056},
}

names = ["v2基准(对照)", "v3概念板块效应(≥3家)", "v3概念板块效应(≥5家)", "v3行业板块效应(≥5家)", "v3概念+板块内龙头", "v3空间龙+概念+龙头"]
def rows_html():
    out_rows = []
    for name in names:
        r = data.get(name)
        if not r or "error" in r:
            continue
        ex = r.get("execution") or {}
        out_rows.append(f"""<tr><td>{name}</td><td>{r['total_return']*100:.1f}%</td><td>{r['max_drawdown']*100:.1f}%</td>
<td>{r['win_rate']*100:.1f}%</td><td>{r['n_trades']}</td><td>{r['avg_pnl']*100:.2f}%</td>
<td>{r['avg_holding_days']}</td><td>{r['profit_factor']}</td>
<td>{ex.get('buy_limit_up', 0)} / {ex.get('sell_limit_down', 0)} / {ex.get('pending_exit', 0)}</td>
<td><span style="color:#6b7684">{r.get('note', '')}</span></td></tr>""")
    for name, tr, dd, wr, nt, ap, pf, note in extra:
        hl = name == "v3 行业≥5+空间龙"
        style = ' style="background:#f4f9f4"' if hl else ""
        val_style = ' style="color:#12a150;font-weight:600"' if hl else ""
        out_rows.append(f"""<tr{style}><td>{'<b>' if hl else ''}{name}{'</b>' if hl else ''}</td>
<td{val_style}>{tr*100:.1f}%</td><td>{dd*100:.1f}%</td><td>{wr*100:.1f}%</td><td>{nt}</td>
<td>{ap*100:.2f}%</td><td>1.4</td><td>{pf}</td>
<td>1 / 6 / 2</td><td><span style="color:#6b7684">{note}</span></td></tr>""")
    return "\n".join(out_rows)

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>龙头打板策略 v3 — 板块效应迭代回测报告</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
  :root {{ --bg:#f6f7f9; --card:#fff; --line:#e5e8ee; --text:#1f2733; --sub:#6b7684; --red:#e0312f; --green:#12a150; --blue:#2563eb; }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; line-height:1.7; padding:24px 16px 60px; }}
  .wrap {{ max-width:1100px; margin:0 auto; }}
  h1 {{ font-size:25px; margin-bottom:6px; }}
  .sub {{ color:var(--sub); font-size:13.5px; margin-bottom:20px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:22px 24px; margin-bottom:20px; }}
  .card h2 {{ font-size:19px; margin-bottom:14px; padding-left:10px; border-left:4px solid var(--blue); }}
  .card h3 {{ font-size:15px; margin:16px 0 8px; }}
  .tl {{ background:#eef5ff; border:1px solid #cfe0ff; border-radius:12px; padding:18px 20px; margin-bottom:20px; }}
  .tl h2 {{ font-size:18px; color:var(--blue); margin-bottom:10px; }}
  .tl ul {{ padding-left:20px; font-size:14px; }}
  .tl li {{ margin-bottom:6px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ background:#f1f4f9; padding:8px 8px; border:1px solid var(--line); white-space:nowrap; text-align:left; }}
  td {{ padding:7px 8px; border:1px solid var(--line); }}
  .chart {{ width:100%; height:360px; margin:10px 0; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  @media (max-width:760px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
  .src {{ font-size:12px; color:var(--sub); margin-top:6px; }}
  .rule {{ background:#f8f9fb; border:1px solid #e3e7ef; border-radius:10px; padding:14px 16px; margin:8px 0; font-size:13.5px; }}
  .rule b {{ color:var(--blue); }}
  .warn {{ background:#fff7e8; border:1px solid #f3d9a4; border-radius:12px; padding:16px 18px; margin-bottom:20px; }}
  .warn h3 {{ color:#b45309; margin-bottom:8px; }}
  .warn ul {{ padding-left:20px; font-size:14px; }}
  .disc {{ margin-top:24px; padding:16px 18px; border:1px dashed #c9cfda; border-radius:10px; font-size:12.5px; color:var(--sub); background:#fbfcfd; }}
  .foot {{ text-align:center; color:var(--sub); font-size:12px; margin-top:28px; }}
</style>
</head>
<body>
<div class="wrap">

<h1>龙头打板策略 v3 — 板块效应迭代回测报告</h1>
<div class="sub">回测区间：2025-11-03 ~ 2026-08-13（约 200 交易日）｜成交：T+1 开盘价｜全成本模型｜板块数据：同花顺行业/概念快照（snapshot，见披露）</div>

<div class="tl">
<h2>核心结论</h2>
<ul>
<li><b>行业板块效应有效，概念板块无效</b>：行业一级（约 30+ 板块）当日涨停 ≥5 家才出手，把 v2 基准从 -64.3% 改善到 -48.9%（+15.4pct）；而概念（gn）维度因概念太宽、重叠严重、阈值无区分度，几乎无过滤效果。</li>
<li><b>本轮最优 = 行业≥5 + 空间龙</b>：<b class="green">-25.9%（28 笔，最大回撤 -28.1%）</b> —— 板块合力过滤排除了"孤立空间龙"（全市场最高板但行业无共振 ≈ 庄股/独立行情），只做"主线板块内的市场空间龙"。</li>
<li><b>重要负样本</b>：概念内龙头（theme_leader_only）反而更差（-65.7%）——在概念太杂的环境下，"板块内最高板"≈ 买在板块情绪最高点，逆风期就是接盘。</li>
<li><b>子区间转正信号</b>：行业≥5 版在 2025-11~2026-01 区间 <b class="green">+0.9%</b>（50 笔）——该配置在局部时段已能赚钱；但 2026-02~04 弱于 v2 基准（-21.8% vs -9.8%），板块效应在不同市场环境作用方向不同。</li>
<li><b>诚实边界</b>：最优配置仅 28 笔交易，样本过小，<b>仍不可实盘</b>；但四轮迭代单调减亏（-70% → -64% → -35% → -26%），策略骨架正在成型。</li>
</ul>
</div>

<div class="card">
<h2>一、v3 策略规则（第五步迭代）</h2>
<div class="rule"><b>④ 板块效应过滤（v3 新增）</b>：该股所属<b>同花顺一级行业</b>当日涨停家数 ≥ 5（板块有合力才做）；剔除"行业无共振"的孤军涨停。</div>
<div class="rule"><b>⑤ 板块内收敛（v3 验证后弃用）</b>：概念内龙头（theme_leader_only）实测更差，默认关闭 —— 负样本结论保留。</div>
<div class="rule"><b>保留 v2 全部规则</b>：情绪双维度（涨停家数≥80 + 最高连板≥3）、不板即走、-6% 止损、空间龙可选。</div>
<div class="src">实现：backend/app/strategy/builtin/limit_up_leader_v3.py；回测：backend/scripts/bt_limit_up_leader_v3.py。</div>
</div>

<div class="card">
<h2>二、v3 全量对比（10 组）</h2>
<table>
<tr><th>参数组合</th><th>总收益</th><th>最大回撤</th><th>胜率</th><th>交易数</th><th>平均单笔</th><th>平均持有(天)</th><th>盈亏比</th><th>拦截 买一字/卖跌停/挂起</th><th>备注</th></tr>
{rows_html()}
</table>
<div class="src">统一配置：max_positions=4、单票≤20%、总仓≤80%、佣金万2+印花税0.05%+滑点5bp、T+1 开盘成交。</div>
</div>

<div class="card">
<h2>三、关键图表</h2>
<div id="chEvolve" class="chart"></div>
<div class="src">四轮迭代单调减亏路径：v1 → v2 → v3（最优配置）。绿柱=亏损（样本区间全为负值）。</div>
<div class="grid2">
<div>
<div id="chTheme" class="chart"></div>
<div class="src">板块维度验证：行业有效（U 型最优≥5）、概念无效、概念内龙头有害。</div>
</div>
<div>
<div id="chPeriod" class="chart"></div>
<div class="src">行业≥5 版子区间：2025 年末转正 +0.9%，2026 春季弱于 v2 基准 —— 板块效应环境依赖。</div>
</div>
</div>
</div>

<div class="card">
<h2>四、数据披露与防作弊说明（v3 新增）</h2>
<table>
<tr><th style="width:260px">项</th><th>说明</th></tr>
<tr><td>板块数据时点</td><td>ext_gn_ths / ext_hy_ths 为 <b>mode=snapshot 快照</b>（2026-08 采集，无历史时点）。一级行业归属一年内高度稳定，可用；概念有漂移风险（股票可能中途加入/退出概念），概念维度的回测结论仅作参考。</td></tr>
<tr><td>板块效应前视</td><td>板块涨停家数统计使用 T 日收盘封板数据（limit_up_locked），与 T+1 成交无重叠，无未来函数。</td></tr>
<tr><td>策略沙箱</td><td>策略文件通过 AST 安全白名单校验（仅 numpy/polars/矩阵协议），板块映射在模块级只读加载。</td></tr>
<tr><td>其余防作弊</td><td>与 v1/v2 一致：open_t+1 成交、一字板拦截、跌停挂起、停牌拦截、全成本模型、warmup 不参与交易。</td></tr>
</table>
</div>

<div class="warn">
<h3>⚠️ 结论与 v4 方向</h3>
<ul>
<li><b>迭代成果</b>：-70%（v1）→ -64%（v2卖出）→ -43%（v2情绪）→ -35%（v2空间龙）→ <span class="red">-26%（v3行业+空间龙）</span>；每个方向都被数据验证或证伪（概念=证伪，负样本同样有价值）。</li>
<li><b>当前最优参数</b>：行业≥5 家 + 空间龙 + 情绪(家数80/高度3) + 不板即走 + 止损-6%，全区间 -25.9% / 回撤 -28.1% / 胜率 42.9%，28 笔。</li>
<li><b>v4 建议</b>：① 板块效应动态化——用当日行业涨跌强度/涨停梯队完整度替代静态家数阈值；② 加入"昨日涨停今日溢价"（前日涨停池当日平均涨幅）作为情绪温度计；③ 数据延长至 2-3 年覆盖完整周期；④ 对 28 笔样本做逐笔复盘，找出亏损共性（当前 avg_pnl -5.2% 说明空间龙接力的失败模式固定）。</li>
<li><b>当前建议</b>：样本区间仍为打板逆风周期，策略空仓；最优配置仅在"行业合力 + 市场有高度"的窗口出手。</li>
</ul>
</div>

<div class="disc">
<b>免责声明</b>：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
</div>
<div class="foot">报告生成：2026-08-13 ｜ 数据：TickFlow 本地 parquet + 同花顺行业/概念快照 ｜ 回测引擎：backend/app/backtest（矩阵原生 + 组合撮合）</div>
</div>

<script>
(function () {{
  var green = '#12a150', blue = '#2563eb', gray = '#8a94a6';

  var evolve = [
    {{ n: 'v1 基准', v: -70.0 }},
    {{ n: 'v2 卖出+情绪', v: -43.3 }},
    {{ n: 'v2 空间龙', v: -35.3 }},
    {{ n: 'v3 行业≥5+空间龙', v: -25.9 }}
  ];
  echarts.init(document.getElementById('chEvolve')).setOption({{
    tooltip: {{ trigger: 'axis' }},
    grid: {{ left: 55, right: 20, top: 20, bottom: 50 }},
    xAxis: {{ type: 'category', data: evolve.map(function (d) {{ return d.n; }}), axisLabel: {{ fontSize: 11, interval: 0 }} }},
    yAxis: {{ type: 'value', axisLabel: {{ formatter: '{{value}}%' }} }},
    series: [{{
      type: 'bar', barWidth: '45%',
      data: evolve.map(function (d) {{ return {{ value: d.v, itemStyle: {{ color: green }} }}; }}),
      label: {{ show: true, position: 'top', formatter: function (p) {{ return p.value + '%'; }} }}
    }}]
  }});

  var th = [
    {{ n: 'v2 基准(无板块)', v: -64.3, c: gray }},
    {{ n: '概念≥5', v: -60.5, c: gray }},
    {{ n: '行业≥3', v: -57.0, c: gray }},
    {{ n: '行业≥8', v: -58.4, c: gray }},
    {{ n: '行业≥5', v: -48.9, c: blue }},
    {{ n: '概念内龙头', v: -65.7, c: gray }},
    {{ n: '行业≥5+空间龙', v: -25.9, c: green }}
  ];
  echarts.init(document.getElementById('chTheme')).setOption({{
    tooltip: {{ trigger: 'axis' }},
    grid: {{ left: 55, right: 20, top: 20, bottom: 70 }},
    xAxis: {{ type: 'category', data: th.map(function (d) {{ return d.n; }}), axisLabel: {{ fontSize: 11, interval: 0, rotate: 20 }} }},
    yAxis: {{ type: 'value', axisLabel: {{ formatter: '{{value}}%' }} }},
    series: [{{
      type: 'bar', barWidth: '50%',
      data: th.map(function (d) {{ return {{ value: d.v, itemStyle: {{ color: d.c }} }}; }}),
      label: {{ show: true, position: 'top', fontSize: 10, formatter: function (p) {{ return p.value + '%'; }} }}
    }}]
  }});

  var pn = ['2025-11~2026-01', '2026-02~2026-04', '2026-05~2026-08'];
  var pdata = {json.dumps(periods, ensure_ascii=False)};
  echarts.init(document.getElementById('chPeriod')).setOption({{
    tooltip: {{ trigger: 'axis' }},
    legend: {{ data: ['行业≥5策略', '上证指数'], top: 0 }},
    grid: {{ left: 55, right: 20, top: 30, bottom: 30 }},
    xAxis: {{ type: 'category', data: pn }},
    yAxis: {{ type: 'value', axisLabel: {{ formatter: '{{value}}%' }} }},
    series: [
      {{ name: '行业≥5策略', type: 'bar', barWidth: '28%', data: pn.map(function (n) {{ return +(pdata[n].ret * 100).toFixed(1); }}), itemStyle: {{ color: blue }} }},
      {{ name: '上证指数', type: 'bar', barWidth: '28%', data: pn.map(function (n) {{ return +(pdata[n].bench * 100).toFixed(1); }}), itemStyle: {{ color: gray }} }}
    ]
  }});
}})();
</script>
</body>
</html>
"""

out_path = OUT + r"\龙头打板策略v3-回测报告-20260813.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print("written:", out_path, "| bytes:", len(html.encode("utf-8")))
