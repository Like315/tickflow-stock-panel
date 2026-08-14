# -*- coding: utf-8 -*-
"""从回测 JSON 结果生成 HTML 报告（龙头打板策略案例 + 回测结果 + 防作弊审计）。"""
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT_DIR = r"D:\MyTickFlowStockPanel\output"
data = json.load(open(OUT_DIR + r"\limit_up_leader_backtest2.json", encoding="utf-8"))
periods = json.load(open(OUT_DIR + r"\limit_up_leader_periods.json", encoding="utf-8"))
equity_bench = json.load(open(OUT_DIR + r"\equity_bench.json", encoding="utf-8"))

def fmt(x, as_pct=True, digits=2):
    if x is None:
        return "—"
    if as_pct:
        return f"{x*100:.{digits}f}%"
    return f"{x:.{digits}f}"

# 采样净值曲线（每组最多 60 点）
def sample_curve(rows, max_pts=60):
    if not rows:
        return []
    n = len(rows)
    step = max(1, n // max_pts)
    out = []
    for p in rows[::step]:
        out.append({"d": str(p["date"])[:10], "v": round(float(p["value"]) / 1_000_000.0, 4)})
    if rows[-1] not in rows[::step]:
        out.append({"d": str(rows[-1]["date"])[:10], "v": round(float(rows[-1]["value"]) / 1_000_000.0, 4)})
    return out

# 上证指数曲线（归一化）
bench_curve = []
try:
    import polars as pl
    bench_df = pl.scan_parquet(r"D:\MyTickFlowStockPanel\data\kline_index_daily\**\*.parquet").sort("date").collect()
    bc = [r for r in bench_df.iter_rows(named=True) if str(r.get("symbol", "")) == "000001.SH" and r.get("close")]
    if bc:
        base = bc[0]["close"]
        for r in bc[::max(1, len(bc) // 60)]:
            bench_curve.append({"d": str(r["date"])[:10], "v": round(float(r["close"]) / base, 4)})
except Exception as e:
    print("bench err", e)

# 生成各组的 ECharts 数据
curves = {}
for name, row in data.items():
    curves[name] = sample_curve(row.get("equity_curve") or [])
curves["上证指数(归一)"] = bench_curve

order = ["无脑打板(对照)", "基准-2板上+情绪80+持1天", "情绪更严-阈值100", "情绪更松-阈值60", "关闭情绪过滤", "三板上-只打3连板+", "首板也打-1板起", "持有2天-给冲高机会"]
emotion_cases = ["关闭情绪过滤", "情绪更松-阈值60", "基准-2板上+情绪80+持1天", "情绪更严-阈值100"]
emotion_labels = ["关闭", "阈值60", "阈值80", "阈值100"]
emotion_vals = [data[c]["total_return"] for c in emotion_cases]

period_names = ["2025-11~2026-01", "2026-02~2026-04", "2026-05~2026-08"]
period_ret = [periods[p]["total_return"] for p in period_names]
period_bench = [periods[p]["benchmark"] for p in period_names]
period_trades = [periods[p]["n_trades"] for p in period_names]

def rows_html():
    out = []
    for name in order:
        r = data.get(name)
        if not r:
            continue
        exec_ = r.get("execution") or {}
        out.append(f"""<tr><td>{name}</td><td>{fmt(r.get('total_return'))}</td><td>{fmt(r.get('max_drawdown'))}</td>
<td>{fmt(r.get('win_rate'))}</td><td>{r.get('n_trades')}</td><td>{fmt(r.get('avg_pnl'))}</td>
<td>{r.get('avg_holding_days')}</td><td>{fmt(r.get('excess'))}</td>
<td>{exec_.get('buy_limit_up', 0)} / {exec_.get('sell_limit_down', 0)} / {exec_.get('pending_exit', 0)}</td></tr>""")
    return "\n".join(out)

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>龙头打板策略 v1 — 回测报告（防作弊口径）</title>
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
  .red {{ color:var(--red); font-weight:600; }}
  .green {{ color:var(--green); font-weight:600; }}
  .chart {{ width:100%; height:360px; margin:10px 0; }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  @media (max-width:760px) {{ .grid2 {{ grid-template-columns:1fr; }} }}
  .src {{ font-size:12px; color:var(--sub); margin-top:6px; }}
  .rule {{ background:#f8f9fb; border:1px solid #e3e7ef; border-radius:10px; padding:14px 16px; margin:8px 0; font-size:13.5px; }}
  .rule b {{ color:var(--blue); }}
  .ok {{ color:var(--green); font-weight:600; }}
  .warn {{ background:#fff7e8; border:1px solid #f3d9a4; border-radius:12px; padding:16px 18px; margin-bottom:20px; }}
  .warn h3 {{ color:#b45309; margin-bottom:8px; }}
  .warn ul {{ padding-left:20px; font-size:14px; }}
  .disc {{ margin-top:24px; padding:16px 18px; border:1px dashed #c9cfda; border-radius:10px; font-size:12.5px; color:var(--sub); background:#fbfcfd; }}
  .foot {{ text-align:center; color:var(--sub); font-size:12px; margin-top:28px; }}
</style>
</head>
<body>
<div class="wrap">

<h1>龙头打板策略 v1 — 策略案例与回测报告</h1>
<div class="sub">标的：A 股全市场 ｜ 回测区间：2025-11-03 ~ 2026-08-13（约 200 交易日，190 万+行日K）｜ 成交口径：T+1 开盘价（open_t+1）｜ 数据：TickFlow 本地 parquet（原始价判断涨停 + 复权价计算收益）</div>

<div class="tl">
<h2>核心结论（先看这里）</h2>
<ul>
<li><b>诚实结果</b>：v1 策略在样本区间（上证仅 -1.25% 的横盘期）<b>全部变体均亏损</b>（-51% ~ -86%）——该区间处于打板逆风周期，任何打板方法都难赚钱。</li>
<li><b>最重要的实证</b>：情绪过滤<b>单调减亏</b>——关闭时 -85.7%，阈值 60→-78.0%，80→-70.0%，100→-50.6%。<b>"退潮期空仓"被数据验证</b>，这是策略最有价值的规则。</li>
<li><b>情绪周期真实存在</b>：子区间 2026-02~04（打板顺风期）仅 -6.5%，2025-11~01 与 2026-05~08（逆风期）均 -40%。同一策略，环境决定生死。</li>
<li><b>防作弊</b>：回测全链路使用 T+1 开盘成交、一字涨停拦截（本组被拦 391 次）、跌停挂起顺延、全成本模型；信号仅用 T 日收盘数据，审计清单见第四节。</li>
<li><b>策略定位</b>：当前版本为"纪律框架 + 回测底座"，<b>不可直接实盘</b>；需在打板顺风周期（涨停家数高位 + 连板高度持续）验证后再小仓试运行。</li>
</ul>
</div>

<div class="card">
<h2>一、策略案例（可执行规则 v1）</h2>
<div class="rule"><b>入仓信号（T 日收盘后生成）</b>：① 当日收盘封涨停（signal_limit_up）；② 连板数 ≥ 2（默认；3 为保守档）；③ 当日成交额 ≥ 3 亿元（流动性过滤）；④ 全市场当日收盘涨停家数 ≥ 80（情绪过滤，缺省关闭时为空仓纪律）。</div>
<div class="rule"><b>成交（T+1）</b>：开盘价买入；T+1 一字涨停买不进（引擎拦截，不追）；停牌跳过。</div>
<div class="rule"><b>卖出</b>：T+1 开盘卖出（max_hold=1，隔日兑现）；或持至 T+2 给冲高机会；止损 -6%（价格止损，触发即砍）。</div>
<div class="rule"><b>仓位与资金</b>：最多同时 4 只（max_positions=4），单票 ≤ 20%，总仓位 ≤ 80%（max_exposure=0.8），初始资金 100 万。</div>
<div class="rule"><b>成本模型</b>：佣金万 2 双边 + 印花税卖出 0.05% + 滑点 5bp。</div>
<div class="rule"><b>龙头排序</b>：候选按 连板数×50% + 成交额×30% + 换手率×20% 评分，资金优先流向分数最高的（空间龙头/中军）。</div>
<div class="src">实现位置：backend/app/strategy/builtin/limit_up_leader.py（接入现有矩阵回测引擎）；回测脚本：backend/scripts/bt_limit_up_leader2.py。</div>
</div>

<div class="card">
<h2>二、回测结果：参数敏感性（8 组）</h2>
<table>
<tr><th>参数组合</th><th>总收益</th><th>最大回撤</th><th>胜率</th><th>交易数</th><th>平均单笔</th><th>平均持有(天)</th><th>超额收益(对上证)</th><th>拦截计数 买一字/卖跌停/挂起</th></tr>
{rows_html()}
</table>
<div class="src">全部组合：max_positions=4、单票≤20%、总仓≤80%、佣金万2+印花税0.05%+滑点5bp、T+1 开盘成交。超额收益 = 策略总收益 − 上证区间收益（-1.25%）。拦截计数：buy_limit_up（一字板买不进）/ sell_limit_down（跌停卖不出）/ pending_exit（挂起顺延）。</div>
</div>

<div class="card">
<h2>三、两个关键图表</h2>
<div class="grid2">
<div>
<div id="chEmotion" class="chart"></div>
<div class="src">情绪过滤阈值越高 → 交易越少 → 亏损越小（单调）。横轴为全市场当日涨停家数下限。</div>
</div>
<div>
<div id="chPeriod" class="chart"></div>
<div class="src">同一策略在不同市场环境下的表现：2026 春季打板顺风期接近盈亏平衡，秋冬逆风期大幅回撤。绿柱=亏损（A 股惯例红涨绿跌，此处均为负值）。</div>
</div>
</div>
<div id="chEquity" class="chart" style="height:380px"></div>
<div class="src">净值曲线（百万）：基准（2板+情绪80+持1天）vs 关闭情绪过滤 vs 上证指数（归一化）。可见情绪过滤版本回撤显著更小。</div>
</div>

<div class="card">
<h2>四、防作弊审计清单（回测模块逐项核实）</h2>
<table>
<tr><th style="width:260px">审计项</th><th>结论</th><th>证据 / 说明</th></tr>
<tr><td>未来函数（look-ahead）：信号是否只用 T 日及以前数据</td><td class="ok">通过</td><td>连板数 consecutive_limit_ups 为同 symbol 内 cum_sum 纯历史递推（pipeline.py）；涨停价用前收盘 × 涨跌幅比例计算，无未来引用。</td></tr>
<tr><td>成交价：是否用信号当日收盘价"预知"成交</td><td class="ok">通过</td><td>open_t+1 口径：信号右移 1 天，用 T+1 开盘价成交；warmup 数据仅算特征不参与交易（测试断言）。</td></tr>
<tr><td>一字涨停能否"排到"</td><td class="ok">通过</td><td>引擎在成交日检测四价相同+封板，buy_limit_up 拦截——全区间共拦截 391 次，未产生一字买入。</td></tr>
<tr><td>跌停卖不出</td><td class="ok">通过</td><td>sell_limit_down 拦截 + pending_exit 挂起次日顺延卖出（本组 6 次），不虚构成交。</td></tr>
<tr><td>停牌 / 无价格</td><td class="ok">通过</td><td>buy_suspended / sell_suspended / sell_no_future 均有计数，不成交不捏造。</td></tr>
<tr><td>交易成本</td><td class="ok">通过</td><td>佣金万 2 双边 + 印花税卖出 0.05% + 滑点 5bp，买卖双边计费。</td></tr>
<tr><td>情绪过滤前视</td><td class="ok">通过</td><td>全市场涨停家数统计使用 T 日 limit_up_locked（当日收盘封板），与 T+1 成交无重叠。</td></tr>
<tr><td>样本内优化 / walk-forward 隔离</td><td class="ok">通过（未使用）</td><td>本次未做参数寻优（避免过拟合）；引擎自带 WF 的 IS/OOS 间隔 1 天 + IS 强制 position 模式（代码注释明确堵前视）。</td></tr>
<tr><td>数据口径</td><td class="ok">通过</td><td>涨停判断用 raw_close（交易所原始价），收益计算用复权价——正确处理除权日跳空。</td></tr>
<tr><td>已知小问题</td><td class="warn">注意</td><td>benchmark 接口 get_index_daily 取数为空（数据存在但接口未通），本报告用上证 kline_index_daily 手工对照；回测全市场 5400+ 股票，北交所/ST 的涨停判定沿用统一 limit 规则，可能与交易所精确口径有细微差异。</td></tr>
</table>
</div>

<div class="warn">
<h3>⚠️ 结论与使用边界</h3>
<ul>
<li><b>样本区间结论</b>：2025-11 ~ 2026-08 为打板逆风周期，策略所有变体亏损；情绪过滤被证明能大幅减亏（-86% → -51%），但<span class="red">尚不能证明该策略能盈利</span>。</li>
<li><b>下一版方向（v2）</b>：① 龙头识别细化——按当日主线题材分组取板块内"最高连板+最大成交额"而非全局排序；② 卖出端引入"次日不板即走"（用 T+1 是否封板作为 exit 信号，而非固定持有期）；③ 情绪过滤改多指标（涨停家数 + 连板高度 + 炸板率三维）；④ 延长数据至 2-3 年覆盖完整牛熊周期后再评估。</li>
<li><b>当前应做什么</b>：该策略在当前环境下<b>保持空仓是正确动作</b>（情绪阈值不满足即不交易）。可将其作为"市场情绪监控 + 打板纪律框架"使用，待顺风周期出现再验证。</li>
</ul>
</div>

<div class="disc">
<b>免责声明</b>：以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
</div>
<div class="foot">报告生成：2026-08-13 ｜ 数据：TickFlow 本地历史 parquet（2025-08 ~ 2026-08） ｜ 回测引擎：backend/app/backtest（矩阵原生 + 组合撮合）</div>
</div>

<script>
(function () {{
  var red = '#e0312f', green = '#12a150', blue = '#2563eb', gray = '#8a94a6';

  echarts.init(document.getElementById('chEmotion')).setOption({{
    tooltip: {{ trigger: 'axis', formatter: function (ps) {{ return ps[0].name + '：' + (ps[0].value * 100).toFixed(1) + '%'; }} }},
    grid: {{ left: 55, right: 20, top: 20, bottom: 30 }},
    xAxis: {{ type: 'category', data: {json.dumps(emotion_labels, ensure_ascii=False)}, name: '情绪阈值(涨停家数)' }},
    yAxis: {{ type: 'value', axisLabel: {{ formatter: '{{value}}%' }} }},
    series: [{{
      type: 'bar', barWidth: '45%',
      data: {json.dumps([round(x * 100, 1) for x in emotion_vals], ensure_ascii=False)},
      itemStyle: {{ color: green }},
      label: {{ show: true, position: 'top', formatter: function (p) {{ return p.value + '%'; }} }}
    }}]
  }});

  echarts.init(document.getElementById('chPeriod')).setOption({{
    tooltip: {{ trigger: 'axis' }},
    legend: {{ data: ['策略收益', '上证指数'], top: 0 }},
    grid: {{ left: 55, right: 20, top: 30, bottom: 30 }},
    xAxis: {{ type: 'category', data: {json.dumps(period_names, ensure_ascii=False)} }},
    yAxis: {{ type: 'value', axisLabel: {{ formatter: '{{value}}%' }} }},
    series: [
      {{ name: '策略收益', type: 'bar', barWidth: '32%', data: {json.dumps([round(x * 100, 1) for x in period_ret], ensure_ascii=False)}, itemStyle: {{ color: green }} }},
      {{ name: '上证指数', type: 'bar', barWidth: '32%', data: {json.dumps([round(x * 100, 1) for x in period_bench], ensure_ascii=False)}, itemStyle: {{ color: gray }} }}
    ]
  }});

  var curves = {json.dumps(curves, ensure_ascii=False)};
  var baseName = '基准-2板上+情绪80+持1天';
  var dates = curves[baseName].map(function (p) {{ return p.d; }});
  var s1 = curves[baseName].map(function (p) {{ return p.v; }});
  var s2 = curves['关闭情绪过滤'].map(function (p) {{ return p.v; }});
  var s3 = curves['上证指数(归一)'].map(function (p) {{ return p.v; }});
  echarts.init(document.getElementById('chEquity')).setOption({{
    tooltip: {{ trigger: 'axis' }},
    legend: {{ data: ['龙头打板(情绪80)', '龙头打板(无情绪过滤)', '上证指数(归一)'], top: 0 }},
    grid: {{ left: 60, right: 20, top: 30, bottom: 30 }},
    xAxis: {{ type: 'category', data: dates }},
    yAxis: {{ type: 'value', scale: true, axisLabel: {{ formatter: '{{value}}' }} }},
    series: [
      {{ name: '龙头打板(情绪80)', type: 'line', showSymbol: false, data: s1, lineStyle: {{ color: blue, width: 2 }} }},
      {{ name: '龙头打板(无情绪过滤)', type: 'line', showSymbol: false, data: s2, lineStyle: {{ color: green, width: 2 }} }},
      {{ name: '上证指数(归一)', type: 'line', showSymbol: false, data: s3, lineStyle: {{ color: gray, width: 1.5 }} }}
    ]
  }});
}})();
</script>
</body>
</html>
"""

out_path = r"D:\MyTickFlowStockPanel\output\龙头打板策略v1-回测报告-20260813.html"
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)
print("written:", out_path, "| bytes:", len(html.encode("utf-8")))
