import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  Bot,
  BrainCircuit,
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
import { api } from '@/lib/api'
import { cn } from '@/lib/cn'
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

export function InvestmentExpert() {
  const queryClient = useQueryClient()
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
                后台任务进行中：{data.active_task}
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
          </section>

          <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <MetricCard icon={TrendingUp} label="模拟权益" value={money(data?.equity)} hint={`现金 ${money(data?.cash)}`} />
            <MetricCard icon={Activity} label="持仓批次" value={String(data?.positions.length ?? 0)} hint={`${data?.pending_order_count ?? 0} 个待成交订单`} />
            <MetricCard icon={ShieldCheck} label="当前策略" value={data?.champion ? `v${data.champion.version}` : '--'} hint={data?.champion?.id ?? '尚未初始化'} />
            <MetricCard icon={BrainCircuit} label="训练模型" value={displayModel ? `v${displayModel.version}${activeModel ? '' : ' Shadow'}` : '规则基线'} hint={displayModel ? `${displayModel.sample_count.toLocaleString()} 样本` : '等待保护集门控'} />
            <MetricCard icon={Database} label="训练数据" value={data?.dataset?.status ?? '未构建'} hint={data?.dataset ? `${data.dataset.start_date} 至 ${data.dataset.end_date}` : '默认拉取近三年'} />
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
                          <td className="px-2 py-2.5 font-mono text-foreground">{position.symbol}</td>
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
