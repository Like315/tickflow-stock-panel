import { useMemo, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  Crown,
  Flame,
  Landmark,
  Layers3,
  RefreshCw,
  Repeat,
  Search,
  TrendingUp,
} from 'lucide-react'
import { PageHeader } from '@/components/PageHeader'
import { EmptyState } from '@/components/EmptyState'
import { StockPreviewDialog } from '@/components/StockPreviewDialog'
import { api, type LeadingSectorItem } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { fmtBigNum, fmtPct, priceColorClass } from '@/lib/format'
import { cn } from '@/lib/cn'

const MIN_DAYS = 7
const MAX_DAYS = 30
const DEFAULT_DAYS = 12
const TOP_N = 30

type Kind = 'concept' | 'industry'
type Level = 1 | 2 | 3

const PART_META: { key: keyof LeadingSectorItem['parts']; label: string; cls: string }[] = [
  { key: 'persistence', label: '排名持续性', cls: 'bg-rose-400' },
  { key: 'capital', label: '资金强度', cls: 'bg-blue-400' },
  { key: 'leader', label: '龙头股强度', cls: 'bg-amber-300' },
]

function shortDate(s: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s)
  if (!m) return s
  return `${Number(m[2])}/${m[3]}`
}

export function LeadingSector() {
  const [kind, setKind] = useState<Kind>('concept')
  const [level, setLevel] = useState<Level>(2)
  const [days, setDays] = useState(DEFAULT_DAYS)
  const [search, setSearch] = useState('')
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [previewSymbol, setPreviewSymbol] = useState<string | null>(null)
  const [previewName, setPreviewName] = useState<string>('')

  const dimLabel = kind === 'industry' ? '行业' : '概念'
  const lvParam = kind === 'industry' ? level : undefined

  const query = useQuery({
    queryKey: QK.leadingSectors(days, kind, lvParam),
    queryFn: () => api.leadingSectors(days, kind, lvParam, TOP_N),
    staleTime: 60_000,
  })

  const data = query.data
  const sectors = data?.sectors ?? []

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return q ? sectors.filter(s => s.name.toLowerCase().includes(q)) : sectors
  }, [sectors, search])

  const selected =
    filtered.find(s => s.name === selectedName) ?? filtered[0] ?? null

  const hero = sectors[0] ?? null
  const totalAmount = useMemo(
    () => sectors.reduce((sum, s) => sum + (s.total_amount || 0), 0),
    [sectors],
  )
  const avgScore = useMemo(
    () => (sectors.length ? sectors.reduce((sum, s) => sum + s.score, 0) / sectors.length : 0),
    [sectors],
  )

  return (
    <>
      <PageHeader
        title="龙头板块"
        subtitle={
          data?.as_of
            ? `${data.as_of} · ${data.days} 个交易日 · ${data.sector_count} 个${dimLabel}`
            : '加载中…'
        }
        right={
          <div className="flex items-center gap-2">
            {/* 维度切换: 概念 / 行业 */}
            <div className="flex items-center rounded-btn border border-border bg-base/60 p-0.5">
              {([
                ['concept', '概念', Layers3],
                ['industry', '行业', Landmark],
              ] as [Kind, string, typeof Layers3][]).map(([k, label, Icon]) => (
                <button
                  key={k}
                  onClick={() => { setKind(k); setSelectedName(null) }}
                  className={cn(
                    'inline-flex items-center gap-1 rounded-[5px] px-2.5 py-1 text-[11px] font-medium transition-colors cursor-pointer',
                    kind === k ? 'bg-accent text-white shadow-sm' : 'text-secondary hover:text-foreground',
                  )}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                </button>
              ))}
            </div>
            {/* 行业层级选择器: 仅 kind=industry 显示 */}
            {kind === 'industry' && (
              <div className="flex items-center rounded-btn border border-border bg-base/60 p-0.5">
                {[1, 2, 3].map(lv => (
                  <button
                    key={lv}
                    onClick={() => { setLevel(lv as Level); setSelectedName(null) }}
                    className={cn(
                      'h-6 rounded-[5px] px-2 text-[10px] font-medium transition-colors cursor-pointer',
                      level === lv ? 'bg-accent text-white shadow-sm' : 'text-secondary hover:text-foreground',
                    )}
                  >
                    {lv}级
                  </button>
                ))}
              </div>
            )}
            {/* 天数滑杆 */}
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-muted">天数</span>
              <input
                type="range"
                min={MIN_DAYS}
                max={MAX_DAYS}
                step={1}
                value={days}
                onChange={e => { setDays(Number(e.target.value)); setSelectedName(null) }}
                className="w-20 accent-accent cursor-pointer"
              />
              <span className="text-[11px] text-secondary tabular-nums w-5">{days}</span>
            </div>
            <button
              onClick={() => query.refetch()}
              disabled={query.isFetching}
              className="p-1.5 text-muted hover:bg-surface disabled:opacity-50"
              title="刷新"
            >
              <RefreshCw className={cn('h-4 w-4', query.isFetching && 'animate-spin')} />
            </button>
          </div>
        }
      />

      <div className="min-h-full bg-[radial-gradient(circle_at_12%_0%,rgba(245,158,11,0.10),transparent_28%),radial-gradient(circle_at_85%_8%,rgba(59,130,246,0.08),transparent_28%)] px-6 py-5">
        <div className="mx-auto max-w-[1440px] space-y-5">
          {/* 顶部概览 */}
          <HeroPanel
            hero={hero}
            dimLabel={dimLabel}
            sectorCount={data?.sector_count ?? 0}
            totalAmount={totalAmount}
            avgScore={avgScore}
          />

          {query.isLoading ? (
            <div className="rounded-2xl border border-border bg-surface px-6 py-16 text-center text-sm text-muted">正在计算龙头板块…</div>
          ) : query.error ? (
            <EmptyState icon={Crown} title="加载失败" hint="请稍后重试或检查数据源" />
          ) : sectors.length === 0 ? (
            <EmptyState
              icon={Crown}
              title="暂无龙头板块数据"
              hint={`请先在「${kind === 'industry' ? '行业分析' : '概念分析'}」页获取${dimLabel}数据源, 并确认已同步日线行情`}
            />
          ) : (
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-[20rem_1fr]">
              {/* 左侧: 板块排行 */}
              <SectorRail
                sectors={filtered}
                dimLabel={dimLabel}
                selectedName={selected?.name ?? null}
                search={search}
                onSearch={v => { setSearch(v); setSelectedName(null) }}
                onSelect={setSelectedName}
              />

              {/* 右侧: 选中板块详情 */}
              <SectorFocus
                sector={selected}
                dimLabel={dimLabel}
                onStockClick={(symbol, name) => { setPreviewSymbol(symbol); setPreviewName(name ?? '') }}
              />
            </div>
          )}
        </div>
      </div>

      {previewSymbol && (
        <StockPreviewDialog
          symbol={previewSymbol}
          name={previewName}
          onClose={() => { setPreviewSymbol(null); setPreviewName('') }}
        />
      )}
    </>
  )
}

// ===== 顶部概览 =====

function HeroPanel({ hero, dimLabel, sectorCount, totalAmount, avgScore }: {
  hero: LeadingSectorItem | null
  dimLabel: string
  sectorCount: number
  totalAmount: number
  avgScore: number
}) {
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
      <HeroMetric
        icon={Crown}
        label={`龙头${dimLabel}`}
        value={hero?.name ?? '—'}
        hint={hero ? <span className="font-mono text-amber-300">{hero.score.toFixed(1)} 分</span> : '等待数据'}
        tone="gold"
      />
      <HeroMetric
        icon={Activity}
        label={`${dimLabel}总数`}
        value={sectorCount > 0 ? sectorCount : '—'}
        hint={hero ? `${hero.count} 只成分` : ''}
        tone="blue"
      />
      <HeroMetric
        icon={TrendingUp}
        label="窗口总成交"
        value={totalAmount > 0 ? fmtBigNum(totalAmount) : '—'}
        hint="全部上榜板块"
        tone="up"
      />
      <HeroMetric
        icon={Repeat}
        label="上榜均分"
        value={avgScore > 0 ? avgScore.toFixed(1) : '—'}
        hint="龙头分 0-100"
        tone="blue"
      />
    </div>
  )
}

function HeroMetric({ icon: Icon, label, value, hint, tone }: {
  icon: typeof Crown
  label: string
  value: ReactNode
  hint: ReactNode
  tone: 'up' | 'down' | 'gold' | 'blue'
}) {
  const toneClass = {
    up: 'text-bull bg-bull/10',
    down: 'text-bear bg-bear/10',
    gold: 'text-amber-300 bg-amber-400/10',
    blue: 'text-blue-300 bg-blue-400/10',
  }[tone]
  const valueClass = {
    up: 'text-bull',
    down: 'text-bear',
    gold: 'text-amber-300',
    blue: 'text-foreground',
  }[tone]
  return (
    <div className="rounded-xl border border-border bg-surface px-3 py-2">
      <div className="flex items-center justify-between text-[11px] text-muted">
        <span>{label}</span>
        <span className={cn('rounded-md p-1', toneClass)}><Icon className="h-3.5 w-3.5" /></span>
      </div>
      <div className={cn('mt-1 truncate text-sm font-semibold', valueClass)}>{value}</div>
      <div className="mt-0.5 truncate text-[11px] text-muted">{hint}</div>
    </div>
  )
}

// ===== 左侧排行 =====

function SectorRail({
  sectors,
  dimLabel,
  selectedName,
  search,
  onSearch,
  onSelect,
}: {
  sectors: LeadingSectorItem[]
  dimLabel: string
  selectedName: string | null
  search: string
  onSearch: (v: string) => void
  onSelect: (v: string) => void
}) {
  return (
    <section className="rounded-2xl border border-border bg-surface p-2.5">
      <div className="px-1 pb-2.5">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-foreground">龙头{dimLabel}排行</h3>
          <span className="text-[10px] text-muted">Top {sectors.length}</span>
        </div>
        <div className="mt-2 relative">
          <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
          <input
            value={search}
            onChange={e => onSearch(e.target.value)}
            placeholder={`搜索${dimLabel}`}
            className="h-8 w-full rounded-lg border border-border bg-base pl-8 pr-3 text-xs text-foreground outline-none focus:border-accent/50"
          />
        </div>
      </div>
      <div className="max-h-[640px] overflow-auto rounded-lg border border-border/50">
        {sectors.map((item, idx) => {
          const active = selectedName === item.name
          return (
            <button
              key={item.name}
              onClick={() => onSelect(item.name)}
              className={cn(
                'w-full border-b border-border/50 px-2.5 py-2 text-left transition-colors last:border-b-0',
                active ? 'bg-amber-400/[0.08]' : 'hover:bg-elevated/40',
              )}
            >
              <div className="flex items-center gap-2">
                <span className={cn(
                  'flex h-5 w-5 shrink-0 items-center justify-center rounded-md font-mono text-[10px]',
                  idx < 3 ? 'bg-amber-400/15 text-amber-300' : 'bg-elevated/70 text-muted',
                )}>{idx + 1}</span>
                <span className="min-w-0 flex-1 truncate text-xs font-medium text-foreground">{item.name}</span>
                <span className="font-mono text-xs text-amber-300">{item.score.toFixed(1)}</span>
              </div>
              {/* 三因子迷你条 */}
              <div className="ml-7 mt-1.5 space-y-1">
                {PART_META.map(p => (
                  <div key={p.key} className="flex items-center gap-1.5">
                    <span className="w-14 shrink-0 text-[9px] text-muted">{p.label}</span>
                    <div className="h-1 flex-1 overflow-hidden rounded-full bg-elevated">
                      <div className={cn('h-full rounded-full', p.cls)} style={{ width: `${Math.max(3, item.parts[p.key])}%` }} />
                    </div>
                    <span className="w-7 shrink-0 text-right font-mono text-[9px] text-muted">{item.parts[p.key].toFixed(0)}</span>
                  </div>
                ))}
              </div>
            </button>
          )
        })}
        {sectors.length === 0 && (
          <div className="px-3 py-8 text-center text-[11px] text-muted">无匹配{dimLabel}</div>
        )}
      </div>
    </section>
  )
}

// ===== 右侧详情 =====

function SectorFocus({ sector, dimLabel, onStockClick }: {
  sector: LeadingSectorItem | null
  dimLabel: string
  onStockClick: (symbol: string, name?: string) => void
}) {
  if (!sector) return null

  const champion = sector.champion
  const leaders = sector.daily_leaders ?? []

  return (
    <section className="flex max-h-[720px] flex-col overflow-hidden rounded-2xl border border-border bg-surface">
      {/* 头部: 板块名 + 关键指标 */}
      <div className="shrink-0 border-b border-border px-5 py-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-3">
              <h3 className="truncate text-xl font-semibold text-foreground">{sector.name}</h3>
              <span className="rounded-full bg-amber-400/10 px-2 py-0.5 text-[10px] text-amber-300">龙头分 {sector.score.toFixed(1)}</span>
            </div>
            <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
              <span>{sector.count} 只成分</span>
              <span className={priceColorClass(sector.avg_pct)}>均涨 {fmtPct(sector.avg_pct)}</span>
              <span>成交 {fmtBigNum(sector.total_amount)}</span>
              <span>平均榜位 #{sector.avg_rank.toFixed(1)}</span>
              <span>前10天数 {sector.top10_days}</span>
            </div>
          </div>
          {/* 三因子拆解 */}
          <div className="grid w-full gap-2 lg:w-[420px]">
            {PART_META.map(p => (
              <div key={p.key} className="flex items-center gap-2">
                <span className="w-16 shrink-0 text-[10px] text-muted">{p.label}</span>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-elevated">
                  <div className={cn('h-full rounded-full', p.cls)} style={{ width: `${Math.max(3, sector.parts[p.key])}%` }} />
                </div>
                <span className="w-8 shrink-0 text-right font-mono text-[11px] text-foreground">{sector.parts[p.key].toFixed(1)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* 区间冠军 */}
      <div className="shrink-0 border-b border-border bg-base/25 p-4">
        <ChampionCard champion={champion} onStockClick={onStockClick} />
      </div>

      {/* 每日龙头 */}
      <div className="min-h-0 flex-1 overflow-auto">
        <div className="px-4 pt-3 pb-2 text-xs font-medium text-amber-300">
          <span className="inline-flex items-center gap-1.5">
            <Flame className="h-3.5 w-3.5" />
            {dimLabel}每日龙头（最新在前）
          </span>
        </div>
        {leaders.length === 0 ? (
          <div className="px-4 pb-4 text-[11px] text-muted">暂无每日龙头记录</div>
        ) : (
          <table className="min-w-full text-left text-xs">
            <thead className="bg-elevated/60 text-[11px] text-muted">
              <tr>
                <th className="px-4 py-2 font-medium">日期</th>
                <th className="px-4 py-2 font-medium">龙头股</th>
                <th className="px-4 py-2 font-medium">当日涨幅</th>
                <th className="px-4 py-2 font-medium">板块内</th>
                <th className="px-4 py-2 font-medium">涨停</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/70">
              {leaders.map((row, idx) => (
                <tr
                  key={`${row.date}-${row.symbol}-${idx}`}
                  className="hover:bg-elevated/30 cursor-pointer"
                  onClick={() => onStockClick(row.symbol, row.name || undefined)}
                >
                  <td className="px-4 py-2 font-mono text-muted">{shortDate(row.date)}</td>
                  <td className="px-4 py-2">
                    <div className="font-medium text-foreground">{row.name || '—'}</div>
                    <div className="font-mono text-[10px] text-muted">{row.symbol}</div>
                  </td>
                  <td className={cn('px-4 py-2 font-mono tabular-nums', priceColorClass(row.change_pct))}>
                    {row.change_pct != null ? fmtPct(row.change_pct) : '—'}
                  </td>
                  <td className="px-4 py-2 font-mono text-foreground">#{row.rank_in_sector}</td>
                  <td className="px-4 py-2">
                    {row.is_limit_up
                      ? <span className="rounded-full bg-bull/15 px-2 py-0.5 text-[10px] font-medium text-bull">涨停</span>
                      : <span className="text-muted/40">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}

function ChampionCard({ champion, onStockClick }: {
  champion: LeadingSectorItem['champion']
  onStockClick: (symbol: string, name?: string) => void
}) {
  if (!champion) {
    return <div className="rounded-xl border border-border/60 bg-surface p-4 text-sm text-muted">暂无区间冠军</div>
  }
  const plan = champion.trade_plan
  return (
    <div className="rounded-xl border border-amber-400/25 bg-surface p-3">
      <div className="mb-2 flex items-center gap-2 text-xs font-medium text-amber-300">
        <Crown className="h-3.5 w-3.5" />
        区间冠军（龙头股）
      </div>
      <div
        onClick={() => onStockClick(champion.symbol, champion.name || undefined)}
        className="flex cursor-pointer flex-wrap items-center gap-x-6 gap-y-2 hover:brightness-110"
      >
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-foreground">{champion.name || champion.symbol}</div>
          <div className="mt-0.5 font-mono text-[10px] text-muted">{champion.symbol}</div>
        </div>
        <ChampStat label="领涨天数" value={`${champion.lead_days} 天`} cls="text-amber-300" />
        <ChampStat
          label="累计涨幅"
          value={fmtPct(champion.cum_pct)}
          cls={priceColorClass(champion.cum_pct)}
        />
        <ChampStat label="最高连板" value={`${champion.max_boards} 板`} cls="text-bull" />
        <ChampStat label="日均涨幅" value={fmtPct(champion.avg_pct)} cls={priceColorClass(champion.avg_pct)} />
      </div>
      {plan && (
        <div className="mt-3 grid gap-2 border-t border-border/60 pt-3 sm:grid-cols-2 xl:grid-cols-5">
          <ChampStat label="月线趋势" value={plan.monthly_trend ? '多头' : '未通过'} cls={plan.monthly_trend ? 'text-bull' : 'text-muted'} />
          <ChampStat label="周线趋势" value={plan.weekly_trend ? '多头' : '未通过'} cls={plan.weekly_trend ? 'text-bull' : 'text-muted'} />
          <ChampStat label="收盘 / 5日线" value={`${plan.close.toFixed(2)} / ${plan.ma5.toFixed(2)}`} cls={plan.above_ma5 ? 'text-bull' : 'text-bear'} />
          <ChampStat label="10%回撤线" value={plan.drawdown_stop_price.toFixed(2)} cls={plan.drawdown_pct <= -0.1 ? 'text-bear' : 'text-amber-300'} />
          <ChampStat label="策略状态" value={plan.exit_ma5 ? '跌破5日线' : plan.eligible ? '候选' : '观察'} cls={plan.eligible ? 'text-bull' : plan.exit_ma5 ? 'text-bear' : 'text-muted'} />
        </div>
      )}
    </div>
  )
}

function ChampStat({ label, value, cls }: { label: string; value: string; cls: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[10px] text-muted">{label}</span>
      <span className={cn('font-mono text-sm font-semibold tabular-nums', cls)}>{value}</span>
    </div>
  )
}
