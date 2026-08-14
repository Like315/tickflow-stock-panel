import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Bot,
  ChevronDown,
  CircleHelp,
  Loader2,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  ShoppingCart,
  TrendingDown,
  TrendingUp,
  WalletCards,
} from 'lucide-react'
import { api, type FundMarketFund, type FundMarketResearchResult, type FundMarketTier } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { askResearchAgent } from '@/lib/researchAgentStore'

const TIER_META: Record<FundMarketTier, { label: string; icon: typeof TrendingUp; text: string; chip: string; ring: string }> = {
  可买入: { label: '可买入', icon: ShoppingCart, text: 'text-bull', chip: 'bg-bull/10 text-bull', ring: 'border-bull/25' },
  长期持有: { label: '长期持有', icon: ShieldCheck, text: 'text-accent', chip: 'bg-accent/10 text-accent', ring: 'border-accent/25' },
  减仓: { label: '减仓', icon: TrendingDown, text: 'text-bear', chip: 'bg-bear/10 text-bear', ring: 'border-bear/25' },
  观望: { label: '观望', icon: CircleHelp, text: 'text-muted', chip: 'bg-elevated text-muted', ring: 'border-border' },
}

const TIER_ORDER: FundMarketTier[] = ['可买入', '长期持有', '减仓', '观望']
const REGIME_META: Record<string, { label: string; text: string; chip: string }> = {
  上行: { label: '上行', text: 'text-bull', chip: 'bg-bull/10 text-bull' },
  下行: { label: '下行', text: 'text-bear', chip: 'bg-bear/10 text-bear' },
  震荡: { label: '震荡', text: 'text-warning', chip: 'bg-warning/10 text-warning' },
  未知: { label: '未知', text: 'text-muted', chip: 'bg-elevated text-muted' },
}

function pct(value: number | null | undefined, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--'
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}%`
}

function toneFor(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'text-foreground'
  return value > 0 ? 'text-bull' : 'text-bear'
}

function valueText(value: number | null | undefined, suffix = ''): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--'
  return `${value.toFixed(2)}${suffix}`
}

function MetricChip({ label, value, tone = 'text-foreground' }: { label: string; value: string; tone?: string }) {
  return (
    <div className="min-w-0 rounded-md bg-elevated/60 px-2 py-1.5">
      <div className="text-[9px] text-muted">{label}</div>
      <div className={`mt-0.5 truncate font-mono text-[11px] font-medium ${tone}`}>{value}</div>
    </div>
  )
}

function FundCard({ fund }: { fund: FundMarketFund }) {
  const meta = TIER_META[fund.recommendation.tier]
  const perf = fund.performance_pct ?? {}
  const returns = [
    { label: '近1月', value: pct(perf['1m']), tone: 'text-foreground' },
    { label: '近3月', value: pct(perf['3m']), tone: 'text-foreground' },
    { label: '近6月', value: pct(perf['6m']), tone: toneFor(perf['6m']) },
    { label: '近1年', value: pct(perf['1y']), tone: toneFor(perf['1y']) },
  ]
  return (
    <div className={`rounded-card border bg-surface/80 ${meta.ring} p-3`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-xs font-medium text-foreground">{fund.name || `基金 ${fund.code}`}</div>
      <div className="mt-0.5 flex items-center gap-1.5 font-mono text-[10px] text-muted">
        <span>{fund.code}</span>
        <span className="rounded-full border border-border px-1.5 py-px text-[9px] text-secondary">{fund.category}</span>
        {fund.held && <span className="rounded-full border border-accent/30 bg-accent/10 px-1.5 py-px text-[9px] text-accent">持有中</span>}
      </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {typeof fund.score === 'number' && <span className="font-mono text-[10px] text-muted">{fund.score.toFixed(0)}分</span>}
          <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${meta.chip}`}>
            <meta.icon className="h-3 w-3" />{fund.recommendation.tier}
          </span>
        </div>
      </div>
      <div className="mt-2.5 grid grid-cols-4 gap-1.5">
        {returns.map(item => <MetricChip key={item.label} label={item.label} value={item.value} tone={item.tone} />)}
      </div>
      <div className="mt-1.5 grid grid-cols-3 gap-1.5">
        <MetricChip label="超额(6月)" value={pct(fund.alpha_6m_pct)} tone={(fund.alpha_6m_pct ?? 0) >= 0 ? 'text-bull' : 'text-bear'} />
        <MetricChip label="年化波动" value={valueText(fund.annualized_volatility_pct, '%')} />
        <MetricChip label="1年最大回撤" value={pct(fund.max_drawdown_1y_pct)} tone={(fund.max_drawdown_1y_pct ?? 0) < -20 ? 'text-bear' : 'text-foreground'} />
      </div>
      <ul className="mt-2.5 space-y-1 border-t border-border/60 pt-2">
        {fund.recommendation.reasons.slice(0, 3).map((reason, index) => (
          <li key={index} className="flex gap-1.5 text-[10px] leading-4 text-secondary">
            <span className="mt-0.5 h-1 w-1 shrink-0 rounded-full bg-purple-400/70" />
            <span>{reason}</span>
          </li>
        ))}
      </ul>
      {fund.benchmark_note && <p className="mt-2 text-[9px] leading-4 text-muted">{fund.benchmark_note}</p>}
    </div>
  )
}

function GroupSection({ tier, funds }: { tier: FundMarketTier; funds: FundMarketFund[] }) {
  const meta = TIER_META[tier]
  if (!funds.length) return null
  return (
    <section className="min-w-0">
      <div className="mb-2 flex items-center gap-2">
        <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-medium ${meta.chip}`}>
          <meta.icon className="h-3.5 w-3.5" />{meta.label}
        </span>
        <span className="text-[10px] text-muted">{funds.length} 只</span>
        {tier === '可买入' && <span className="text-[9px] text-muted">· 按类别分散，最多 5 只</span>}
      </div>
      <div className="space-y-2.5">
        {funds.map(fund => <FundCard key={fund.code} fund={fund} />)}
      </div>
    </section>
  )
}

function HeldFundsSection({ funds }: { funds: FundMarketFund[] }) {
  const [open, setOpen] = useState(true)
  if (!funds.length) return null
  const tiers = TIER_ORDER.filter(tier => funds.some(fund => fund.recommendation.tier === tier))
  return (
    <section className="overflow-hidden rounded-card border border-accent/20 bg-accent/[0.03]">
      <button type="button" onClick={() => setOpen(value => !value)} className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-accent/[0.04]">
        <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-foreground">
          <WalletCards className="h-3.5 w-3.5 text-accent" />我持有的
        </span>
        <span className="text-[10px] text-muted">{funds.length} 只 · 档位为持有/调整建议</span>
        <ChevronDown className={`ml-auto h-3.5 w-3.5 text-muted transition-transform ${open ? '' : '-rotate-90'}`} />
      </button>
      {open && (
        <div className="px-3 pb-3">
          <p className="mb-2.5 text-[9px] leading-4 text-muted">研判仍基于历史净值与大盘，与你的持仓成本无关</p>
          <div className="space-y-3">
            {tiers.map(tier => {
              const meta = TIER_META[tier]
              const tierFunds = funds.filter(fund => fund.recommendation.tier === tier)
              return (
                <div key={tier}>
                  <div className="mb-1.5 flex items-center gap-2">
                    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${meta.chip}`}>
                      <meta.icon className="h-3 w-3" />{tier === '可买入' ? '可买入（加仓候选）' : meta.label}
                    </span>
                    <span className="text-[9px] text-muted">{tierFunds.length} 只</span>
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                    {tierFunds.map(fund => <FundCard key={fund.code} fund={fund} />)}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </section>
  )
}

function ExternalMarketSection({ funds }: { funds: FundMarketFund[] }) {
  const [open, setOpen] = useState(true)
  const watchCount = funds.filter(fund => fund.recommendation.tier === '观望').length
  if (!funds.length) return null
  return (
    <section className="overflow-hidden rounded-card border border-border bg-surface/40">
      <button type="button" onClick={() => setOpen(value => !value)} className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-elevated/30">
        <span className="text-xs font-semibold text-foreground">外部市场</span>
        <span className="text-[10px] text-muted">{funds.length} 只 · 未持有基金</span>
        <ChevronDown className={`ml-auto h-3.5 w-3.5 text-muted transition-transform ${open ? '' : '-rotate-90'}`} />
      </button>
      {open && (
        <div className="space-y-3 px-3 pb-3">
          <div className="grid gap-4 xl:grid-cols-3">
            {TIER_ORDER.filter(tier => tier !== '观望').map(tier => (
              <GroupSection key={tier} tier={tier} funds={funds.filter(fund => fund.recommendation.tier === tier)} />
            ))}
          </div>
          {watchCount > 0 && (
            <details className="rounded-card border border-border bg-surface/50 px-3 py-2.5">
              <summary className="cursor-pointer text-[10px] font-medium text-secondary">观望名单（{watchCount} 只，指标中性或数据不足）</summary>
              <div className="mt-2.5 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {funds.filter(fund => fund.recommendation.tier === '观望').map(fund => <FundCard key={fund.code} fund={fund} />)}
              </div>
            </details>
          )}
        </div>
      )}
    </section>
  )
}

function MarketContext({ result }: { result: FundMarketResearchResult }) {
  const regime = REGIME_META[result.market_regime.regime] ?? REGIME_META['未知']
  return (
    <div className="rounded-card border border-border bg-surface/80 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold text-foreground">大盘环境</span>
        <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium ${regime.chip}`}>
          {result.market_regime.regime === '上行' ? <TrendingUp className="h-3.5 w-3.5" /> : result.market_regime.regime === '下行' ? <TrendingDown className="h-3.5 w-3.5" /> : <CircleHelp className="h-3.5 w-3.5" />}
          {regime.label}
        </span>
        <span className="text-[10px] text-muted">{result.market_regime.label}</span>
      </div>
      <div className="mt-3 grid gap-1.5 sm:grid-cols-2 lg:grid-cols-4">
        {result.market_context.map(index => (
          <div key={index.symbol} className="rounded-md bg-elevated/50 px-2.5 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-[10px] text-secondary">{index.name}</span>
              <span className={`shrink-0 font-mono text-[10px] ${index.trend === '上行' ? 'text-bull' : index.trend === '下行' ? 'text-bear' : 'text-muted'}`}>{index.trend}</span>
            </div>
            <div className="mt-1 flex items-center gap-2 font-mono text-[10px] text-muted">
              <span>20日 {pct(index.return_20d_pct)}</span>
              <span>60日 {pct(index.return_60d_pct)}</span>
            </div>
          </div>
        ))}
      </div>
      <p className="mt-2.5 text-[9px] leading-4 text-muted">依据公开基金净值历史与本地大盘指数计算，仅供研究参考，不构成投资建议。</p>
    </div>
  )
}

export function FundMarketResearchPanel() {
  const research = useQuery({
    queryKey: QK.fundMarketResearch,
    queryFn: () => api.fundMarketResearchRun(),
    staleTime: 15 * 60 * 1000,
    refetchOnWindowFocus: false,
  })
  const result = research.data

  const ask = (question: string) => {
    askResearchAgent(question, { context: 'fund_market', contextLabel: '基金市场研究' })
  }

  return (
    <section className="mt-4">
      <div className="flex flex-wrap items-center gap-2 border-y border-border bg-surface/45 px-3 py-2.5">
        <span className="mr-1 inline-flex items-center gap-1.5 text-[10px] font-medium text-secondary">
          <Bot className="h-3.5 w-3.5 text-purple-400" />基金 AI 研究
        </span>
        <button type="button" onClick={() => research.refetch()} disabled={research.isFetching} className="inline-flex items-center gap-1 rounded-btn bg-purple-500 px-2.5 py-1.5 text-[10px] font-medium text-white hover:bg-purple-500/90 disabled:opacity-50">
          {research.isFetching ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}{result ? '重新研究' : '运行研究'}
        </button>
        <button type="button" onClick={() => ask('请基于历史净值和大盘趋势，对本次基金市场研究做完整解读：哪些基金适合长期持有、哪些需要减仓、哪些可以买入，并说明理由与风险。')} className="inline-flex items-center gap-1 rounded-btn border border-purple-500/30 bg-purple-500/[0.06] px-2.5 py-1.5 text-[10px] text-purple-400 hover:bg-purple-500/10">
          <Bot className="h-3 w-3" />AI 深度解读
        </button>
        <span className="ml-auto text-[9px] text-muted">基于历史净值与大盘趋势，不依赖持仓</span>
      </div>

      {research.isLoading && !result && (
        <div className="flex items-center justify-center gap-2 rounded-card border border-dashed border-border py-14 text-xs text-muted">
          <Loader2 className="h-4 w-4 animate-spin text-purple-400" />正在抓取基金净值历史并分析大盘趋势（约 20 只基金）…
        </div>
      )}

      {research.isError && !result && (
        <div className="rounded-card border border-danger/25 bg-danger/[0.06] p-4 text-xs text-danger">
          基金研究运行失败：{research.error instanceof Error ? research.error.message : '请稍后重试'}
        </div>
      )}

      {result && (
        <div className="space-y-3">
          <MarketContext result={result} />
          {result.data_gaps.length > 0 && (
            <div className="rounded-card border border-warning/25 bg-warning/[0.06] px-3 py-2 text-[10px] leading-4 text-warning">
              <span className="font-medium">数据缺口：</span>{result.data_gaps.join('；')}
            </div>
          )}
          <HeldFundsSection funds={result.funds.filter(fund => fund.held)} />
          <ExternalMarketSection funds={result.funds.filter(fund => !fund.held)} />
          {result.universe_count > 0 && (
            <p className="flex items-center gap-1.5 text-[9px] leading-4 text-muted">
              <ShieldAlert className="h-3 w-3" />
              量化研判基于公开历史数据，非收益预测或自动交易指令；基金名称、来源等外部信息不作决策依据。数据截至 {result.as_of || '--'}。
            </p>
          )}
        </div>
      )}
    </section>
  )
}
