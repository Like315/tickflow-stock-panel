import { useDeferredValue, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ChevronLeft,
  ChevronRight,
  Database,
  ExternalLink,
  LineChart,
  Loader2,
  Search,
  X,
} from 'lucide-react'

import {
  api,
  type UsMarketDailyRow,
  type UsMarketGroupSummary,
} from '../../lib/api'
import { QK } from '../../lib/queryKeys'

const PAGE_SIZE = 30

function formatNumber(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 2 }).format(value)
}

function formatPrice(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return value >= 100 ? value.toFixed(2) : value.toFixed(3)
}

function formatPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return `${value > 0 ? '+' : ''}${(value * 100).toFixed(2)}%`
}

function changeClass(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return 'text-muted'
  if (value > 0) return 'text-emerald-400'
  if (value < 0) return 'text-rose-400'
  return 'text-secondary'
}

function HistoryChart({ rows }: { rows: UsMarketDailyRow[] }) {
  const points = rows.filter(row => Number.isFinite(row.close))
  if (points.length < 2) {
    return <div className="flex h-44 items-center justify-center text-xs text-muted">历史行情样本不足</div>
  }
  const closes = points.map(row => row.close)
  const low = Math.min(...closes)
  const high = Math.max(...closes)
  const span = Math.max(high - low, Math.abs(high) * 0.01, 1e-6)
  const polyline = closes.map((close, index) => {
    const x = (index / (closes.length - 1)) * 1000
    const y = 190 - ((close - low) / span) * 160
    return `${x.toFixed(2)},${y.toFixed(2)}`
  }).join(' ')
  const positive = closes[closes.length - 1] >= closes[0]

  return (
    <div>
      <svg viewBox="0 0 1000 220" className="h-44 w-full" preserveAspectRatio="none" role="img" aria-label="历史收盘价走势">
        <defs>
          <linearGradient id="us-history-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={positive ? '#34d399' : '#fb7185'} stopOpacity="0.28" />
            <stop offset="100%" stopColor={positive ? '#34d399' : '#fb7185'} stopOpacity="0" />
          </linearGradient>
        </defs>
        <line x1="0" x2="1000" y1="30" y2="30" stroke="currentColor" className="text-border" strokeDasharray="4 8" />
        <line x1="0" x2="1000" y1="110" y2="110" stroke="currentColor" className="text-border" strokeDasharray="4 8" />
        <line x1="0" x2="1000" y1="190" y2="190" stroke="currentColor" className="text-border" strokeDasharray="4 8" />
        <polygon points={`0,190 ${polyline} 1000,190`} fill="url(#us-history-fill)" />
        <polyline points={polyline} fill="none" stroke={positive ? '#34d399' : '#fb7185'} strokeWidth="3" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="flex justify-between font-mono text-[9px] text-muted">
        <span>{points[0].date}</span>
        <span>区间 {formatPrice(low)} – {formatPrice(high)}</span>
        <span>{points[points.length - 1].date}</span>
      </div>
    </div>
  )
}

export function UsMarketInstrumentDetail({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const detail = useQuery({
    queryKey: QK.usMarketInstrument(symbol),
    queryFn: () => api.usMarketInstrument(symbol),
    staleTime: 60_000,
  })
  const history = useQuery({
    queryKey: QK.usMarketDaily(symbol, 260),
    queryFn: () => api.usMarketDaily(symbol, 260),
    staleTime: 4 * 60 * 60 * 1000,
  })
  const instrument = detail.data?.instrument
  const recent = history.data?.rows.slice(-8).reverse() ?? []

  return (
    <div className="mt-3 overflow-hidden rounded-card border border-sky-400/20 bg-surface/90">
      <div className="flex items-start justify-between gap-4 border-b border-border px-4 py-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <LineChart className="h-4 w-4 text-sky-400" />
            <h3 className="text-sm font-semibold text-foreground">{instrument?.name || symbol}</h3>
            <span className="rounded-full bg-sky-400/10 px-2 py-0.5 font-mono text-[9px] text-sky-300">{symbol.replace('.US', '')}</span>
            {history.data && <span className="text-[9px] text-muted">{history.data.status === 'live' ? '实时缓存' : '历史快照'}</span>}
          </div>
          <p className="mt-1 text-[10px] text-muted">{instrument?.name_en || '正在读取基础档案与历史行情'}</p>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭股票详情" className="rounded p-1 text-muted hover:bg-elevated hover:text-foreground">
          <X className="h-4 w-4" />
        </button>
      </div>

      {detail.isLoading ? (
        <div className="flex items-center justify-center gap-2 py-16 text-xs text-muted"><Loader2 className="h-4 w-4 animate-spin text-sky-400" />正在加载基础档案…</div>
      ) : detail.isError || !instrument ? (
        <div className="py-14 text-center text-xs text-rose-400">{detail.error instanceof Error ? detail.error.message : '股票档案不可用'}</div>
      ) : (
        <>
          <div className="grid gap-2 border-b border-border bg-base/20 p-4 sm:grid-cols-3 lg:grid-cols-6">
            {[
              ['现价', formatPrice(instrument.last_price)],
              ['涨跌幅', formatPct(instrument.change_pct)],
              ['总市值', formatNumber(instrument.market_cap)],
              ['行业', instrument.sector || '未分类'],
              ['国家', instrument.country || '—'],
              ['IPO', instrument.ipo_year?.toString() || '—'],
            ].map(([label, value], index) => (
              <div key={label} className="rounded-lg border border-border/60 bg-surface px-3 py-2">
                <div className="text-[9px] text-muted">{label}</div>
                <div className={`mt-1 truncate font-mono text-xs font-semibold ${index === 1 ? changeClass(instrument.change_pct) : 'text-foreground'}`}>{value}</div>
              </div>
            ))}
          </div>

          <div className="grid gap-4 p-4 xl:grid-cols-[minmax(0,1fr)_360px]">
            <div className="min-w-0 rounded-lg border border-border/60 bg-base/20 p-3">
              <div className="mb-1 flex items-center justify-between gap-3">
                <span className="text-[10px] font-medium text-secondary">近 260 个交易日 · 不复权收盘价</span>
                {instrument.profile_url && (
                  <a href={instrument.profile_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[9px] text-sky-400 hover:text-sky-300">
                    Nasdaq 档案 <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </div>
              {history.isLoading ? (
                <div className="flex h-48 items-center justify-center gap-2 text-xs text-muted"><Loader2 className="h-4 w-4 animate-spin text-sky-400" />正在加载历史行情…</div>
              ) : history.isError ? (
                <div className="flex h-48 items-center justify-center text-xs text-rose-400">{history.error instanceof Error ? history.error.message : '历史行情不可用'}</div>
              ) : <HistoryChart rows={history.data?.rows ?? []} />}
            </div>

            <div className="overflow-hidden rounded-lg border border-border/60">
              <div className="bg-elevated px-3 py-2 text-[10px] font-medium text-secondary">最近交易日</div>
              <table className="w-full text-[9px]">
                <thead className="text-muted"><tr><th className="px-3 py-1.5 text-left font-medium">日期</th><th className="px-2 py-1.5 text-right font-medium">收盘</th><th className="px-3 py-1.5 text-right font-medium">涨跌</th></tr></thead>
                <tbody className="divide-y divide-border/60">
                  {recent.map(row => (
                    <tr key={row.date}><td className="px-3 py-1.5 font-mono text-secondary">{row.date}</td><td className="px-2 py-1.5 text-right font-mono text-foreground">{formatPrice(row.close)}</td><td className={`px-3 py-1.5 text-right font-mono ${changeClass(row.change_pct)}`}>{formatPct(row.change_pct)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

export function UsStockExplorer({ sectors }: { sectors: UsMarketGroupSummary[] }) {
  const [search, setSearch] = useState('')
  const deferredSearch = useDeferredValue(search.trim())
  const [sector, setSector] = useState('')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  const catalog = useQuery({
    queryKey: QK.usMarketInstruments(deferredSearch, sector, offset),
    queryFn: () => api.usMarketInstruments({
      q: deferredSearch,
      sector,
      limit: PAGE_SIZE,
      offset,
    }),
    staleTime: 60_000,
    placeholderData: previous => previous,
  })
  const data = catalog.data
  const canPrevious = offset > 0
  const canNext = data != null && offset + PAGE_SIZE < data.matched

  return (
    <section>
      <div className="mb-2.5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><Database className="h-4 w-4 text-sky-400" /><h2 className="text-sm font-semibold text-foreground">美股全量证券目录</h2></div>
          <p className="mt-1 text-[10px] text-muted">
            {data ? `${data.total.toLocaleString()} 个代码 · ${data.classified_count.toLocaleString()} 个有行业分类 · ${data.quote_coverage_count.toLocaleString()} 个有行情` : '全量基础档案、行业归属与按需历史行情'}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <label className="relative block">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
            <input
              value={search}
              onChange={event => { setSearch(event.target.value); setOffset(0) }}
              placeholder="搜索代码或名称"
              className="h-8 w-48 rounded-md border border-border bg-elevated pl-8 pr-3 text-xs text-foreground outline-none focus:border-sky-400/50"
            />
          </label>
          <select
            value={sector}
            onChange={event => { setSector(event.target.value); setOffset(0) }}
            className="h-8 rounded-md border border-border bg-elevated px-2.5 text-xs text-secondary outline-none focus:border-sky-400/50"
          >
            <option value="">全部行业</option>
            {sectors.map(item => <option key={item.id} value={item.name_en}>{item.name}</option>)}
          </select>
        </div>
      </div>

      <div className="overflow-hidden rounded-card border border-border bg-surface/85">
        {catalog.isLoading ? (
          <div className="flex items-center justify-center gap-2 py-20 text-xs text-muted"><Loader2 className="h-4 w-4 animate-spin text-sky-400" />首次加载正在建立全量档案缓存…</div>
        ) : catalog.isError ? (
          <div className="py-16 text-center text-xs text-rose-400">{catalog.error instanceof Error ? catalog.error.message : '全量股票目录不可用'}</div>
        ) : data && data.rows.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-[10px]">
              <thead className="bg-elevated text-muted"><tr><th className="px-4 py-2 font-medium">股票</th><th className="px-3 py-2 font-medium">行业 / 子行业</th><th className="px-3 py-2 font-medium">国家</th><th className="px-3 py-2 text-right font-medium">市值</th><th className="px-3 py-2 text-right font-medium">现价</th><th className="px-4 py-2 text-right font-medium">涨跌幅</th></tr></thead>
              <tbody className="divide-y divide-border/60">
                {data.rows.map(row => (
                  <tr key={row.symbol} onClick={() => setSelected(row.symbol)} className="cursor-pointer hover:bg-elevated/35">
                    <td className="px-4 py-2"><div className="font-medium text-foreground">{row.name}</div><div className="font-mono text-[9px] text-muted">{row.symbol.replace('.US', '')}{row.name_en ? ` · ${row.name_en}` : ''}</div></td>
                    <td className="max-w-80 px-3 py-2"><div className="text-secondary">{row.sector || '未分类'}</div><div className="truncate text-[9px] text-muted">{row.industry || '—'}</div></td>
                    <td className="px-3 py-2 text-secondary">{row.country || '—'}</td>
                    <td className="px-3 py-2 text-right font-mono text-secondary">{formatNumber(row.market_cap)}</td>
                    <td className="px-3 py-2 text-right font-mono text-foreground">{formatPrice(row.last_price)}</td>
                    <td className={`px-4 py-2 text-right font-mono font-semibold ${changeClass(row.change_pct)}`}>{formatPct(row.change_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div className="py-16 text-center text-xs text-muted">没有匹配的美股代码</div>}

        {data && (
          <div className="flex items-center justify-between border-t border-border px-4 py-2 text-[10px] text-muted">
            <span>匹配 {data.matched.toLocaleString()} 个 · 第 {Math.floor(offset / PAGE_SIZE) + 1} 页</span>
            <div className="flex gap-1">
              <button type="button" disabled={!canPrevious} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))} className="rounded border border-border p-1.5 text-secondary hover:bg-elevated disabled:opacity-30" aria-label="上一页"><ChevronLeft className="h-3.5 w-3.5" /></button>
              <button type="button" disabled={!canNext} onClick={() => setOffset(offset + PAGE_SIZE)} className="rounded border border-border p-1.5 text-secondary hover:bg-elevated disabled:opacity-30" aria-label="下一页"><ChevronRight className="h-3.5 w-3.5" /></button>
            </div>
          </div>
        )}
      </div>

      {selected && <UsMarketInstrumentDetail symbol={selected} onClose={() => setSelected(null)} />}
    </section>
  )
}
