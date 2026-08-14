# -*- coding: utf-8 -*-
"""生成：炒股养家心法体系 · 淘股吧一手资料与实证对照"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>炒股养家心法 · 龙头-补涨-切换体系与实证对照</title>
<style>
  :root { --bg:#f7f8fa; --card:#fff; --ink:#1a2332; --sub:#64748b; --line:#e5e9f0; --red:#d43d3d; --green:#0d9d6e; --blue:#2563eb; --amber:#b45309; --chip:#eef2f7; }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { background:var(--bg); color:var(--ink); font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; padding:32px 20px 60px; }
  .wrap { max-width:1060px; margin:0 auto; }
  h1 { font-size:25px; font-weight:800; letter-spacing:.5px; }
  .sub { color:var(--sub); font-size:14px; margin:8px 0 4px; }
  .period { display:inline-block; background:var(--chip); border-radius:20px; padding:4px 14px; font-size:12px; color:var(--sub); margin-top:10px; }
  .kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin:24px 0; }
  .kpi { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px 20px; }
  .kpi .v { font-size:23px; font-weight:800; }
  .kpi .l { font-size:12px; color:var(--sub); margin-top:4px; line-height:1.5; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:22px 24px; margin:18px 0; }
  .card h2 { font-size:17px; margin-bottom:12px; }
  .card h3 { font-size:14.5px; margin:14px 0 8px; color:var(--blue); }
  table { width:100%; border-collapse:collapse; font-size:13.5px; }
  th { text-align:left; padding:9px 10px; color:var(--sub); font-weight:600; border-bottom:2px solid var(--line); white-space:nowrap; }
  td { padding:9px 10px; border-bottom:1px solid var(--line); vertical-align:top; line-height:1.6; }
  .tag { display:inline-block; padding:2px 8px; border-radius:6px; font-size:11px; margin-left:4px; font-weight:600; white-space:nowrap; }
  .tag.yes { background:#e8f7f1; color:var(--green); }
  .tag.part { background:#fff7e6; color:var(--amber); }
  .tag.no { background:#fdeeee; color:var(--red); }
  .quote { border-left:3px solid var(--blue); background:var(--chip); padding:10px 16px; font-size:13px; color:var(--sub); margin:8px 0; line-height:1.8; }
  .quad { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:12px 0; }
  .qcell { border-radius:10px; padding:14px 16px; font-size:13px; line-height:1.7; }
  .q1 { background:#e8f7f1; border:1px solid #bfe5d5; }
  .q2 { background:#fff7e6; border:1px solid #f5d9a8; }
  .q3 { background:#fdeeee; border:1px solid #f2c4c4; }
  .q4 { background:#eef2f7; border:1px solid #d5dce8; }
  .qcell b { font-size:14px; }
  .warn { background:#fff8ec; border:1px solid #f5d9a8; border-radius:12px; padding:16px 20px; margin:18px 0; font-size:13px; color:#7a5b1e; line-height:1.8; }
  .foot { color:var(--sub); font-size:12px; margin-top:26px; line-height:1.8; border-top:1px solid var(--line); padding-top:14px; }
  ul.tight { margin:6px 0 0 18px; font-size:13.5px; line-height:1.9; }
  .hl { background:linear-gradient(transparent 55%, #ffe08a 85%); }
</style>
</head>
<body>
<div class="wrap">
  <h1>炒股养家心法 · 龙头-补涨-切换体系</h1>
  <div class="sub">淘股吧一手资料整理 + 与我们的 8 轮量化实证逐条对照（数据：2025-08~2026-08 全市场，涨停样本 15,188）</div>
  <span class="period">来源：淘股吧原帖 / m.tgb.cn 养家心法日拆 / 雪球深度解析 / 新浪财经 · 语录均为公开流传版本</span>

  <div class="kpis">
    <div class="kpi"><div class="v">80家</div><div class="l">养家:"行情好猛干,涨停超80家" —— 与我们回测情绪阈值 80 完全一致</div></div>
    <div class="kpi"><div class="v" style="color:var(--green)">≤20%</div><div class="l">养家高潮期仓位上限 —— 与我们"高潮禁入"实证同向,与"高潮重仓"长文相反</div></div>
    <div class="kpi"><div class="v">4阶段</div><div class="l">龙头→补涨→空仓→切换,情绪×指数四象限定位</div></div>
    <div class="kpi"><div class="v">6%</div><div class="l">养家单票仓位上限(核心龙头10%) —— 与"满仓单吊"彻底相反</div></div>
  </div>

  <div class="card">
    <h2>一、人物背景（淘股吧脉络）</h2>
    <ul class="tight">
      <li><b>炒股养家</b>：A股传奇游资，中年辞职炒股，大亏后悟道，在淘股吧毫无保留分享心法，被尊为情绪龙头战法开山宗师。名言"一本养家心法名扬天下"。</li>
      <li><b>92科比</b>：90后游资，2015年10万入市破产送外卖，靠抄养家作业翻身，5年做到数亿，淘股吧百万杯半年收益307%。他把自己对养家体系的实战化总结为<b>"龙头、补涨、切换、空仓"</b>四阶段，即你问的这套体系。</li>
      <li><b>涅槃重升</b>：另一位顶级游资，从"情绪+指数"两个维度描述同一套周期（情绪好/坏 × 指数好/坏 → 四象限），与科比互为印证。</li>
    </ul>
    <div class="quote">"为什么会有不同的策略，本质上还是养家总结出来的在那个特定的环境下，胜率和存活率最高的手段。龙头是策略，补涨是策略，空仓是策略，切换是策略。" —— 淘股吧原帖</div>
  </div>

  <div class="card">
    <h2>二、养家心法总纲（六脉神剑 + 语录原文）</h2>
    <table>
      <thead><tr><th style="width:200px">心法</th><th>原文/释义</th></tr></thead>
      <tbody>
        <tr><td>情绪揣摩</td><td>"本人理论体系的核心思想是基于对市场情绪的揣摩，进而判断风险和收益的比较"——情绪是短线第一生产力，技术题材只是工具</td></tr>
        <tr><td>龙头买卖</td><td>"高手买入龙头，超级高手卖出龙头"——买点易学，卖点（一致时撤退）才是段位分水岭</td></tr>
        <tr><td>顺势而为</td><td>"别人贪婪时我更贪婪，别人恐慌时我更恐慌"——不是反着来，是<b>跟随情绪但比大众快半步</b>（初期贪婪时激进，极致一致时先撤）</td></tr>
        <tr><td>心中无顶底</td><td>"敢于大盘低位空仓，敢于大盘高位满仓；心中无顶底，操作自随心"——不看价格高低，只看赚钱效应/亏钱效应</td></tr>
        <tr><td>永不止损止盈</td><td>"永不止损，永不止盈，只有进场、出局。买入机会，卖出风险"——不设固定点位，按"条件破坏即出局"规则执行，不看成本</td></tr>
        <tr><td>人气所在</td><td>"得散户心者得天下，人气所向，牛股所在"——龙头必须有广泛群众基础，无人气的冷门股不做</td></tr>
      </tbody>
    </table>
    <h3>其他高频金句</h3>
    <ul class="tight">
      <li>"行情好猛干（涨停超80家），行情差空仓（跌停超20家）" —— 用<b>客观数据</b>量化情绪，拒绝主观</li>
      <li>"买入机会，卖出风险，只做对的交易，胜负交给概率"</li>
      <li>"预判→试错→确认→加仓"——操作流程四步</li>
      <li>"仓位由赢面决定"：赢面&lt;60%观望/空仓；60-70%小仓≤30%；70-80%中仓；80-90%大仓；&gt;90%才满仓</li>
      <li>"重仓买入的一刹那，要有后市还有3到5个涨停空间的判断"——大局观</li>
    </ul>
  </div>

  <div class="card">
    <h2>三、情绪周期 × 仓位（养家体系的核心骨架）</h2>
    <table>
      <thead><tr><th>周期</th><th>盘面特征</th><th>仓位</th><th>操作</th></tr></thead>
      <tbody>
        <tr><td>极致冰点</td><td>高位集体跌停、连板断层、炸板大面、做多资金绝迹</td><td>0-5%（可空仓）</td><td>只做极小仓试错：逆势独立活口 / 超跌首板。冰点不是抄底点，是观察拐点窗口</td></tr>
        <tr><td>弱修复</td><td>跌停减少、零星首板、连板小幅修复，赚钱效应分散</td><td>10-20%</td><td>只做辨识度高的老龙头/新首板，快进快出不格局</td></tr>
        <tr><td>强回暖/启动</td><td>板块集体联动、连板梯队完整、炸板少、隔日溢价稳定，主线明确</td><td><b>40-50%</b></td><td>全年主要盈利阶段：分歧低吸、弱转强优先，聚焦主线放弃杂毛</td></tr>
        <tr><td>主升高潮</td><td>批量连板、一字增多、缩量加速、全民打板</td><td><b>≤20%</b></td><td><b>卖在一致</b>：逐步减仓，缩量加速板坚决不接力，只留核心龙头，清掉所有后排</td></tr>
        <tr><td>分歧退潮</td><td>高位龙头断板、炸板率飙升、高位大面横行</td><td>清仓</td><td>禁止低吸、禁止接力、禁止抄底——"散户多死在退潮期博反弹"</td></tr>
        <tr><td>深度亏钱潮</td><td>修复夭折、热点熄火、短线生态崩坏</td><td><b>0%</b></td><td>空仓为王，规避系统性风险，等待下一轮冰点</td></tr>
      </tbody>
    </table>
    <div class="quote">分仓铁律：单票 ≤ 总仓 6%（核心龙头放宽至 10%，<b>绝不满仓单票</b>）；持股 2-3 只为最优，最多 4 只。<br>止损三件套：日内 -3%~-5% 无条件减仓/离场；龙头次日无溢价/高开低走/放量破位直接离场；确认退潮期无论盈亏全部清仓。<b>被套绝不补仓。</b></div>
  </div>

  <div class="card">
    <h2>四、"龙头-补涨-切换-空仓"四阶段体系（92科比 × 涅槃重升）</h2>
    <div class="sub">情绪（赚钱效应） × 指数（大盘环境） → 四象限，每个象限对应一种策略</div>
    <div class="quad">
      <div class="qcell q1"><b>情绪好 + 指数好 → 做龙头</b><br>市场整体向上，情绪助涨。只需找到被情绪选中的那部分（人气核心股），买进躺赢。主升段专做龙头，让渡 10 个点利润/亏损的从容。</div>
      <div class="qcell q2"><b>情绪好 + 指数差 → 做补涨</b><br>整体跌多涨少，但涨的票有明显题材共性。回避高位股，转向<b>低位首板/一进二</b>，享受情绪推动低位票的溢价。</div>
      <div class="qcell q3"><b>情绪差 + 指数差 → 空仓</b><br>情绪助跌，领跌品种杀跌、补涨品种跟随杀跌。赚钱效应演绎到极致后力竭，进入主杀阶段——<b>立即启动空仓策略</b>，回避一切大热题材。</div>
      <div class="qcell q4"><b>情绪差 + 指数好 → 切换</b><br>整体企稳，但情绪还在补跌互砍。选与主跌品种<b>完全不同属性</b>的方向，预判买新的主流板块人气核心——等下一阶段龙头诞生。</div>
    </div>
    <h3>补涨 vs 切换（判别核心：原属性是加分还是减分）</h3>
    <table>
      <thead><tr><th style="width:100px">维度</th><th>补涨</th><th>切换</th></tr></thead>
      <tbody>
        <tr><td>原题材状态</td><td>原有属性都是<b>加分项</b>，原题材没有大幅杀跌、亏钱效应未扩散</td><td>原有属性都是<b>减分项</b>，原题材大幅杀跌，资金闻原题材而逃</td></tr>
        <tr><td>资金心理</td><td>龙头不停涨 → 眼红效应 + 模仿加速 + 赚钱效应溢出（"踏空比套牢难受"）</td><td>老周期存量萎缩（老人不断亏钱被淘汰）+ 新周期增量加入 → 改变共识</td></tr>
        <tr><td>策略</td><td>做低位：低位首板、1进2，最好带原属性</td><td>找与主跌品种完全不同、甚至跷跷板的板块，已涨出一定高度的标的</td></tr>
        <tr><td>高度约束</td><td><b>补涨高度受原龙头高度压制</b></td><td><b>新标的高度不受原龙头压制</b>（完全新的故事）</td></tr>
        <tr><td>结束信号</td><td>补涨龙力竭、补涨四面开花后，参与高低位的资金都开始亏钱 → 进入主杀</td><td>切换确认：新板块出现连板龙头，形成板块效应（案例：2021.2 仁东控股 9 天 8 板）</td></tr>
      </tbody>
    </table>
    <div class="quote"><b>2021 年 2 月实战案例（淘股吧原帖）</b>：抱团股茅台 2.18 高开后下杀 → 2.22 茅台 -7%、阳光电源 -17%（但未确认）→ 2.24 茅台再 -5%（高点累计 -18%），紫金矿业补涨末段放量不板，<b>抱团瓦解确认</b>；同日仁东控股早盘快速上板开启 9 天 8 板，垃圾小盘投机派形成板块效应第一天。鲸落万物生——切换的本质是改变共识，契机 = 存量萎缩 + 增量加入。</div>
  </div>

  <div class="card">
    <h2>五、与我们的 8 轮实证逐条对照（最有价值的部分）</h2>
    <table>
      <thead><tr><th style="width:210px">养家/科比主张</th><th>我们的实证结论</th><th style="width:80px">判定</th></tr></thead>
      <tbody>
        <tr><td>"行情好猛干（涨停超80家），行情差空仓"</td><td>情绪过滤用<b>涨停家数 ≥80</b> 作为出手门槛（单调减亏 -85.7%→-6%）。两个独立来源得到同一个阈值——这是最强的互相印证</td><td><span class="tag yes">✅ 完全一致</span></td></tr>
        <tr><td>高潮期 ≤20% 撤退、退潮期 0%</td><td>"高潮禁入"实证有效（+18.9%）、退潮空仓被反复验证；而"高潮重仓"（某长文主张）被证伪（+3.87%）——<b>养家站在我们数据这边</b></td><td><span class="tag yes">✅ 验证</span></td></tr>
        <tr><td>买在分歧，卖在一致</td><td>我们测出"高潮=情绪顶"（涨停家数滞后于价格）；"一致加速后追入=接盘"（v4 高位跳空止损单平均 -15.5%）——"卖在一致"正是防跳空的前置手段</td><td><span class="tag yes">✅ 验证</span></td></tr>
        <tr><td>只做龙头，不碰后排杂毛</td><td>v3 概念内龙头（买情绪最高点）反而更差 -65.7% → 我们更精确的结论是"只做<b>主线板块内的空间龙</b>（行业≥5 家共振）"——比"龙头"更严一层，方向一致</td><td><span class="tag yes">✅ 验证</span></td></tr>
        <tr><td>弱市低吸回调（不接飞刀）</td><td>低吸策略回撤最小（-5.0%）；"缩量回踩 5/10 日线"与我们"MA20>MA60 趋势内回踩"同构；我们实证：<b>无趋势保护的低吸=接飞刀</b>（+5.27% 且回撤 -37.7%），必须叠加指数开关——这恰好是"情绪差指数差→空仓"的量化版</td><td><span class="tag yes">✅ 验证（有前提）</span></td></tr>
        <tr><td>情绪好指数差 → 补涨（低位首板）</td><td>v8 子题材轮动 top1 逆风期被证伪（+20 笔交易全亏）——但注意：我们测的是<b>逆风期</b>，养家说补涨只发生在"情绪好指数差"象限；我们的"回避 4 板+ 高位"与"补涨做低位"完全同向</td><td><span class="tag part">⚠️ 部分验证</span></td></tr>
        <tr><td>主升段专做龙头（可以做到 5-6 板）</td><td>我们实测"连板≤3 最优"是<b>逆风期</b>结论；养家的"主升段做龙头"需要"情绪好指数好"象限才能成立——两者不矛盾，<b>主升期跟龙头、非主升期回避高位</b>，这是同一枚硬币的两面</td><td><span class="tag part">⚠️ 需区分周期</span></td></tr>
        <tr><td>分仓铁律：单票≤6%、2-3 只最优</td><td>满仓单吊是打板第一杀手（拓日新能 -24.67%）；我们所有策略 max_positions=4、单票 ≤20% 起步——养家更严（6%），因为他是极端短线；2 万块资金下"2-3 只"天然成立</td><td><span class="tag yes">✅ 验证</span></td></tr>
        <tr><td>"永不止损，只有出局"</td><td>解读：不是不设止损，是<b>按条件出局不看成本</b>。与我们的"止损触发即砍、禁止事后宽松"本质一致；但我们的实证补充了关键盲区——<b>跳空穿透</b>（7% 概率低开 -3%+），出局规则要配合"回避高位 + 仓位前置"才防得住</td><td><span class="tag yes">✅ 同向（补盲区）</span></td></tr>
        <tr><td>"不要量化，模糊的正确"</td><td>我们 8 轮回测发现：<b>模糊框架（情绪/周期）是对的，但精细参数（阈值/均线）在同区间会过拟合</b>——养家"模糊的正确"与我们的"walk-forward 验证"殊途同归：框架可信，参数存疑</td><td><span class="tag part">⚠️ 方法论一致</span></td></tr>
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2>六、对 2 万块初学者的落地建议（养家路线图）</h2>
    <ul class="tight">
      <li><b>阶段 1（现在）</b>：只做一件事——<b>每日记录情绪</b>：涨停家数、最高连板、炸板率、昨日涨停今日溢价。练"模糊识别周期"（冰点/回暖/高潮/退潮），这是养家体系的骨架，0 成本</li>
      <li><b>阶段 2（1-3 个月模拟）</b>：套用四象限——"情绪好指数好→只观察龙头；情绪好指数差→低位首板；情绪差→直接空仓"。用我们的 v6 科技打板框架当筛选器，每天对照验证</li>
      <li><b>阶段 3（实盘 1 万）</b>：只在"强回暖期（涨停≥80 + 主线明确）"出手，单票 ≤ 1/3 仓位（2 万拆 3 只），买在分歧低吸（弱转强/回踩不破），隔日有溢价就落袋，退潮期无条件空仓</li>
      <li><b>贯穿始终</b>：被套绝不补仓（养家禁令，也是我们实证的满仓单吊教训）；每笔按"出局条件"执行不看成本；记录 100 笔后算胜率/盈亏比，达标才放大</li>
    </ul>
    <div class="warn">⚠️ 两点提醒：① "龙头-补涨-切换"是<b>主升周期的内部演化</b>，我们实测的 2025-11~2026-08 恰是打板逆风期（无主升段），所以四象限中"做龙头/做补涨"两格在该样本里无法用数据验证，需要 2-3 年含主升行情的数据才能检验；② 养家"永不止盈、格局龙头"与超短"隔日落袋"是两种节奏（主升期格局 vs 非主升期落袋），2 万小资金阶段建议先学"落袋"，再学"格局"。以上内容为量化研究与语录整理，不构成投资建议。市场有风险，投资需谨慎。</div>
  </div>

  <div class="foot">
    资料来源：淘股吧原帖《92科比：龙头-补涨-切换-空仓；涅槃重升：情绪+指数》（2021）及回帖全文、m.tgb.cn 养家心法日拆系列、雪球《炒股养家思想分析与总结之二》、新浪财经《第一名 炒股养家》、爱股票语录整理。语录为网络公开流传版本，可能存在传抄差异。实证部分基于 D:\MyTickFlowStockPanel 全市场日线 2025-08~2026-08（涨停样本 15,188）与 8 轮打板策略回测。
  </div>
</div>
</body>
</html>"""

out = r"D:\MyTickFlowStockPanel\output\炒股养家心法-龙头补涨切换体系-实证对照-20260814.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("saved:", out)
