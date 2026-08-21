import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  Bot,
  BrainCircuit,
  ChevronDown,
  Database,
  FlaskConical,
  Loader2,
  Play,
  RefreshCw,
  ShieldCheck,
  Square,
  TrendingUp,
} from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
import {
  investmentExpertExperimentStatusLabel,
  investmentExpertStatusLabel,
  investmentExpertTaskLabel,
} from '@/lib/investmentExpertLabels'
import { QK } from '@/lib/queryKeys'

function money(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '--'
  return `¥${value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}

function percent(value: unknown): string {
  const number = Number(value)
  if (!Number.isFinite(number)) return '--'
  return `${(number * 100).toFixed(2)}%`
}

function statusClass(status: string | null | undefined): string {
  if (status === 'promoted' || status === 'succeeded') return 'text-emerald-400 bg-emerald-400/10'
  if (status === 'rejected' || status === 'failed') return 'text-rose-400 bg-rose-400/10'
  return 'text-amber-300 bg-amber-300/10'
}

function mutationLabel(field: string): string {
  const labels: Record<string, string> = {
    min_vwap_bias: '最低均价偏离',
    min_breakout_pct: '最低突破幅度',
    exit_vwap_bias: '离场均价偏离',
    target_position_pct: '目标仓位比例',
  }
  return labels[field] ?? field
}

function reasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    protected_evaluation_passed: '保护集评估通过，候选策略已晋升',
    anti_cheat_or_data_quality_violation: '存在防作弊或数据质量违规',
    no_protected_evaluation_data: '没有可用的保护集评估数据',
    insufficient_closed_trades: '有效平仓样本不足，无法完成晋升判定',
    expectancy_did_not_improve: '交易期望未优于原冠军策略',
    max_drawdown_regressed: '最大回撤明显退化',
    net_return_regressed: '净收益低于原冠军策略',
  }
  return labels[reason] ?? reason
}

const EXPERIMENT_METRICS = [
  ['total_return', '总收益率'],
  ['max_drawdown', '最大回撤'],
  ['closed_trades', '已平仓交易'],
  ['win_rate', '胜率'],
  ['expectancy', '单笔期望'],
  ['violations', '违规次数'],
  ['processed_dates', '评估交易日'],
] as const

function experimentMetric(value: number | string | null | undefined, key: string): string {
  if (value == null) return '--'
  const number = Number(value)
  if (!Number.isFinite(number)) return String(value)
  if (['total_return', 'max_drawdown', 'win_rate', 'expectancy'].includes(key)) {
    return `${(number * 100).toFixed(2)}%`
  }
  return number.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}

export function InvestmentExpert() {
  const queryClient = useQueryClient()
  const [previewSymbol, setPreviewSymbol] = useState<string | null>(null)
  const [expandedSessionId, setExpandedSessionId] = useState<string | null>(null)
  const [expandedExperimentId, setExpandedExperimentId] = useState<string | null>(null)
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
  const busy = Boolean(data?.active_task)
  const activeModel = data?.active_model
  const latestModel = data?.latest_model
  const displayModel = activeModel ?? latestModel
  const protectedMetrics = displayModel?.metrics.protected_test
  const manifest = data?.dataset?.manifest
  const overnightModules = Object.values(data?.overnight_us_market?.modules ?? {})
    .sort((left, right) => right.change_pct - left.change_pct)
  const strongestOvernightModule = overnightModules[0]
  const weakestOvernightModule = overnightModules[overnightModules.length - 1]

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
                <ActionButton label="构建三年样本" icon={Database} pending={bootstrap.isPending || data?.active_task === 'dataset_bootstrap'} disabled={busy || data?.minute_capable === false} onClick={() => bootstrap.mutate()} />
                <ActionButton label="重新训练" icon={BrainCircuit} pending={train.isPending || data?.active_task === 'model_training'} disabled={busy} onClick={() => train.mutate()} />
                <ActionButton label="发起进化" icon={FlaskConical} pending={evolve.isPending || data?.active_task === 'evolution'} disabled={busy} onClick={() => evolve.mutate()} />
              </div>
            </div>
            {data?.active_task && (
              <div className="mt-4 flex items-center gap-2 rounded-lg border border-amber-300/20 bg-amber-300/5 px-3 py-2 text-xs text-amber-200">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                后台任务进行中：{investmentExpertTaskLabel(data.active_task)}
              </div>
            )}
            {data?.last_error && (
              <div className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-2 text-xs text-rose-300">
                最近错误：{data.last_error}
              </div>
            )}
            {data && !data.minute_capable && (
              <div className="mt-3 rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-2 text-xs text-rose-300">
                当前 TickFlow 能力未包含分钟 K 线批量接口，模拟盯盘与三年分钟样本任务保持关闭。
              </div>
            )}
            {data?.risk_trip_reason && (
              <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-300/5 px-3 py-2 text-xs text-amber-200">
                风险熔断已触发：{data.risk_trip_reason}。系统已禁止新买入并在盘后执行回滚。
              </div>
            )}
            {data?.overnight_us_market && !data.overnight_us_market.available && (
              <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-300/5 px-3 py-2 text-xs text-amber-200">
                昨夜美股行业数据缺失或已过期，当日会话仍会正常创建；行业因子按中性处理，不影响原策略交易。
              </div>
            )}
            {data?.overnight_us_market?.available && overnightModules.length > 0 && (
              <div className="mt-3 rounded-lg border border-blue-400/20 bg-blue-400/5 px-3 py-2 text-xs text-blue-200">
                隔夜美股行业因子：{data.overnight_us_market.market_date} · 已读取 {overnightModules.length} 个行业/主题。
                候选按所属行业独立加减分，买入同向调整、卖出反向调整；
                {data.overnight_us_market.market_background_available === false
                  ? '大盘背景数据不完整，不参与评分。'
                  : `大盘综合 ${percent(data.overnight_us_market.score)} 仅作背景。`}
                {strongestOvernightModule && weakestOvernightModule && (
                  <> 最强：{strongestOvernightModule.name} {percent(strongestOvernightModule.change_pct)}；最弱：{weakestOvernightModule.name} {percent(weakestOvernightModule.change_pct)}。</>
                )}
              </div>
            )}
            {data?.news_sentiment?.available ? (
              <div className="mt-3 rounded-lg border border-violet-400/20 bg-violet-400/5 px-3 py-2 text-xs text-violet-200">
                消息面因子：综合情绪 {percent(data.news_sentiment.score)} · 置信度 {percent(data.news_sentiment.confidence)} ·
                新闻 {data.news_sentiment.item_count} 条（海外 {data.news_sentiment.regions.global}、国内 {data.news_sentiment.regions.domestic}、盘面 {data.news_sentiment.regions.market}）。
                当前策略最高权重 {percent(data.champion?.news_candidate_weight ?? 0.25)}，盘中每 10 分钟刷新。
              </div>
            ) : data?.news_sentiment ? (
              <div className="mt-3 rounded-lg border border-amber-300/20 bg-amber-300/5 px-3 py-2 text-xs text-amber-200">
                国内外及盘中新闻暂不可用，消息面因子按中性处理，不影响会话和原策略运行。
              </div>
            ) : null}
          </section>

          <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <MetricCard icon={TrendingUp} label="模拟权益" value={money(data?.equity)} hint={`现金 ${money(data?.cash)}`} />
            <MetricCard icon={Activity} label="持仓批次" value={String(data?.positions.length ?? 0)} hint={`${data?.pending_order_count ?? 0} 个待成交订单`} />
            <MetricCard icon={ShieldCheck} label="当前策略" value={data?.champion ? `v${data.champion.version}` : '--'} hint={data?.champion?.id ?? '尚未初始化'} />
            <MetricCard icon={BrainCircuit} label="训练模型" value={displayModel ? `v${displayModel.version}${activeModel ? '' : ' 影子观察'}` : '规则基线'} hint={displayModel ? `${displayModel.sample_count.toLocaleString()} 样本` : '等待保护集门控'} />
            <MetricCard icon={Database} label="训练数据" value={data?.dataset ? investmentExpertStatusLabel(data.dataset.status) : '未构建'} hint={data?.dataset ? `${data.dataset.start_date} 至 ${data.dataset.end_date}` : '默认拉取近三年'} />
          </section>

          <section className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
            <Panel title="当前组合" subtitle={`${data?.candidate_count ?? 0} 个候选 · ${data?.market_symbol_count ?? 0} 个盯盘标的`}>
              {data?.positions.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead className="text-muted">
                      <tr className="border-b border-border">
                        <th className="px-2 py-2 font-medium">标的</th>
                        <th className="px-2 py-2 font-medium">买入日</th>
                        <th className="px-2 py-2 text-right font-medium">剩余股数</th>
                        <th className="px-2 py-2 text-right font-medium">成本价</th>
                        <th className="px-2 py-2 text-right font-medium">T+1 状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.positions.map(position => (
                        <tr key={position.lot_id} className="border-b border-border/60 last:border-0">
                          <td className="px-2 py-2.5">
                            <button
                              type="button"
                              onClick={() => setPreviewSymbol(position.symbol)}
                              className="font-mono text-accent transition-colors hover:text-accent/80 hover:underline"
                              title={`查看 ${position.symbol} 股票详情`}
                            >
                              {position.symbol}
                            </button>
                          </td>
                          <td className="px-2 py-2.5 text-secondary">{position.acquired_date}</td>
                          <td className="px-2 py-2.5 text-right tabular-nums">{position.remaining_shares}</td>
                          <td className="px-2 py-2.5 text-right tabular-nums">{position.entry_price.toFixed(3)}</td>
                          <td className="px-2 py-2.5 text-right text-muted">次交易日起可卖</td>
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
                  数据集：{String(manifest.candidate_dates ?? '--')} 个交易日，候选 {String(manifest.candidate_rows ?? '--')} 条；执行价格使用 raw OHLC。
                </div>
              )}
            </Panel>
          </section>

          <section className="grid gap-4 xl:grid-cols-2">
            <Panel title="模拟盘会话" subtitle="点击会话查看候选，点击代码查询股票详情">
              <div className="space-y-2">
                {(sessions.data?.sessions ?? []).slice(0, 8).map(session => {
                  const expanded = expandedSessionId === session.id
                  return (
                    <div key={session.id} className="rounded-lg border border-border/70 bg-base/40 text-xs">
                      <button
                        type="button"
                        onClick={() => setExpandedSessionId(expanded ? null : session.id)}
                        className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left transition-colors hover:bg-elevated/60"
                        aria-expanded={expanded}
                      >
                        <div>
                          <div className="font-medium text-foreground">{session.trade_date}</div>
                          <div className="mt-0.5 text-[10px] text-muted">{session.policy_id} · {session.candidates.length} 个候选</div>
                        </div>
                        <div className="flex items-center gap-2">
                          <span className={cn('rounded-full px-2 py-0.5 text-[10px]', statusClass(session.status))}>{investmentExpertStatusLabel(session.status)}</span>
                          <ChevronDown className={cn('h-3.5 w-3.5 text-muted transition-transform', expanded && 'rotate-180')} />
                        </div>
                      </button>
                      {expanded && (
                        <div className="border-t border-border/70 px-3 py-3">
                          <div className="grid max-h-56 grid-cols-2 gap-1.5 overflow-y-auto pr-1 sm:grid-cols-3 lg:grid-cols-4">
                            {session.candidates.map((symbol, index) => (
                              <button
                                key={symbol}
                                type="button"
                                onClick={() => setPreviewSymbol(symbol)}
                                className="rounded-md border border-border/70 bg-surface/70 px-2 py-1.5 text-left font-mono text-[11px] text-accent transition-colors hover:border-accent/40 hover:bg-elevated"
                                title={`查询 ${symbol} 股票详情`}
                              >
                                <span className="mr-1 text-[9px] text-muted">{index + 1}</span>
                                {symbol}
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
                {!sessions.data?.sessions.length && <EmptyCopy text="暂无模拟盘会话。" />}
              </div>
            </Panel>

            <Panel title="进化实验" subtitle="单变量变异 · 未晋升不替换冠军策略">
              <div className="space-y-2">
                {(experiments.data?.experiments ?? []).slice(0, 8).map(experiment => {
                  const expanded = expandedExperimentId === experiment.id
                  return (
                    <div key={experiment.id} className="rounded-lg border border-border/70 bg-base/40 text-xs">
                      <button
                        type="button"
                        onClick={() => setExpandedExperimentId(expanded ? null : experiment.id)}
                        className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left transition-colors hover:bg-elevated/60"
                        aria-expanded={expanded}
                      >
                        <div className="min-w-0">
                          <div className="truncate font-mono text-foreground">{experiment.candidate_policy_id}</div>
                          <div className="mt-1 text-[10px] text-muted">变异项：{mutationLabel(experiment.mutation_field)}</div>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          <span className={cn('rounded-full px-2 py-0.5 text-[10px]', statusClass(experiment.status))}>{investmentExpertExperimentStatusLabel(experiment.status)}</span>
                          <ChevronDown className={cn('h-3.5 w-3.5 text-muted transition-transform', expanded && 'rotate-180')} />
                        </div>
                      </button>
                      {expanded && (
                        <div className="space-y-3 border-t border-border/70 px-3 py-3">
                          <div className="rounded-md bg-base/50 px-2.5 py-2 text-[11px] leading-5 text-secondary">
                            <div>结论：{reasonLabel(experiment.reason)}</div>
                            <div className="text-muted">原冠军：{experiment.champion_policy_id}</div>
                          </div>
                          <div className="overflow-x-auto">
                            <table className="w-full min-w-[420px] text-left text-[11px]">
                              <thead className="text-muted">
                                <tr className="border-b border-border/70">
                                  <th className="px-2 py-1.5 font-medium">评估指标</th>
                                  <th className="px-2 py-1.5 text-right font-medium">原冠军策略</th>
                                  <th className="px-2 py-1.5 text-right font-medium">候选策略</th>
                                </tr>
                              </thead>
                              <tbody>
                                {EXPERIMENT_METRICS.map(([key, label]) => (
                                  <tr key={key} className="border-b border-border/40 last:border-0">
                                    <td className="px-2 py-1.5 text-secondary">{label}</td>
                                    <td className="px-2 py-1.5 text-right tabular-nums text-muted">{experimentMetric(experiment.champion_metrics?.[key], key)}</td>
                                    <td className="px-2 py-1.5 text-right tabular-nums text-foreground">{experimentMetric(experiment.candidate_metrics?.[key], key)}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
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
        symbol={previewSymbol}
        triggerInfo={null}
        onClose={() => setPreviewSymbol(null)}
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

function MetricCard({ icon: Icon, label, value, hint }: { icon: typeof Activity; label: string; value: string; hint: string }) {
  return (
    <div className="rounded-xl border border-border bg-surface/80 p-4">
      <div className="flex items-center gap-2 text-[11px] text-muted"><Icon className="h-3.5 w-3.5" />{label}</div>
      <div className="mt-2 truncate text-lg font-semibold tabular-nums text-foreground">{value}</div>
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
