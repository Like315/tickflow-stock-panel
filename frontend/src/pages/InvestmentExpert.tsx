import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  Bot,
  BrainCircuit,
  CircleDollarSign,
  Database,
  ExternalLink,
  FlaskConical,
  Loader2,
  Play,
  ReceiptText,
  RefreshCw,
  Scale,
  ShieldCheck,
  Square,
  Target,
  TrendingUp,
} from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import { api, type InvestmentExpertTrade } from '@/lib/api'
import { cn } from '@/lib/cn'
import { QK } from '@/lib/queryKeys'

function money(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '--'
  return `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}

function signedMoney(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '--'
  const sign = value > 0 ? '+' : value < 0 ? '-' : ''
  return `${sign}¥${Math.abs(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}

function price(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '--'
  return value.toFixed(3)
}

function percent(value: unknown): string {
  if (value == null || value === '') return '--'
  const number = Number(value)
  if (!Number.isFinite(number)) return '--'
  return `${(number * 100).toFixed(2)}%`
}

function multiple(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '--'
  return `${value.toFixed(2)} : 1`
}

function pnlClass(value: number | null | undefined): string {
  if (value == null || value === 0) return 'text-foreground'
  return value > 0 ? 'text-bull' : 'text-bear'
}

function timestamp(value: string | null | undefined): string {
  if (!value) return '暂无估值时间'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

const DECISION_REASON_LABELS: Record<string, string> = {
  vwap_and_opening_range_confirmed: '候选池入选，价格站上 VWAP 且突破盘初高点',
  settled_position_stop_loss: '持仓收益触及止损线',
  settled_position_take_profit: '持仓收益触及止盈线',
  settled_position_max_hold: '持仓达到最长持有期限',
  settled_position_vwap_breakdown: '价格跌破持仓退出 VWAP 阈值',
}

function featureNumber(trade: InvestmentExpertTrade, key: string): number | null {
  const value = trade.decision_features?.[key]
  if (value == null || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function tradeReason(trade: InvestmentExpertTrade): { summary: string; evidence: string } {
  const summary = trade.decision_reason
    ? DECISION_REASON_LABELS[trade.decision_reason] ?? trade.decision_reason
    : '历史成交已保存，但当时的决策原因快照缺失'
  const evidence: string[] = []
  const candidateScore = featureNumber(trade, 'candidate_score')
  const momentum = featureNumber(trade, 'daily_momentum_20d')
  const vwapBias = featureNumber(trade, 'vwap_bias')
  const breakout = featureNumber(trade, 'breakout_pct')
  const probability = featureNumber(trade, 'model_probability')
  if (candidateScore != null) evidence.push(`候选分 ${candidateScore.toFixed(3)}`)
  if (momentum != null) evidence.push(`20日动量 ${percent(momentum)}`)
  if (vwapBias != null) evidence.push(`${vwapBias >= 0 ? '高于' : '低于'}VWAP ${percent(Math.abs(vwapBias))}`)
  if (breakout != null) evidence.push(`突破幅度 ${percent(breakout)}`)
  if (probability != null) evidence.push(`模型概率 ${percent(probability)}`)
  if (trade.execution_reason === 'next_minute_open') evidence.push('下一分钟开盘撮合')
  return { summary, evidence: evidence.join(' · ') }
}

function statusClass(status: string | null | undefined): string {
  if (status === 'promoted' || status === 'succeeded') return 'text-emerald-400 bg-emerald-400/10'
  if (status === 'rejected' || status === 'failed') return 'text-rose-400 bg-rose-400/10'
  return 'text-amber-300 bg-amber-300/10'
}

export function InvestmentExpert() {
  const queryClient = useQueryClient()
  const [previewStock, setPreviewStock] = useState<{ symbol: string; name: string } | null>(null)
  const status = useQuery({
    queryKey: QK.investmentExpertStatus,
    queryFn: api.investmentExpertStatus,
    refetchInterval: 5_000,
  })
  const sessions = useQuery({
    queryKey: QK.investmentExpertSessions,
    queryFn: () => api.investmentExpertSessions(20),
    refetchInterval: 15_000,
  })
  const tradeHistory = useQuery({
    queryKey: QK.investmentExpertTrades,
    queryFn: () => api.investmentExpertTrades(100),
    refetchInterval: 15_000,
  })
  const experiments = useQuery({
    queryKey: QK.investmentExpertExperiments,
    queryFn: () => api.investmentExpertExperiments(20),
    refetchInterval: 15_000,
  })
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['investment-expert'] })
  const start = useMutation({ mutationFn: api.investmentExpertStart, onSuccess: invalidate })
  const stop = useMutation({ mutationFn: api.investmentExpertStop, onSuccess: invalidate })
  const bootstrap = useMutation({
    mutationFn: () => api.investmentExpertBootstrap(3, 50),
    onSuccess: invalidate,
  })
  const train = useMutation({ mutationFn: api.investmentExpertTrain, onSuccess: invalidate })
  const evolve = useMutation({ mutationFn: api.investmentExpertEvolve, onSuccess: invalidate })

  const data = status.data
  const performance = data?.performance
  const historicalTrades = tradeHistory.data?.trades ?? []
  const positionSymbols = useMemo(
    () => Array.from(new Set([
      ...(data?.positions ?? []).map(position => position.symbol),
      ...historicalTrades.map(trade => trade.symbol),
    ])).sort(),
    [data?.positions, historicalTrades],
  )
  const names = useQuery({
    queryKey: QK.instrumentNames(positionSymbols.join(',')),
    queryFn: () => api.instrumentNames(positionSymbols),
    enabled: positionSymbols.length > 0,
    staleTime: 300_000,
  })
  const positionNames = names.data?.names ?? {}
  const busy = Boolean(data?.active_task)
  const activeModel = data?.active_model
  const latestModel = data?.latest_model
  const displayModel = activeModel ?? latestModel
  const protectedMetrics = displayModel?.metrics.protected_test
  const manifest = data?.dataset?.manifest
  const datasetProgress = manifest?.progress as {
    current?: number
    total?: number
    label?: string | null
    pct?: number
  } | undefined

  return (
    <>
      <PageHeader
        title="AI 投资专家"
        subtitle="分钟级模拟盘 · 每日复盘 · 验证门控进化"
        titleExtra={(
          <span className="rounded-full bg-blue-400/10 px-2 py-0.5 text-[10px] font-medium text-blue-300">
            仅模拟盘
          </span>
        )}
        right={(
          <button
            type="button"
            onClick={() => invalidate()}
            className="rounded-btn p-2 text-muted transition-colors hover:bg-elevated hover:text-foreground"
            title="刷新"
          >
            <RefreshCw className={cn('h-4 w-4', status.isFetching && 'animate-spin')} />
          </button>
        )}
      />

      <main className="min-h-full bg-[radial-gradient(circle_at_8%_0%,rgba(59,130,246,0.10),transparent_30%),radial-gradient(circle_at_90%_0%,rgba(139,92,246,0.09),transparent_28%)] px-6 py-5">
        <div className="mx-auto max-w-[1440px] space-y-5">
          <section className="rounded-2xl border border-blue-400/20 bg-surface/85 p-5 shadow-lg shadow-black/5">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex items-start gap-3">
                <div className="rounded-xl bg-blue-400/10 p-2.5 text-blue-300 ring-1 ring-blue-400/20">
                  <Bot className="h-6 w-6" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-base font-semibold">自主模拟交易运行时</h2>
                    <span className={cn(
                      'rounded-full px-2 py-0.5 text-[10px] font-medium',
                      data?.running ? 'bg-emerald-400/10 text-emerald-400' : 'bg-zinc-400/10 text-muted',
                    )}>
                      {data?.running ? '运行中' : '已停止'}
                    </span>
                  </div>
                  <p className="mt-1 max-w-3xl text-xs leading-5 text-muted">
                    开盘后只消费已完成分钟线，信号最早在下一分钟按未复权开盘价模拟成交。风控宪法不可被训练或进化修改。
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {data?.running ? (
                  <ActionButton label="停止盯盘" icon={Square} pending={stop.isPending} onClick={() => stop.mutate()} />
                ) : (
                  <ActionButton label="启动盯盘" icon={Play} pending={start.isPending} disabled={data?.minute_capable === false} onClick={() => start.mutate()} primary />
                )}
                <ActionButton label="构建三年历史样本" icon={Database} pending={bootstrap.isPending || data?.active_task === 'dataset_bootstrap'} disabled={busy || data?.historical_minute_three_year_capable === false} onClick={() => bootstrap.mutate()} />
                <ActionButton label="重新训练" icon={BrainCircuit} pending={train.isPending || data?.active_task === 'model_training'} disabled={busy} onClick={() => train.mutate()} />
                <ActionButton label="发起进化" icon={FlaskConical} pending={evolve.isPending || data?.active_task === 'evolution'} disabled={busy} onClick={() => evolve.mutate()} />
              </div>
            </div>
            {data?.active_task && (
              <div className="mt-4 flex items-center gap-2 rounded-lg border border-amber-300/20 bg-amber-300/5 px-3 py-2 text-xs text-amber-200">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                后台任务进行中：{data.active_task}
                {data.active_task === 'dataset_bootstrap' && datasetProgress?.total
                  ? ` · ${datasetProgress.current ?? 0}/${datasetProgress.total}（${datasetProgress.pct ?? 0}%） · ${datasetProgress.label ?? ''}`
                  : ''}
              </div>
            )}
            {data?.last_error && (
              <div className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-2 text-xs text-rose-300">
                最近错误：{data.last_error}
              </div>
            )}
            {data && !data.minute_capable && (
              <div className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-2 text-xs text-rose-300">
                当前 TickFlow 能力未包含分钟 K 线批量接口，模拟盯盘保持关闭；三年样本由独立历史分钟源能力决定。
              </div>
            )}
            {data?.historical_minute_error && (
              <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-300/5 px-3 py-2 text-xs text-amber-200">
                历史分钟源不可用：{data.historical_minute_error}
              </div>
            )}
            {data?.historical_minute_three_year_error && (
              <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-300/5 px-3 py-2 text-xs text-amber-200">
                三年样本暂不可启动：{data.historical_minute_three_year_error}
              </div>
            )}
            {data?.risk_trip_reason && (
              <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-300/5 px-3 py-2 text-xs text-amber-200">
                风险熔断已触发：{data.risk_trip_reason}。系统已禁止新买入并在盘后执行回滚。
              </div>
            )}
          </section>

          <section className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
            <MetricCard
              icon={CircleDollarSign}
              label="当前盈利"
              value={signedMoney(performance?.total_pnl)}
              valueClass={pnlClass(performance?.total_pnl)}
              hint={`累计收益 ${percent(performance?.total_return)} · 已实现 ${signedMoney(performance?.realized_pnl)}`}
            />
            <MetricCard
              icon={Activity}
              label="当前持仓"
              value={`${performance?.position_count ?? data?.positions.length ?? 0} 只`}
              hint={`${performance?.position_lot_count ?? data?.positions.length ?? 0} 个批次 · ${data?.pending_order_count ?? 0} 个待成交订单`}
            />
            <MetricCard
              icon={ReceiptText}
              label="成交单数"
              value={`${performance?.filled_order_count ?? 0} 单`}
              hint={`买入 ${performance?.buy_order_count ?? 0} · 卖出 ${performance?.sell_order_count ?? 0}`}
            />
            <MetricCard
              icon={Target}
              label="胜率"
              value={percent(performance?.win_rate)}
              hint={`${performance?.winning_trade_count ?? 0} 胜 / ${performance?.closed_trade_count ?? 0} 笔已平仓`}
            />
            <MetricCard
              icon={Scale}
              label="盈亏比"
              value={multiple(performance?.profit_loss_ratio)}
              hint={performance?.profit_loss_ratio == null ? '需同时有盈利和亏损交易' : '平均盈利 / 平均亏损'}
            />
            <MetricCard
              icon={TrendingUp}
              label="模拟权益"
              value={money(data?.equity)}
              hint={performance?.unpriced_position_count
                ? `${performance.unpriced_position_count} 个持仓批次等待最新价`
                : `现金 ${money(data?.cash)} · 浮盈 ${signedMoney(performance?.unrealized_pnl)}`}
            />
          </section>

          <section className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
            <Panel title="当前组合" subtitle={`${data?.candidate_count ?? 0} 个候选 · ${data?.market_symbol_count ?? 0} 个盯盘标的 · 数据时点 ${timestamp(performance?.valuation_as_of)}`}>
              {data?.positions.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="text-muted">
                      <tr className="border-b border-border">
                        <th className="px-2 py-2 font-medium">标的</th>
                        <th className="px-2 py-2 font-medium">买入日</th>
                        <th className="px-2 py-2 text-right font-medium">持仓股数</th>
                        <th className="px-2 py-2 text-right font-medium">成本 / 最新</th>
                        <th className="px-2 py-2 text-right font-medium">持仓市值</th>
                        <th className="px-2 py-2 text-right font-medium">浮动盈利</th>
                        <th className="px-2 py-2 text-right font-medium">收益率</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.positions.map(position => (
                        <tr key={position.lot_id} className="border-b border-border/60 last:border-0">
                          <td className="px-2 py-2.5">
                            <button
                              type="button"
                              onClick={() => setPreviewStock({
                                symbol: position.symbol,
                                name: positionNames[position.symbol] ?? position.symbol,
                              })}
                              className="group inline-flex min-w-0 items-center gap-1.5 rounded px-1 py-0.5 text-left transition-colors hover:bg-elevated"
                              title={`查看 ${positionNames[position.symbol] ?? position.symbol}（${position.symbol}）日 K`}
                            >
                              <span className="max-w-28 truncate font-medium text-foreground transition-colors group-hover:text-accent">
                                {positionNames[position.symbol] ?? position.symbol}
                              </span>
                              {positionNames[position.symbol] && (
                                <span className="font-mono text-[10px] text-muted">{position.symbol}</span>
                              )}
                              <ExternalLink className="h-3 w-3 shrink-0 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
                            </button>
                          </td>
                          <td className="px-2 py-2.5 text-secondary">
                            <div>{position.acquired_date}</div>
                            <div className="mt-0.5 whitespace-nowrap text-[10px] text-muted">次交易日起可卖</div>
                          </td>
                          <td className="px-2 py-2.5 text-right tabular-nums">{position.remaining_shares}</td>
                          <td className="px-2 py-2.5 text-right tabular-nums">
                            <div>{price(position.entry_price)}</div>
                            <div className="mt-0.5 text-[10px] text-muted">{price(position.market_price)}</div>
                          </td>
                          <td className="px-2 py-2.5 text-right tabular-nums">{money(position.market_value)}</td>
                          <td className={cn('px-2 py-2.5 text-right font-medium tabular-nums', pnlClass(position.unrealized_pnl))}>
                            {signedMoney(position.unrealized_pnl)}
                          </td>
                          <td className={cn('px-2 py-2.5 text-right font-medium tabular-nums', pnlClass(position.unrealized_pnl_pct))}>
                            {percent(position.unrealized_pnl_pct)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyCopy text="当前没有模拟持仓。Agent 在证据不足时会保持空仓。" />
              )}
            </Panel>

            <Panel title="模型保护集" subtitle="只有保护集优于基线才允许晋升">
              <div className="mb-3 grid grid-cols-3 gap-2">
                <SmallMetric label="当前策略" value={data?.champion ? `v${data.champion.version}` : '--'} />
                <SmallMetric label="训练模型" value={displayModel ? `v${displayModel.version}${activeModel ? '' : ' Shadow'}` : '规则基线'} />
                <SmallMetric label="训练数据" value={data?.dataset?.status ?? '未构建'} />
              </div>
              {displayModel && protectedMetrics ? (
                <div className="grid grid-cols-2 gap-3">
                  <SmallMetric label="训练区间" value={`${displayModel.trained_start} — ${displayModel.trained_end}`} wide />
                  <SmallMetric label="保护集样本" value={String(protectedMetrics.samples ?? '--')} />
                  <SmallMetric label="Brier 分数" value={String(protectedMetrics.brier ?? '--')} />
                  <SmallMetric label="入选后期望" value={percent(protectedMetrics.selected_mean_net_return)} />
                  <SmallMetric label="正样本率" value={percent(protectedMetrics.positive_rate)} />
                </div>
              ) : (
                <EmptyCopy text="尚无通过保护集门控的模型；系统会继续使用可审计的规则基线。" />
              )}
              {manifest && (
                <div className="mt-4 border-t border-border pt-3 text-[11px] leading-5 text-muted">
                  数据集：{String(manifest.candidate_dates ?? '--')} 个交易日，候选 {String(manifest.candidate_rows ?? '--')} 条，分钟 {String(manifest.minute_rows ?? '--')} 条；历史源 {String(manifest.minute_source ?? data?.historical_minute_source ?? '--')}，执行价格使用 raw OHLC。
                </div>
              )}
            </Panel>
          </section>

          <Panel
            title="历史成交单"
            subtitle={`最近 ${historicalTrades.length} 条真实成交 · 选择与退出原因来自当时保存的决策快照`}
          >
            {tradeHistory.isLoading ? (
              <div className="flex items-center justify-center gap-2 py-10 text-xs text-muted">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在读取历史成交…
              </div>
            ) : tradeHistory.isError ? (
              <div className="rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-4 text-xs text-rose-300">
                历史成交读取失败，请稍后刷新。
              </div>
            ) : historicalTrades.length ? (
              <div className="max-h-[520px] overflow-auto">
                <table className="min-w-[1060px] w-full text-left text-xs">
                  <thead className="sticky top-0 z-10 border-b border-border bg-surface text-muted">
                    <tr>
                      <th className="px-2 py-2.5 font-medium">成交时间</th>
                      <th className="px-2 py-2.5 font-medium">标的</th>
                      <th className="px-2 py-2.5 font-medium">方向</th>
                      <th className="px-2 py-2.5 text-right font-medium">数量 / 成交价</th>
                      <th className="px-2 py-2.5 text-right font-medium">费用</th>
                      <th className="px-2 py-2.5 text-right font-medium">已实现盈亏</th>
                      <th className="min-w-[360px] px-3 py-2.5 font-medium">为什么交易这只股票</th>
                    </tr>
                  </thead>
                  <tbody>
                    {historicalTrades.map(trade => {
                      const reason = tradeReason(trade)
                      const stockName = positionNames[trade.symbol] ?? trade.symbol
                      return (
                        <tr key={trade.id} className="border-b border-border/60 last:border-0">
                          <td className="whitespace-nowrap px-2 py-3 text-secondary">
                            <div>{timestamp(trade.occurred_at)}</div>
                            <div className="mt-0.5 text-[10px] text-muted">{trade.trade_date}</div>
                          </td>
                          <td className="px-2 py-3">
                            <button
                              type="button"
                              onClick={() => setPreviewStock({ symbol: trade.symbol, name: stockName })}
                              className="group inline-flex items-center gap-1.5 rounded px-1 py-0.5 text-left transition-colors hover:bg-elevated"
                              title={`查看 ${stockName}（${trade.symbol}）日 K`}
                            >
                              <span className="font-medium text-foreground group-hover:text-accent">{stockName}</span>
                              {stockName !== trade.symbol && (
                                <span className="font-mono text-[10px] text-muted">{trade.symbol}</span>
                              )}
                              <ExternalLink className="h-3 w-3 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
                            </button>
                          </td>
                          <td className="px-2 py-3">
                            <span className={cn(
                              'rounded-full px-2 py-1 text-[10px] font-medium',
                              trade.side === 'buy' ? 'bg-bull/10 text-bull' : 'bg-bear/10 text-bear',
                            )}>
                              {trade.side === 'buy' ? '买入' : '卖出'}
                            </span>
                            {trade.fill_status === 'order_partially_filled' && (
                              <div className="mt-1 text-[10px] text-amber-300">部分成交</div>
                            )}
                          </td>
                          <td className="whitespace-nowrap px-2 py-3 text-right tabular-nums">
                            <div>{trade.shares.toLocaleString('zh-CN')} 股</div>
                            <div className="mt-0.5 text-[10px] text-muted">@ {price(trade.price)}</div>
                          </td>
                          <td className="px-2 py-3 text-right tabular-nums text-secondary">{money(trade.fees)}</td>
                          <td className={cn('px-2 py-3 text-right font-medium tabular-nums', pnlClass(trade.realized_pnl))}>
                            {trade.realized_pnl == null ? '--' : signedMoney(trade.realized_pnl)}
                          </td>
                          <td className="px-3 py-3">
                            <div className="font-medium leading-5 text-foreground">{reason.summary}</div>
                            {reason.evidence && (
                              <div className="mt-1 text-[10px] leading-4 text-muted">{reason.evidence}</div>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyCopy text="暂无历史成交。Agent 产生真实模拟成交后会在这里保留订单与决策原因。" />
            )}
          </Panel>

          <section className="grid gap-4 xl:grid-cols-2">
            <Panel title="模拟盘会话" subtitle="每日操作与盘后结果均持久化">
              <div className="space-y-2">
                {(sessions.data?.sessions ?? []).slice(0, 8).map(session => (
                  <div key={session.id} className="flex items-center justify-between gap-3 rounded-lg border border-border/70 bg-base/40 px-3 py-2.5 text-xs">
                    <div>
                      <div className="font-medium text-foreground">{session.trade_date}</div>
                      <div className="mt-0.5 text-[10px] text-muted">{session.policy_id} · {session.candidates.length} 个候选</div>
                    </div>
                    <span className={cn('rounded-full px-2 py-0.5 text-[10px]', statusClass(session.status))}>{session.status}</span>
                  </div>
                ))}
                {!sessions.data?.sessions.length && <EmptyCopy text="暂无模拟盘会话。" />}
              </div>
            </Panel>

            <Panel title="进化实验" subtitle="单变量变异 · 失败不替换冠军策略">
              <div className="space-y-2">
                {(experiments.data?.experiments ?? []).slice(0, 8).map(experiment => (
                  <div key={experiment.id} className="rounded-lg border border-border/70 bg-base/40 px-3 py-2.5 text-xs">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-mono text-foreground">{experiment.candidate_policy_id}</span>
                      <span className={cn('rounded-full px-2 py-0.5 text-[10px]', statusClass(experiment.status))}>{experiment.status}</span>
                    </div>
                    <div className="mt-1 text-[10px] text-muted">{experiment.mutation_field} · {experiment.reason}</div>
                  </div>
                ))}
                {!experiments.data?.experiments.length && <EmptyCopy text="暂无进化实验；三年样本准备完成后可自动验证。" />}
              </div>
            </Panel>
          </section>

          <section className="rounded-xl border border-emerald-400/20 bg-emerald-400/[0.04] px-4 py-3 text-xs leading-5 text-secondary">
            <div className="flex items-start gap-2">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
              <p>
                防作弊边界：Point-in-Time 候选必须满足 source_date &lt; trade_date；分钟信号在 bar 结束后才可用；成交使用下一分钟 raw open；数据迟到、缺失、乱序时拒绝交易；策略与模型均保留不可变版本和晋升记录。
              </p>
            </div>
          </section>
        </div>
      </main>

      <StockPreviewDialog
        symbol={previewStock?.symbol ?? null}
        name={previewStock?.name}
        onClose={() => setPreviewStock(null)}
      />
    </>
  )
}

function ActionButton({
  label,
  icon: Icon,
  pending,
  disabled,
  primary,
  onClick,
}: {
  label: string
  icon: typeof Play
  pending?: boolean
  disabled?: boolean
  primary?: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || pending}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-btn border px-3 py-2 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45',
        primary
          ? 'border-blue-400/30 bg-blue-500 text-white hover:bg-blue-400'
          : 'border-border bg-base/60 text-secondary hover:bg-elevated hover:text-foreground',
      )}
    >
      {pending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Icon className="h-3.5 w-3.5" />}
      {label}
    </button>
  )
}

function MetricCard({
  icon: Icon,
  label,
  value,
  hint,
  valueClass,
}: {
  icon: typeof Activity
  label: string
  value: string
  hint: string
  valueClass?: string
}) {
  return (
    <div className="rounded-xl border border-border bg-surface/80 p-4">
      <div className="flex items-center gap-2 text-[11px] text-muted"><Icon className="h-3.5 w-3.5" />{label}</div>
      <div className={cn('mt-2 truncate text-lg font-semibold tabular-nums text-foreground', valueClass)}>{value}</div>
      <div className="mt-1 truncate text-[10px] text-muted" title={hint}>{hint}</div>
    </div>
  )
}

function Panel({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-border bg-surface/80 p-4">
      <div className="mb-3">
        <h3 className="text-sm font-semibold">{title}</h3>
        <p className="mt-0.5 text-[10px] text-muted">{subtitle}</p>
      </div>
      {children}
    </section>
  )
}

function SmallMetric({ label, value, wide }: { label: string; value: string; wide?: boolean }) {
  return (
    <div className={cn('rounded-lg bg-base/50 p-3', wide && 'col-span-2')}>
      <div className="text-[10px] text-muted">{label}</div>
      <div className="mt-1 text-xs font-medium tabular-nums text-foreground">{value}</div>
    </div>
  )
}

function EmptyCopy({ text }: { text: string }) {
  return <div className="rounded-lg border border-dashed border-border px-3 py-8 text-center text-xs text-muted">{text}</div>
}
