import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  Clock3,
  Globe2,
  Loader2,
  RefreshCw,
  TrendingUp,
} from 'lucide-react'
import { api, type UsMarketDataStatus, type UsMarketQuote } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

function validNumber(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function formatPrice(value: number | null | undefined): string {
  const number = validNumber(value)
  if (number == null) return '—'
  const digits = number >= 100 ? 2 : number >= 1 ? 2 : 4
  return number.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function formatPct(value: number | null | undefined): string {
  const number = validNumber(value)
  if (number == null) return '—'
  return `${number >= 0 ? '+' : ''}${(number * 100).toFixed(2)}%`
}

function formatCompact(value: number | null | undefined): string {
  const number = validNumber(value)
  if (number == null) return '—'
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 2 }).format(number)
}

function changeClass(value: number | null | undefined): string {
  const number = validNumber(value)
  if (number == null || number === 0) return 'text-muted'
  return number > 0 ? 'text-emerald-400' : 'text-rose-400'
}

const STATUS_META: Record<UsMarketDataStatus, { label: string; className: string }> = {
  live: { label: '实时聚合', className: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-400' },
  snapshot: { label: '历史快照', className: 'border-amber-400/30 bg-amber-400/10 text-amber-400' },
  partial: { label: 'ETF 代理', className: 'border-sky-400/30 bg-sky-400/10 text-sky-400' },
}

const SESSION_LABELS: Record<string, string> = {
  pre_market: '盘前',
  regular: '盘中',
  after_hours: '盘后',
  closed: '已收盘',
  halted: '停牌',
  unknown: '未知时段',
}

function SectionTitle({ icon: Icon, title, hint }: { icon: typeof Activity; title: string; hint?: string }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <div className="flex items-center gap-2">
        <span className="h-4 w-0.5 rounded-full bg-gradient-to-b from-sky-400 to-violet-500" />
        <Icon className="h-4 w-4 text-sky-400" />
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
      </div>
      {hint && <span className="font-mono text-[10px] text-muted">{hint}</span>}
    </div>
  )
}

function BenchmarkCard({ quote }: { quote: UsMarketQuote }) {
  const positive = (quote.change_pct ?? 0) > 0
  return (
    <div className="rounded-card border border-border bg-surface/85 p-4 shadow-sm transition-colors hover:border-sky-400/30">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-mono text-xs font-semibold text-sky-300">{quote.symbol.replace('.US', '')}</div>
          <div className="mt-1 text-[11px] text-muted">{quote.name}</div>
        </div>
        <span className={`rounded-full p-1.5 ${positive ? 'bg-emerald-400/10' : 'bg-rose-400/10'}`}>
          {positive
            ? <ArrowUpRight className="h-3.5 w-3.5 text-emerald-400" />
            : <ArrowDownRight className="h-3.5 w-3.5 text-rose-400" />}
        </span>
      </div>
      <div className="mt-4 flex items-end justify-between gap-2">
        <span className="font-mono text-xl font-semibold text-foreground">{formatPrice(quote.last_price)}</span>
        <span className={`font-mono text-sm font-semibold ${changeClass(quote.change_pct)}`}>
          {formatPct(quote.change_pct)}
        </span>
      </div>
    </div>
  )
}

function RankingTable({ title, rows, mode }: { title: string; rows: UsMarketQuote[]; mode: 'change' | 'amount' }) {
  return (
    <section className="min-w-0 rounded-card border border-border bg-surface/85 p-3.5">
      <div className="mb-2.5 flex items-center justify-between">
        <h3 className="text-xs font-semibold text-foreground">{title}</h3>
        <span className="text-[10px] text-muted">TOP {rows.length}</span>
      </div>
      {rows.length === 0 ? (
        <div className="rounded-md border border-dashed border-border py-8 text-center text-xs text-muted">
          当前数据模式不提供排行榜
        </div>
      ) : (
        <div className="space-y-0.5">
          {rows.map((row, index) => (
            <div
              key={row.symbol}
              className="grid grid-cols-[20px_minmax(0,1fr)_80px_82px] items-center gap-2 rounded-md px-1.5 py-1.5 text-[11px] hover:bg-elevated/60"
            >
              <span className="font-mono text-[10px] text-muted">{index + 1}</span>
              <div className="min-w-0">
                <div className="truncate font-medium text-foreground">{row.symbol.replace('.US', '')}</div>
                <div className="truncate text-[9px] text-muted">{row.name}</div>
              </div>
              <span className="text-right font-mono text-secondary">{formatPrice(row.last_price)}</span>
              <span className={`text-right font-mono font-semibold ${mode === 'change' ? changeClass(row.change_pct) : 'text-sky-300'}`}>
                {mode === 'change'
                  ? formatPct(row.change_pct)
                  : `${row.amount_estimated ? '≈' : ''}${formatCompact(row.amount)}`}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

export function UsMarketDashboard() {
  const queryClient = useQueryClient()
  const overview = useQuery({
    queryKey: QK.usMarketOverview,
    queryFn: api.usMarketOverview,
    staleTime: 15_000,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  })
  const refresh = useMutation({
    mutationFn: api.usMarketRefresh,
    onSuccess: data => queryClient.setQueryData(QK.usMarketOverview, data),
  })

  if (overview.isLoading) {
    return (
      <div className="flex min-h-[70vh] items-center justify-center">
        <div className="flex items-center gap-2 text-sm text-muted">
          <Loader2 className="h-4 w-4 animate-spin text-sky-400" />
          正在聚合美股市场数据…
        </div>
      </div>
    )
  }

  if (overview.isError || !overview.data) {
    return (
      <div className="mx-auto flex min-h-[70vh] max-w-lg items-center px-6">
        <div className="w-full rounded-card border border-rose-400/25 bg-surface p-8 text-center">
          <Globe2 className="mx-auto h-8 w-8 text-rose-400" />
          <h1 className="mt-3 text-base font-semibold text-foreground">美股行情暂时不可用</h1>
          <p className="mt-2 text-xs leading-5 text-muted">
            {overview.error instanceof Error ? overview.error.message : '请稍后重试'}
          </p>
          <button
            onClick={() => overview.refetch()}
            className="mt-5 inline-flex items-center gap-1.5 rounded-btn border border-border bg-elevated px-3 py-1.5 text-xs text-foreground hover:border-sky-400/40"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            重试
          </button>
        </div>
      </div>
    )
  }

  const data = overview.data
  const status = STATUS_META[data.status]
  const breadth = data.breadth
  const maxDistribution = Math.max(...data.distribution.map(item => item.count), 1)

  return (
    <div className="mx-auto max-w-[1600px] space-y-4 p-4 md:p-5">
      <header className="overflow-hidden rounded-card border border-border bg-gradient-to-r from-surface via-surface/95 to-sky-950/20 p-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <span className="rounded-xl border border-sky-400/20 bg-sky-400/10 p-2.5">
              <Globe2 className="h-5 w-5 text-sky-400" />
            </span>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-lg font-semibold tracking-tight text-foreground">美股市场看板</h1>
                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${status.className}`}>
                  {status.label}
                </span>
                <span className="rounded-full border border-border bg-elevated/60 px-2 py-0.5 text-[10px] text-secondary">
                  {SESSION_LABELS[data.session] ?? data.session}
                </span>
              </div>
              <p className="mt-1.5 text-xs text-secondary">{data.message}</p>
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[10px] text-muted">
                <span className="inline-flex items-center gap-1"><Clock3 className="h-3 w-3" />纽约 {data.market_time ? new Date(data.market_time).toLocaleString('zh-CN', { timeZone: 'America/New_York', hour12: false }) : '—'}</span>
                <span>北京 {data.beijing_time ? new Date(data.beijing_time).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—'}</span>
                <span>数据源 {data.source}</span>
              </div>
            </div>
          </div>
          <button
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
            className="inline-flex items-center gap-1.5 rounded-btn border border-border bg-elevated/70 px-3 py-1.5 text-xs text-secondary transition-colors hover:border-sky-400/40 hover:text-foreground disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refresh.isPending ? 'animate-spin' : ''}`} />
            {refresh.isPending ? '刷新中' : '立即刷新'}
          </button>
        </div>
      </header>

      <section>
        <SectionTitle icon={TrendingUp} title="市场基准" hint="ETF 代理" />
        {data.benchmarks.length > 0 ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {data.benchmarks.map(quote => <BenchmarkCard key={quote.symbol} quote={quote} />)}
          </div>
        ) : (
          <div className="rounded-card border border-dashed border-border py-8 text-center text-xs text-muted">暂无基准行情</div>
        )}
      </section>

      <div className="grid gap-4 xl:grid-cols-[1.05fr_0.95fr]">
        <section className="rounded-card border border-border bg-surface/85 p-4">
          <SectionTitle icon={Activity} title="市场宽度" hint={breadth ? `${breadth.total.toLocaleString()} 只有效样本` : '实时权限下可用'} />
          {breadth ? (
            <>
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-lg bg-emerald-400/8 p-3">
                  <div className="text-[10px] text-muted">上涨</div>
                  <div className="mt-1 font-mono text-xl font-semibold text-emerald-400">{breadth.up.toLocaleString()}</div>
                  <div className="mt-0.5 font-mono text-[10px] text-emerald-400/70">{(breadth.up_ratio * 100).toFixed(1)}%</div>
                </div>
                <div className="rounded-lg bg-elevated/60 p-3">
                  <div className="text-[10px] text-muted">平盘</div>
                  <div className="mt-1 font-mono text-xl font-semibold text-secondary">{breadth.flat.toLocaleString()}</div>
                  <div className="mt-0.5 font-mono text-[10px] text-muted">|涨跌| &lt; 0.005%</div>
                </div>
                <div className="rounded-lg bg-rose-400/8 p-3">
                  <div className="text-[10px] text-muted">下跌</div>
                  <div className="mt-1 font-mono text-xl font-semibold text-rose-400">{breadth.down.toLocaleString()}</div>
                  <div className="mt-0.5 font-mono text-[10px] text-rose-400/70">{(breadth.down_ratio * 100).toFixed(1)}%</div>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
                <div className="flex items-center justify-between rounded-md border border-emerald-400/15 px-3 py-2 text-secondary">
                  <span>强势上涨 ≥ 2%</span><span className="font-mono text-emerald-400">{breadth.strong}</span>
                </div>
                <div className="flex items-center justify-between rounded-md border border-rose-400/15 px-3 py-2 text-secondary">
                  <span>弱势下跌 ≤ -2%</span><span className="font-mono text-rose-400">{breadth.weak}</span>
                </div>
              </div>
            </>
          ) : (
            <div className="rounded-lg border border-dashed border-sky-400/20 bg-sky-400/5 px-4 py-10 text-center text-xs leading-5 text-muted">
              当前为 ETF 代理模式，TickFlow 全市场实时权限可用后将自动展示涨跌家数与分布。
            </div>
          )}
        </section>

        <section className="rounded-card border border-border bg-surface/85 p-4">
          <SectionTitle icon={BarChart3} title="涨跌分布" />
          {data.distribution.length > 0 ? (
            <div className="space-y-3 pt-1">
              {data.distribution.map((item, index) => (
                <div key={item.label} className="grid grid-cols-[76px_minmax(0,1fr)_58px] items-center gap-2 text-[10px]">
                  <span className="font-mono text-muted">{item.label}</span>
                  <div className="h-2 overflow-hidden rounded-full bg-elevated">
                    <div
                      className={`h-full rounded-full ${index < 3 ? 'bg-rose-400/75' : 'bg-emerald-400/75'}`}
                      style={{ width: `${Math.max(2, item.count / maxDistribution * 100)}%` }}
                    />
                  </div>
                  <span className="text-right font-mono text-secondary">{item.count.toLocaleString()}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-10 text-center text-xs text-muted">当前数据模式不提供全市场涨跌分布</div>
          )}
        </section>
      </div>

      <section className="rounded-card border border-border bg-surface/85 p-4">
        <SectionTitle icon={Globe2} title="行业表现" hint="SPDR 行业 ETF 代理" />
        {data.sectors.length > 0 ? (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {data.sectors.map((sector, index) => (
              <div key={sector.symbol} className="flex items-center gap-3 rounded-lg border border-border/60 bg-elevated/35 px-3 py-2.5">
                <span className="w-5 font-mono text-[10px] text-muted">{String(index + 1).padStart(2, '0')}</span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs font-medium text-foreground">{sector.name}</div>
                  <div className="mt-0.5 font-mono text-[9px] text-muted">{sector.symbol.replace('.US', '')}</div>
                </div>
                <span className={`font-mono text-xs font-semibold ${changeClass(sector.change_pct)}`}>{formatPct(sector.change_pct)}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="py-8 text-center text-xs text-muted">暂无行业 ETF 行情</div>
        )}
      </section>

      <div className="grid gap-4 xl:grid-cols-3">
        <RankingTable title="涨幅榜" rows={data.rankings.gainers} mode="change" />
        <RankingTable title="跌幅榜" rows={data.rankings.losers} mode="change" />
        <RankingTable title="成交活跃" rows={data.rankings.active} mode="amount" />
      </div>

      <footer className="pb-2 text-center text-[10px] leading-5 text-muted">
        SPY、QQQ、DIA、IWM 及行业基金用于代理主要指数和板块表现，不代表指数本身。行情仅供信息展示。
      </footer>
    </div>
  )
}
