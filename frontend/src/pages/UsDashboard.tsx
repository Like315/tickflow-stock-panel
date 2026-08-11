import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import { Loader2, RefreshCw, TrendingUp, TrendingDown, Activity, DollarSign, Flame, BarChart3, Globe } from 'lucide-react'
import { useState } from 'react'
import { api, type UsMarketOverview, type UsStockRow } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { cn } from '@/lib/cn'
import { PageHeader } from '@/components/PageHeader'

// ── 美股格式化工具 (与 A 股相反: 绿涨红跌) ─────────────────────

function fmtUsPct(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(digits)}%`
}

function fmtUsPrice(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—'
  return v.toFixed(digits)
}

function fmtUsVolume(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}亿`
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}百万`
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}千`
  return v.toFixed(0)
}

function fmtUsMktCap(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—'
  if (v >= 1e12) return `${(v / 1e12).toFixed(2)}万亿美元`
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}亿美元`
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}百万美元`
  return `${v.toFixed(0)}美元`
}

// 美股配色: 涨=绿(bear 色), 跌=红(bull 色) — 与 A 股相反
function usPriceColor(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || v === 0) return 'text-muted'
  return v > 0 ? 'text-bear' : 'text-bull'
}

function usBgColor(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v) || v === 0) return ''
  return v > 0 ? 'bg-bear/10' : 'bg-bull/10'
}

// ── 子组件 ──────────────────────────────────────────────────

function IndexTicker({ name, price, change, changePct }: {
  name: string
  price: number | null
  change: number | null
  changePct: number | null
}) {
  const color = usPriceColor(changePct)
  return (
    <div className="flex items-center gap-3 rounded-card border border-border bg-surface px-4 py-3">
      <div className="min-w-0">
        <div className="text-xs text-muted truncate">{name}</div>
        <div className="num text-lg font-semibold text-foreground">{fmtUsPrice(price)}</div>
      </div>
      <div className={cn('num ml-auto text-right text-sm font-medium', color)}>
        {changePct != null && (changePct > 0 ? '▲' : '▼')} {fmtUsPct(changePct)}
        {change != null && <div className="text-xs">{change > 0 ? '+' : ''}{fmtUsPrice(change)}</div>}
      </div>
    </div>
  )
}

function BreadthBar({ breadth }: { breadth: UsMarketOverview['breadth'] }) {
  const { total, up, down, flat } = breadth
  const upPct = total > 0 ? (up / total) * 100 : 0
  const downPct = total > 0 ? (down / total) * 100 : 0
  const flatPct = total > 0 ? (flat / total) * 100 : 0

  return (
    <div className="rounded-card border border-border bg-surface p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-sm font-medium text-secondary">市场广度</span>
        <span className="text-xs text-muted">{total} 只</span>
      </div>
      {/* 堆叠条 */}
      <div className="flex h-6 overflow-hidden rounded-btn">
        <div className="bg-bear/70 flex items-center justify-center text-[10px] font-medium text-white" style={{ width: `${upPct}%` }}>
          {upPct > 8 ? `${up}` : ''}
        </div>
        <div className="bg-muted/30 flex items-center justify-center text-[10px] text-muted" style={{ width: `${flatPct}%` }}>
          {flatPct > 8 ? `${flat}` : ''}
        </div>
        <div className="bg-bull/70 flex items-center justify-center text-[10px] font-medium text-white" style={{ width: `${downPct}%` }}>
          {downPct > 8 ? `${down}` : ''}
        </div>
      </div>
      <div className="mt-2 flex items-center gap-4 text-xs">
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-bear/70" /> 上涨 {up}
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-bull/70" /> 下跌 {down}
        </span>
        <span className="flex items-center gap-1 text-muted">
          <span className="h-2 w-2 rounded-full bg-muted/30" /> 平盘 {flat}
        </span>
        <span className="ml-auto num text-muted">均值 {fmtUsPct(breadth.avg_pct)}</span>
      </div>
    </div>
  )
}

function SectorHeatmap({ sectors }: { sectors: UsMarketOverview['sectors'] }) {
  if (!sectors.length) return null
  return (
    <div className="rounded-card border border-border bg-surface p-4">
      <div className="mb-3 flex items-center gap-2">
        <BarChart3 className="h-4 w-4 text-accent" />
        <span className="text-sm font-medium text-secondary">板块表现</span>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {sectors.map((s) => (
          <div
            key={s.name}
            className={cn('rounded-btn border border-border p-2.5 transition-colors', usBgColor(s.avg_pct))}
          >
            <div className="truncate text-xs font-medium text-secondary">{s.name}</div>
            <div className={cn('num text-base font-semibold', usPriceColor(s.avg_pct))}>
              {fmtUsPct(s.avg_pct)}
            </div>
            <div className="mt-0.5 flex items-center gap-1 text-[10px] text-muted">
              <span className="text-bear/80">{s.up_count}↑</span>
              <span className="text-bull/80">{s.down_count}↓</span>
            </div>
            {s.leader && (
              <div className="mt-0.5 truncate text-[10px] text-muted">
                {s.leader.name}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function StockList({ title, icon: Icon, rows, metric }: {
  title: string
  icon: typeof TrendingUp
  rows: UsStockRow[]
  metric: 'change_pct' | 'volume' | 'market_cap'
}) {
  return (
    <div className="rounded-card border border-border bg-surface p-4">
      <div className="mb-2 flex items-center gap-2">
        <Icon className="h-4 w-4 text-accent" />
        <span className="text-sm font-medium text-secondary">{title}</span>
      </div>
      <div className="space-y-0.5">
        {rows.map((s, i) => (
          <div key={s.symbol} className="flex items-center gap-2 rounded-btn px-2 py-1.5 text-sm hover:bg-elevated/50">
            <span className="w-4 text-xs text-muted num">{i + 1}</span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium text-foreground">{s.name}</div>
              <div className="text-[10px] text-muted">{s.symbol}</div>
            </div>
            <div className="num text-right text-xs text-secondary">
              {metric === 'change_pct' && (
                <span className={cn('font-medium', usPriceColor(s.change_pct))}>
                  {fmtUsPct(s.change_pct)}
                </span>
              )}
              {metric === 'volume' && <span>{fmtUsVolume(s.volume)}</span>}
              {metric === 'market_cap' && <span>{fmtUsMktCap(s.market_cap)}</span>}
            </div>
            <div className="num w-16 text-right text-xs text-muted">
              {fmtUsPrice(s.price)}
            </div>
          </div>
        ))}
        {!rows.length && <div className="py-4 text-center text-xs text-muted">暂无数据</div>}
      </div>
    </div>
  )
}

function DistributionChart({ distribution }: { distribution: UsMarketOverview['distribution'] }) {
  if (!distribution.length) return null
  const maxCount = Math.max(...distribution.map((d) => d.count), 1)
  return (
    <div className="rounded-card border border-border bg-surface p-4">
      <div className="mb-3 flex items-center gap-2">
        <Activity className="h-4 w-4 text-accent" />
        <span className="text-sm font-medium text-secondary">涨跌分布</span>
      </div>
      <div className="flex items-end gap-1.5" style={{ height: 120 }}>
        {distribution.map((d) => {
          const h = (d.count / maxCount) * 100
          const color = d.label.startsWith('-') || d.label.startsWith('<')
            ? 'bg-bull/60'
            : d.label.includes('0~1') || d.label.includes('0~')
            ? 'bg-muted/40'
            : 'bg-bear/60'
          return (
            <div key={d.label} className="flex flex-1 flex-col items-center justify-end gap-1">
              <span className="num text-[10px] text-muted">{d.count}</span>
              <div
                className={cn('w-full rounded-t-sm transition-all', color)}
                style={{ height: `${h}%`, minHeight: d.count > 0 ? 4 : 0 }}
              />
              <span className="text-[9px] text-muted whitespace-nowrap">{d.label}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── 主页面 ──────────────────────────────────────────────────

export function UsDashboard() {
  const [lastRefresh, setLastRefresh] = useState<string>('')

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: QK.usMarketOverview,
    queryFn: api.usMarketOverview,
    staleTime: 15_000,
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  })

  const handleRefresh = () => {
    refetch()
    setLastRefresh(new Date().toLocaleTimeString())
  }

  if (isLoading && !data) {
    return (
      <div className="flex h-full items-center justify-center bg-base">
        <div className="flex items-center gap-2 text-sm text-muted">
          <Loader2 className="h-4 w-4 animate-spin" /> 加载美股看板…
        </div>
      </div>
    )
  }

  if (isError && !data) {
    return (
      <div className="flex h-full items-center justify-center bg-base p-6">
        <div className="rounded-card border border-border bg-surface p-6 text-center">
          <div className="text-sm text-danger">美股看板加载失败</div>
          <div className="mt-1 text-xs text-muted">可能是 Yahoo Finance API 被限流,请稍后重试</div>
          <button
            onClick={() => refetch()}
            className="mt-3 rounded-btn bg-accent px-4 py-1.5 text-sm text-white hover:bg-accent/90"
          >
            重试
          </button>
        </div>
      </div>
    )
  }

  const d = data!
  const marketStatus = d.market_open ? '交易中' : '已收盘'
  const marketColor = d.market_open ? 'text-bear' : 'text-muted'

  return (
    <div className="min-h-full bg-base">
      <PageHeader
        title="美股看板"
        subtitle="美股市场总览"
        titleExtra={
          <div className="flex items-center gap-2">
            <span className={cn('flex items-center gap-1 rounded-full border border-border px-2.5 py-0.5 text-xs', marketColor)}>
              <span className={cn('h-1.5 w-1.5 rounded-full', d.market_open ? 'bg-bear animate-pulse' : 'bg-muted')} />
              {marketStatus}
            </span>
            {d.as_of && <span className="text-xs text-muted">{d.as_of}</span>}
          </div>
        }
        right={
          <button
            onClick={handleRefresh}
            disabled={isFetching}
            className="flex items-center gap-1.5 rounded-btn border border-border bg-surface px-3 py-1.5 text-xs text-secondary transition-colors hover:bg-elevated disabled:opacity-50"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', isFetching && 'animate-spin')} />
            刷新
          </button>
        }
      />

      <div className="space-y-3 p-4">
        {/* 指数行情条 */}
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5"
        >
          {d.indices.map((idx) => (
            <IndexTicker
              key={idx.symbol}
              name={idx.name}
              price={idx.price}
              change={idx.change}
              changePct={idx.change_pct}
            />
          ))}
        </motion.div>

        {/* KPI 行 */}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <div className="rounded-card border border-border bg-surface px-4 py-3">
            <div className="text-xs text-muted">上涨家数</div>
            <div className="num text-xl font-semibold text-bear">{d.breadth.up}</div>
            <div className="text-[10px] text-muted">{fmtUsPct(d.breadth.up_pct, 1)} 占比</div>
          </div>
          <div className="rounded-card border border-border bg-surface px-4 py-3">
            <div className="text-xs text-muted">下跌家数</div>
            <div className="num text-xl font-semibold text-bull">{d.breadth.down}</div>
            <div className="text-[10px] text-muted">{fmtUsPct(d.breadth.down_pct, 1)} 占比</div>
          </div>
          <div className="rounded-card border border-border bg-surface px-4 py-3">
            <div className="text-xs text-muted">平均涨跌</div>
            <div className={cn('num text-xl font-semibold', usPriceColor(d.breadth.avg_pct))}>
              {fmtUsPct(d.breadth.avg_pct)}
            </div>
            <div className="text-[10px] text-muted">样本 {d.breadth.total} 只</div>
          </div>
          <div className="rounded-card border border-border bg-surface px-4 py-3">
            <div className="text-xs text-muted">市场状态</div>
            <div className={cn('text-xl font-semibold', marketColor)}>
              {d.market_open ? '🟢 开盘中' : '🔴 已收盘'}
            </div>
            <div className="text-[10px] text-muted">美东 09:30 - 16:00</div>
          </div>
        </div>

        {/* 板块热力图 + 广度 */}
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <div className="space-y-3">
            <SectorHeatmap sectors={d.sectors} />
            <DistributionChart distribution={d.distribution} />
          </div>
          <BreadthBar breadth={d.breadth} />
        </div>

        {/* 排行榜三列 */}
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <StockList
            title="涨幅榜"
            icon={TrendingUp}
            rows={d.top_gainers}
            metric="change_pct"
          />
          <StockList
            title="跌幅榜"
            icon={TrendingDown}
            rows={d.top_losers}
            metric="change_pct"
          />
          <StockList
            title="成交额榜"
            icon={Flame}
            rows={d.most_active}
            metric="volume"
          />
        </div>

        {/* 市值龙头 */}
        <StockList
          title="市值龙头"
          icon={DollarSign}
          rows={d.market_cap_leaders}
          metric="market_cap"
        />

        {/* 底部说明 */}
        <div className="flex items-center justify-center gap-2 py-2 text-[10px] text-muted">
          <Globe className="h-3 w-3" />
          数据源：Yahoo Finance · 仅供参考，不构成投资建议 · 美股配色：绿涨红跌
          {lastRefresh && <span>· 最后刷新 {lastRefresh}</span>}
        </div>
      </div>
    </div>
  )
}
