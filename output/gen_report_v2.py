# -*- coding: utf-8 -*-
"""生成 v2 回测报告 HTML（v1→v2 优化对比）。"""
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT = r"D:\MyTickFlowStockPanel\output"
data = json.load(open(OUT + r"\limit_up_leader_v2_backtest.json", encoding="utf-8"))

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

# 补充实验数据（脚本输出，手工固化）
extra = {
    "v2空间龙+情绪严(100+高度5)": {"total_return": -0.0603, "max_drawdown": -0.0663, "win_rate": 0.4444,
                                     "n_trades": 9, "avg_pnl": -0.0332, "median_pnl": -0.0618, "profit_factor": 0.62},
}
periods = {
    "2025-11~2026-01": {"base": -0.208, "sl": -0.092, "bench": 0.036, "n_base": 56, "n_sl": 3},
    "2026-02~2026-04": {"base": -0.098, "sl": -0.085, "bench": 0.024, "n_base": 52, "n_sl": 12},
    "2026-05~2026-08": {"base": -0.467, "sl": -0.168, "bench": -0.056, "n_base": 142, "n_sl": 24},
}

names = ["v1基准(情绪80+持1天)", "v2基准(高度3+不板即走)", "v2无高度过滤", "v2空间龙(只打最高板)", "v2情绪严(家数100+高度4)", "v2固定持1天(关不板即走)"]
def rows_html():
    out = []
    for name in names:
        r = data.get(name)
        if not r or "error" in r:
            continue
        ex = r.get("execution") or {}
        out.append(f"""<tr><td>{name}</td><td>{r['total_return']*100:.1f}%</td><td>{r['max_drawdown']*100:.1f}%</td>
<td>{r['win_rate']*100:.1f}%</td><td>{r['n_trades']}</td><td>{r['avg_pnl']*100:.2f}%</td>
<td>{r['avg_holding_days']}</td><td>{r['profit_factor']}</td>
<td>{ex.get('buy_limit_up', 0)} / {ex.get('sell_limit_down', 0)} / {ex.get('pending_exit', 0)}</td>
<td><span style="color:#6b7684">{r.get('note', '')}</span></td></tr>""")
    ex = {"buy_limit_up": 0, "sell_limit_down": 2, "pending_exit": 1}
    out.append(f"""<tr style="background:#f4f9f4"><td><b>v2 空间龙+情绪严(100+高度5)</b></td>
<td><b style="color:#12a150">{extra['v2空间龙+情绪严(100+高度5)']['total_return']*100:.1f}%</b></td>
<td>{extra['v2空间龙+情绪严(100+高度5)']['max_drawdown']*100:.1f}%</td>
<td>{extra['v2空间龙+情绪严(100+高度5)']['win_rate']*100:.1f}%</td>
<td>{extra['v2空间龙+情绪严(100+高度5)']['n_trades']}</td>
<td>{extra['v2空间龙+情绪严(100+高度5)']['avg_pnl']*100:.2f}%</td><td>—</td>
<td>{extra['v2空间龙+情绪严(100+高度5)']['profit_factor']}</td>
<td>{ex['buy_limit_up']} / {ex['sell_limit_down']} / {ex['pending_exit']}</td>
<td><span style="color:#12a150">本区间最优：几乎空仓（9 笔）</span></td></tr>""")
    return "\n".join(out)

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>龙头打板策略 v2 — 优化迭代回测报告</title>
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

<h1>龙头打板策略 v2 — 优化迭代回测报告</h1>
<div class="sub">回测区间：2025-11-03 ~ 2026-08-13（约 200 交易日）｜成交：T+1 开盘价｜全成本模型｜防作弊口径与 v1 一致</div>

<div class="tl">
<h2>核心结论</h2>
<ul>
<li><b>三步优化均有效，方向被验证</b>：卖出改"不板即走"（-70.0%→-64.3%）、情绪门槛提高（-64.3%→-43.3%）、空间龙收敛（-64.3%→-35.3%），三者叠加（空间龙+家数100+高度5）→ <b class="green">-6.0%，最大回撤仅 -6.6%</b>。</li>
<li><b>最诚实的解读</b>：-6.0% 版本只有 9 笔交易/200 个交易日 —— 本质是"几乎空仓"策略。在打板逆风周期，<b>空仓就是最优解</b>；策略的"不亏钱能力"被验证，"赚钱能力"尚未被证明。</li>
<li><b>新发现</b>：连板高度维度在阈值 3 时与"涨停家数≥80"完全冗余（该区间家数达标日必有 3 板以上）；高度 ≥5 才有独立区分度 —— 情绪过滤应看"高高度"而非"有高度"。</li>
<li><b>子区间验证</b>：空间龙模式在 2026-05~08 逆风期把亏损从 -46.7% 压缩到 -16.8%（交易 142→24 笔）；顺风期（2026-02~04）差异不大（-9.8% vs -8.5%），说明"只打最高板"还需要板块/题材维度才能真正盈利。</li>
<li><b>结论</b>：v2 仍不可实盘，但已收敛出正确的策略骨架 —— 高情绪门槛 + 空间龙 + 不板即走 + 空仓纪律。v3 方向：加题材/板块龙头识别（见文末）。</li>
</ul>
</div>

<div class="card">
<h2>一、v2 策略规则（相对 v1 的三处修改）</h2>
<div class="rule"><b>① 情绪过滤双维度</b>：全市场当日涨停家数 ≥ 80（广度）+ 当日最高连板 ≥ 3（高度，建议实盘用 ≥5）—— 广度与高度同时达标才出手。</div>
<div class="rule"><b>② 卖出"不板即走"</b>：持仓日收盘未封板 → 次日开盘卖出；涨停则继续持有（让利润奔跑）。由策略级 exit 信号实现，-6% 止损保留。</div>
<div class="rule"><b>③ 空间龙模式（可选）</b>：space_leader_only=True 时只打当日最高连板梯队的股票 —— 从"连板≥2 的池子"收敛到"全市场最强票"。</div>
<div class="src">实现：backend/app/strategy/builtin/limit_up_leader_v2.py；回测：backend/scripts/bt_limit_up_leader_v2.py。</div>
</div>

<div class="card">
<h2>二、v1 vs v2 全量对比</h2>
<table>
<tr><th>参数组合</th><th>总收益</th><th>最大回撤</th><th>胜率</th><th>交易数</th><th>平均单笔</th><th>平均持有(天)</th><th>盈亏比</th><th>拦截 买一字/卖跌停/挂起</th><th>备注</th></tr>
{rows_html()}
</table>
<div class="src">统一配置：max_positions=4、单票≤20%、总仓≤80%、佣金万2+印花税0.05%+滑点5bp、T+1 开盘成交。</div>
</div>

<div class="card">
<h2>三、关键图表</h2>
<div id="chEvolve" class="chart"></div>
<div class="src">v1→v2 演化：每步优化的累计贡献。全绿=亏损（A 股惯例红涨绿跌，样本区间全部为负值）。</div>
<div class="grid2">
<div>
<div id="chPeriod" class="chart"></div>
<div class="src">子区间：v2 基准 vs 空间龙 vs 上证。空间龙在逆风期（2026-05~08）显著减亏。</div>
</div>
<div>
<div id="chEquity" class="chart"></div>
<div class="src">净值曲线（百万）：v1 基准 vs v2 基准 vs v2 空间龙。</div>
</div>
</div>
</div>

<div class="warn">
<h3>⚠️ 优化结论与 v3 方向</h3>
<ul>
<li><b>验证了什么</b>：①"退潮期空仓"（情绪过滤）是全策略最重要的规则；②卖出端"不板即走"优于固定持有；③收敛到"市场最强票"（空间龙）显著降低逆风期亏损；④空仓本身就是策略的一部分。</li>
<li><b>还缺什么</b>：v2 的空间龙只看"连板高度"，不看<b>题材/板块</b> —— 这是龙头打板的灵魂（当日主线 + 板块内辨识度）。数据层缺行业/概念映射，需要补充（instruments 目前无行业字段）。</li>
<li><b>v3 建议</b>：① 引入板块（行业/概念）映射，识别"主线题材内的最高板"而非"全市场最高板"；② 情绪过滤加"昨日涨停今日溢价"（前日涨停池当日平均涨幅）；③ 数据延长至 2-3 年覆盖完整牛熊周期；④ 对最终参数做 walk-forward 样本外验证。</li>
<li><b>当前建议</b>：样本区间为打板逆风周期，策略应空仓；可把 v2 作为"市场情绪监控 + 出手纪律框架"先行使用。</li>
</ul>
</div>

<div class="disc">
<b>免责声明</b>：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
</div>
<div class="foot">报告生成：2026-08-13 ｜ 数据：TickFlow 本地历史 parquet ｜ 回测引擎：backend/app/backtest（矩阵原生 + 组合撮合）</div>
</div>

<script>
(function () {{
  var green = '#12a150', blue = '#2563eb', gray = '#8a94a6', red = '#e0312f';

  var evolve = [
    {{ n: 'v1 基准', v: -70.0 }},
    {{ n: 'v2 卖出优化', v: -64.3 }},
    {{ n: 'v2 情绪严', v: -43.3 }},
    {{ n: 'v2 空间龙', v: -35.3 }},
    {{ n: 'v2 空间龙+严情绪', v: -6.0 }}
  ];
  echarts.init(document.getElementById('chEvolve')).setOption({{
    tooltip: {{ trigger: 'axis', formatter: function (ps) {{ return ps[0].name + '：' + ps[0].value + '%'; }} }},
    grid: {{ left: 55, right: 20, top: 20, bottom: 50 }},
    xAxis: {{ type: 'category', data: evolve.map(function (d) {{ return d.n; }}), axisLabel: {{ fontSize: 11, interval: 0 }} }},
    yAxis: {{ type: 'value', axisLabel: {{ formatter: '{{value}}%' }} }},
    series: [{{
      type: 'bar', barWidth: '45%',
      data: evolve.map(function (d) {{ return {{ value: d.v, itemStyle: {{ color: green }} }}; }}),
      label: {{ show: true, position: 'top', formatter: function (p) {{ return p.value + '%'; }} }}
    }}]
  }});

  var pn = ['2025-11~2026-01', '2026-02~2026-04', '2026-05~2026-08'];
  var pdata = {json.dumps(periods, ensure_ascii=False)};
  echarts.init(document.getElementById('chPeriod')).setOption({{
    tooltip: {{ trigger: 'axis' }},
    legend: {{ data: ['v2基准', 'v2空间龙', '上证指数'], top: 0 }},
    grid: {{ left: 55, right: 20, top: 30, bottom: 30 }},
    xAxis: {{ type: 'category', data: pn }},
    yAxis: {{ type: 'value', axisLabel: {{ formatter: '{{value}}%' }} }},
    series: [
      {{ name: 'v2基准', type: 'bar', barWidth: '22%', data: pn.map(function (n) {{ return +(pdata[n].base * 100).toFixed(1); }}), itemStyle: {{ color: gray }} }},
      {{ name: 'v2空间龙', type: 'bar', barWidth: '22%', data: pn.map(function (n) {{ return +(pdata[n].sl * 100).toFixed(1); }}), itemStyle: {{ color: green }} }},
      {{ name: '上证指数', type: 'bar', barWidth: '22%', data: pn.map(function (n) {{ return +(pdata[n].bench * 100).toFixed(1); }}), itemStyle: {{ color: blue }} }}
    ]
  }});

  var curves = {json.dumps(curves, ensure_ascii=False)};
  var baseName = 'v1基准(情绪80+持1天)';
  var v2Name = 'v2基准(高度3+不板即走)';
  var slName = 'v2空间龙(只打最高板)';
  var dates = curves[v2Name].map(function (p) {{ return p.d; }});
  echarts.init(document.getElementById('chEquity')).setOption({{
    tooltip: {{ trigger: 'axis' }},
    legend: {{ data: ['v1基准', 'v2基准', 'v2空间龙'], top: 0 }},
    grid: {{ left: 60, right: 20, top: 30, bottom: 30 }},
    xAxis: {{ type: 'category', data: dates }},
    yAxis: {{ type: 'value', scale: true }},
    series: [
      {{ name: 'v1基准', type: 'line', showSymbol: false, data: curves[baseName].map(function (p) {{ return p.v; }}), lineStyle: {{ color: gray, width: 1.5 }} }},
      {{ name: 'v2基准', type: 'line', showSymbol: false, data: curves[v2Name].map(function (p) {{ return p.v; }}), lineStyle: {{ color: blue, width: 2 }} }},
      {{ name: 'v2空间龙', type: 'line', showSymbol: false, data: curves[slName].map(function (p) {{ return p.v; }}), lineStyle: {{ color: green, width: 2 }} }}
    ]
  }});
}})();
</script>
</body>
</html>
"""

out_path = OUT + r"\龙头打板策略v2-回测报告-20260813.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print("written:", out_path, "| bytes:", len(html.encode("utf-8")))
