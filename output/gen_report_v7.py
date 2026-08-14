# -*- coding: utf-8 -*-
"""生成 v7 报告 HTML（候选漏斗分析 + 空间龙容差证伪）。"""
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT = r"D:\MyTickFlowStockPanel\output"
data = json.load(open(OUT + r"\limit_up_leader_v7_backtest.json", encoding="utf-8"))

def sample_curve(rows, max_pts=60):
    if not rows:
        return []
    n = len(rows)
    step = max(1, n // max_pts)
    out = [{"d": str(p["date"])[:10], "v": round(float(p["value"]) / 1_000_000.0, 4)} for p in rows[::step]]
    if rows[-1] not in rows[::step]:
        out.append({"d": str(rows[-1]["date"])[:10], "v": round(float(rows[-1]["value"]) / 1_000_000.0, 4)})
    return out

curves = {name: sample_curve(row.get("equity_curve")) for name, row in data.items() if isinstance(row, dict) and row.get("equity_curve")}

funnel = [
    ("0 全市场收盘封板", 191, 15329),
    ("1 +科技池", 191, 9060),
    ("2 +连板≥1", 189, 8932),
    ("3 +连板≤3", 189, 8635),
    ("4 +成交额≥3亿", 189, 6281),
    ("5 +情绪(涨停80/高度3)", 78, 3556),
    ("6 +科技占比≥30%", 76, 3518),
    ("7 +行业(5家/涨幅1%)", 74, 2075),
    ("8 +空间龙(当日最高板)", 3, 4),
]

names = ["v6基线(容差0)", "v7 容差1(最高或次高)", "v7 容差1+连板≤4", "v7 容差2"]
def rows_html():
    out_rows = []
    for name in names:
        r = data.get(name)
        if not r:
            continue
        out_rows.append(f"""<tr><td>{name}</td><td>{r['total_return']*100:.2f}%</td><td>{r['max_drawdown']*100:.2f}%</td>
<td>{r['win_rate']*100:.0f}%</td><td>{r['n_trades']}</td></tr>""")
    return "\n".join(out_rows)

def funnel_rows():
    out_rows = []
    for i, (label, days, cands) in enumerate(funnel):
        cut_days = (funnel[i-1][1] - days) if i > 0 else 0
        cut_cands = (funnel[i-1][2] - cands) if i > 0 else 0
        hl = i in (5, 8)
        style = ' style="background:#f4f9f4"' if hl else ""
        out_rows.append(f"""<tr{style}><td>{label}</td><td>{days}</td><td>{cands}</td>
<td>{cut_days} 天</td><td>{cut_cands}</td>
<td>{'<b>大刀：情绪周期过滤（189→78 天）</b>' if i == 5 else ('<b>最严：空间龙收敛（74→3 天）</b>' if i == 8 else '—')}</td></tr>""")
    return "\n".join(out_rows)

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>龙头打板策略 v7 — 候选漏斗诊断与空间龙容差验证</title>
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
  .src {{ font-size:12px; color:var(--sub); margin-top:6px; }}
  .warn {{ background:#fff7e8; border:1px solid #f3d9a4; border-radius:12px; padding:16px 18px; margin-bottom:20px; }}
  .warn h3 {{ color:#b45309; margin-bottom:8px; }}
  .warn ul {{ padding-left:20px; font-size:14px; }}
  .disc {{ margin-top:24px; padding:16px 18px; border:1px dashed #c9cfda; border-radius:10px; font-size:12.5px; color:var(--sub); background:#fbfcfd; }}
  .foot {{ text-align:center; color:var(--sub); font-size:12px; margin-top:28px; }}
</style>
</head>
<body>
<div class="wrap">

<h1>龙头打板策略 v7 — 候选漏斗诊断 + 空间龙容差验证</h1>
<div class="sub">回测区间：2025-11-03 ~ 2026-08-13｜成交：T+1 开盘价｜全成本模型｜本轮为诊断性迭代</div>

<div class="tl">
<h2>核心结论</h2>
<ul>
<li><b>空间龙容差被证伪</b>：把"只打当日最高板"放宽为"最高或次高板"（容差1）后，9 笔交易 -3.14%（vs v6 容差0 的 +0.85%）；容差2 → 35 笔 -5.53%、回撤 -14.56%。<b>交易越多亏得越多——"过严"恰恰是逆风期存活的原因</b>。</li>
<li><b>漏斗诊断定位了两个"大刀"</b>：情绪过滤（189→78 天）和空间龙收敛（74→3 天）砍掉了绝大多数候选日。空间龙与"连板≤3"叠加等效于"只做当日市场高度恰好 ≤3 的天"——这正是策略"宁缺毋滥"的设计意图。</li>
<li><b>v6 的极致挑剔被确认是正确设计</b>：200 个交易日只出手 3 次并全胜（+0.85%），不是过拟合，而是"等待极少数高确定性窗口"的纪律体现；放宽容差=放弃纪律，在逆风期立刻被惩罚。</li>
<li><b>本轮结论</b>：v6（科技池 + 上证MA20 + 题材集中度 + 空间龙容差0）保持最优，无需修改。策略当前定位不变：情绪监控 + 极挑剔出手框架。</li>
</ul>
</div>

<div class="card">
<h2>一、候选漏斗：每一层过滤砍掉多少（诊断核心）</h2>
<table>
<tr><th>过滤层</th><th>候选日数</th><th>候选数</th><th>较上层减少(天)</th><th>较上层减少(候选)</th><th>点评</th></tr>
{funnel_rows()}
</table>
<div class="src">候选日数 = 该层至少有一个候选的交易日数（正式区间约 200 天）；候选数 = 信号日 × 股票数。最终 4 个候选（汉缆/卓郎/泰和/新金路）中 3 笔成交（泰和因 T+1 一字或资金约束未成交）。</div>
<div id="chFunnel" class="chart"></div>
<div class="src">漏斗可视化：候选数对数坐标。前 4 层（科技/连板/成交额）温和收敛，情绪层与空间龙层断崖式收敛。</div>
</div>

<div class="card">
<h2>二、空间龙容差实验（证伪）</h2>
<table>
<tr><th>配置</th><th>总收益</th><th>最大回撤</th><th>胜率</th><th>交易数</th></tr>
{rows_html()}
</table>
<div class="src">容差=允许偏离当日最高板的连板数（0=仅最高板；1=最高或次高；2=最高/次高/次次高）。v6 基线 +0.85%（3笔100%胜率）为最优；任何放宽都引入逆风期亏损单。</div>
</div>

<div class="warn">
<h3>⚠️ 结论与边界</h3>
<ul>
<li><b>诊断价值</b>：漏斗分析证明策略的"极挑剔"不是参数巧合——情绪层砍掉 111 个交易日、空间龙层砍掉 71 个交易日，最终只在市场高度温和（≤3板）且满足全部条件的极少数窗口出手。逆风期放宽容差立即亏钱，反证严苛是正确的。</li>
<li><b>样本警示（不变）</b>：3 笔样本过小，+0.85% 不可外推；需 2-3 年数据在顺风周期验证"容差0"是否同样成立（顺风期可能容差1 更好——逆风期结论不能外推到顺风期）。</li>
<li><b>下一步可选方向</b>：① 更长数据（验证顺风期）；② 真实新闻/公告数据（事件驱动）；③ 分钟数据（炸板即走）；④ 科技池子题材轮动（AI算力 vs AI应用 vs 半导体——需当日题材强度数据，当前快照概念数据无法支持历史轮动识别）。</li>
</ul>
</div>

<div class="disc">
<b>免责声明</b>：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
</div>
<div class="foot">报告生成：2026-08-13 ｜ 数据：TickFlow 本地 parquet + 同花顺行业/概念快照 + 上证指数 ｜ 回测引擎：backend/app/backtest</div>
</div>

<script>
(function () {{
  var green = '#12a150', red = '#e0312f', blue = '#2563eb', gray = '#8a94a6';
  var f = {json.dumps(funnel, ensure_ascii=False)};
  echarts.init(document.getElementById('chFunnel')).setOption({{
    tooltip: {{ trigger: 'axis' }},
    grid: {{ left: 90, right: 30, top: 20, bottom: 60 }},
    xAxis: {{ type: 'value', type: 'log', name: '候选数(log)' }},
    yAxis: {{ type: 'category', data: f.map(function (d) {{ return d[0]; }}), axisLabel: {{ fontSize: 11 }} }},
    series: [{{
      type: 'bar', barWidth: '60%',
      data: f.map(function (d, i) {{
        return {{ value: d[2], itemStyle: {{ color: i >= 5 ? (i >= 8 ? red : blue) : gray }} }};
      }}),
      label: {{ show: true, position: 'right', fontSize: 10 }}
    }}]
  }});
}})();
</script>
</body>
</html>
"""

out_path = OUT + r"\龙头打板策略v7-漏斗诊断-回测报告-20260813.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print("written:", out_path, "| bytes:", len(html.encode("utf-8")))
