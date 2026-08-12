import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowDownRight,
  ArrowUpRight,
  Bot,
  Clock3,
  Edit3,
  Loader2,
  Plus,
  RefreshCw,
  ScanSearch,
  Trash2,
  Upload,
  WalletCards,
} from 'lucide-react'
import { FundImportDialog } from '@/components/funds/FundImportDialog'
import { FundPositionDialog } from '@/components/funds/FundPositionDialog'
import { toast } from '@/components/Toast'
import { api, type FundPosition } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import { askResearchAgent } from '@/lib/researchAgentStore'

const AUTO_REFRESH_MS = 10 * 60 * 1000
const ALLOCATION_COLORS = ['bg-accent', 'bg-bull', 'bg-bear', 'bg-warning', 'bg-cyan-500', 'bg-fuchsia-500']

function finite(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function money(value: number | null | undefined, signed = false): string {
  const number = finite(value)
  if (number == null) return '--'
  const prefix = signed && number > 0 ? '+' : ''
  return `${prefix}${new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(number)}`
}

function numberText(value: number | null | undefined, digits = 4): string {
  const number = finite(value)
  if (number == null) return '--'
  return number.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function percent(value: number | null | undefined): string {
  const number = finite(value)
  if (number == null) return '--'
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`
}

function changeClass(value: number | null | undefined): string {
  const number = finite(value)
  if (number == null || number === 0) return 'text-foreground'
  return number > 0 ? 'text-bull' : 'text-bear'
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed)
}

function sourceLabel(source: string | null): string {
  if (source === 'alipay_screenshot') return '支付宝截图'
  if (source === 'csv') return 'CSV 快照'
  if (source === 'manual') return '手工维护'
  return '本地账本'
}

function SummaryMetric({ label, value, detail, tone = 'text-foreground' }: { label: string; value: string; detail?: string; tone?: string }) {
  return (
    <div className="min-w-0 px-4 py-3 md:px-5">
      <div className="text-[10px] text-muted">{label}</div>
      <div className={`mt-1 truncate font-mono text-xl font-semibold ${tone}`}>{value}</div>
      {detail && <div className="mt-1 truncate text-[10px] text-muted">{detail}</div>}
    </div>
  )
}

export function FundPortfolio() {
  const queryClient = useQueryClient()
  const [importOpen, setImportOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [editing, setEditing] = useState<FundPosition | null>(null)

  const portfolio = useQuery({
    queryKey: QK.fundPortfolio,
    queryFn: api.fundPortfolio,
    staleTime: 30_000,
  })

  const refresh = useMutation({
    mutationFn: api.fundRefresh,
    onSuccess: result => {
      queryClient.setQueryData(QK.fundPortfolio, result.portfolio)
      if (result.refresh.failed > 0) {
        toast(`已刷新 ${result.refresh.updated} 只，${result.refresh.failed} 只暂时失败`, 'error')
      } else {
        toast(`已刷新 ${result.refresh.updated} 只基金估值`, 'success')
      }
    },
  })
  const refreshRef = useRef(refresh.mutate)
  refreshRef.current = refresh.mutate

  const remove = useMutation({
    mutationFn: api.fundDeletePosition,
    onSuccess: result => {
      queryClient.setQueryData(QK.fundPortfolio, result)
      toast('基金持仓已删除', 'success')
    },
  })

  const positionCount = portfolio.data?.summary.position_count ?? 0
  const refreshedAt = portfolio.data?.quotes_refreshed_at ?? null
  useEffect(() => {
    if (positionCount === 0) return
    const lastRefresh = refreshedAt ? new Date(refreshedAt).getTime() : 0
    if (!lastRefresh || Date.now() - lastRefresh >= AUTO_REFRESH_MS) refreshRef.current()
    const timer = window.setInterval(() => refreshRef.current(), AUTO_REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [positionCount]) // eslint-disable-line react-hooks/exhaustive-deps

  if (portfolio.isLoading) {
    return (
      <div className="flex min-h-[70vh] items-center justify-center text-sm text-muted">
        <Loader2 className="mr-2 h-4 w-4 animate-spin text-accent" />正在读取基金账本
      </div>
    )
  }

  if (portfolio.isError || !portfolio.data) {
    return (
      <div className="mx-auto flex min-h-[70vh] max-w-lg items-center px-6">
        <div className="w-full rounded-card border border-danger/30 bg-surface p-7 text-center">
          <WalletCards className="mx-auto h-8 w-8 text-danger" />
          <h1 className="mt-3 text-base font-semibold text-foreground">基金账本暂时无法读取</h1>
          <p className="mt-2 text-xs text-muted">{portfolio.error instanceof Error ? portfolio.error.message : '请稍后重试'}</p>
          <button type="button" onClick={() => portfolio.refetch()} className="mt-5 inline-flex items-center gap-1.5 rounded-btn border border-border bg-elevated px-3 py-1.5 text-xs text-foreground hover:border-accent/50"><RefreshCw className="h-3.5 w-3.5" />重试</button>
        </div>
      </div>
    )
  }

  const data = portfolio.data
  const summary = data.summary
  const sortedPositions = [...data.positions].sort((a, b) => (b.market_value ?? 0) - (a.market_value ?? 0))
  const allocationTotal = sortedPositions.reduce((total, position) => total + Math.max(position.market_value ?? 0, 0), 0)
  const hasIntradayEstimate = sortedPositions.some(position => position.quote_status === 'estimate')

  const openCreate = () => {
    setEditing(null)
    setEditOpen(true)
  }

  const openEdit = (position: FundPosition) => {
    setEditing(position)
    setEditOpen(true)
  }

  const deletePosition = (position: FundPosition) => {
    if (!window.confirm(`确认删除 ${position.name || position.code} 的本地持仓？`)) return
    remove.mutate(position.code)
  }

  const researchPortfolio = (question: string) => {
    askResearchAgent(question, { context: 'fund_portfolio', contextLabel: '当前基金组合' })
  }

  const researchFund = (position: FundPosition) => {
    const label = position.name || position.code
    askResearchAgent(
      `请研究我持有的 ${label}（${position.code}），结合其组合权重、持有盈亏和公开净值历史，分析表现、风险、反向证据和后续验证点。`,
      { context: 'fund', fundCode: position.code, contextLabel: label },
    )
  }

  return (
    <div className="mx-auto max-w-[1600px] p-4 md:p-5">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border pb-4">
        <div className="flex items-start gap-3">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-card border border-accent/30 bg-accent/10 text-accent">
            <WalletCards className="h-4.5 w-4.5" />
          </span>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-base font-semibold text-foreground">基金账户</h1>
              <span className="rounded-full border border-border bg-elevated px-2 py-0.5 text-[10px] text-secondary">{sourceLabel(data.source)}</span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted">
              <span>{summary.position_count} 只基金</span>
              <span className="inline-flex items-center gap-1"><Clock3 className="h-3 w-3" />持仓 {formatTime(data.synced_at)} · 估值 {formatTime(data.quotes_refreshed_at)}</span>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" disabled={summary.position_count === 0} onClick={() => researchPortfolio('请对我的当前基金组合做一次完整体检，重点分析集中度、盈亏贡献、风险来源、数据缺口，并给出后续验证清单。')} className="inline-flex items-center gap-1.5 rounded-btn border border-purple-500/35 bg-purple-500/10 px-3 py-1.5 text-xs text-purple-400 hover:bg-purple-500/15 disabled:cursor-not-allowed disabled:opacity-40"><Bot className="h-3.5 w-3.5" />AI 组合体检</button>
          <button type="button" onClick={openCreate} className="inline-flex items-center gap-1.5 rounded-btn border border-border bg-surface px-3 py-1.5 text-xs text-secondary hover:border-accent/40 hover:text-foreground"><Plus className="h-3.5 w-3.5" />添加基金</button>
          <button type="button" onClick={() => setImportOpen(true)} className="inline-flex items-center gap-1.5 rounded-btn border border-border bg-surface px-3 py-1.5 text-xs text-secondary hover:border-accent/40 hover:text-foreground"><Upload className="h-3.5 w-3.5" />同步快照</button>
          <button type="button" disabled={summary.position_count === 0 || refresh.isPending} onClick={() => refresh.mutate()} className="inline-flex items-center gap-1.5 rounded-btn bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50">
            <RefreshCw className={`h-3.5 w-3.5 ${refresh.isPending ? 'animate-spin' : ''}`} />刷新估值
          </button>
        </div>
      </header>

      <section className="mt-4 grid overflow-hidden rounded-card border border-border bg-surface sm:grid-cols-2 xl:grid-cols-4 sm:[&>*:nth-child(2n)]:border-l xl:[&>*+*]:border-l">
        <SummaryMetric label="总市值（CNY）" value={`¥${money(summary.total_market_value)}`} detail={`持仓成本 ¥${money(summary.total_cost_amount)}`} />
        <SummaryMetric label="持有收益" value={`¥${money(summary.total_holding_profit, true)}`} detail={percent(summary.holding_profit_pct)} tone={changeClass(summary.total_holding_profit)} />
        <SummaryMetric label={hasIntradayEstimate ? '盘中估算收益' : '最新单日收益'} value={`¥${money(summary.total_day_profit, true)}`} detail={hasIntradayEstimate ? '按公开基金估值推算' : '按最近公布净值日涨跌计算'} tone={changeClass(summary.total_day_profit)} />
        <SummaryMetric label="行情状态" value={refresh.isPending ? '刷新中' : data.quotes_refreshed_at ? '已更新' : '待刷新'} detail="页面开启时每 10 分钟更新" tone={refresh.isPending ? 'text-warning' : data.quotes_refreshed_at ? 'text-bear' : 'text-muted'} />
      </section>

      {summary.position_count > 0 && (
        <section className="mt-3 flex flex-wrap items-center gap-2 border-y border-border bg-surface/45 px-3 py-2.5">
          <span className="mr-1 inline-flex items-center gap-1.5 text-[10px] font-medium text-secondary"><ScanSearch className="h-3.5 w-3.5 text-purple-400" />基金 AI 研究</span>
          <button type="button" onClick={() => researchPortfolio('请对我的当前基金组合做一次完整体检，重点分析集中度、盈亏贡献、风险来源、数据缺口，并给出后续验证清单。')} className="rounded-btn border border-border bg-surface px-2.5 py-1.5 text-[10px] text-secondary hover:border-purple-500/35 hover:text-foreground">组合体检</button>
          <button type="button" onClick={() => researchPortfolio('请分析当前基金组合的收益来源：哪些持仓贡献最大，是否依赖少数基金，并说明支持证据和反向证据。')} className="rounded-btn border border-border bg-surface px-2.5 py-1.5 text-[10px] text-secondary hover:border-purple-500/35 hover:text-foreground">收益归因</button>
          <button type="button" onClick={() => researchPortfolio('请扫描当前基金组合风险，重点关注集中度、回撤、波动、净值时效和证据不足，不要给出确定性收益承诺。')} className="rounded-btn border border-border bg-surface px-2.5 py-1.5 text-[10px] text-secondary hover:border-purple-500/35 hover:text-foreground">风险扫描</button>
          <button type="button" onClick={() => researchPortfolio('请逐只分析当前基金：结合我的成本盈亏和组合权重、最新定期报告披露的前十大持仓与资产配置、基金净值趋势和大盘趋势，给出继续持有观察、降低风险暴露、进入卖出评估或信息不足四档研判，并列明触发条件与失效条件。')} className="rounded-btn border border-purple-500/30 bg-purple-500/[0.06] px-2.5 py-1.5 text-[10px] text-purple-400 hover:bg-purple-500/10">持有/卖出研判</button>
          <span className="ml-auto text-[9px] text-muted">使用本地持仓快照与公开净值，不上传支付宝登录信息</span>
        </section>
      )}

      {summary.position_count === 0 ? (
        <section className="mt-4 flex min-h-[420px] items-center justify-center border-y border-border bg-surface/50 px-6 text-center">
          <div className="max-w-sm">
            <WalletCards className="mx-auto h-9 w-9 text-muted" />
            <h2 className="mt-3 text-sm font-semibold text-foreground">基金账本还是空的</h2>
            <p className="mt-2 text-xs leading-5 text-muted">导入支付宝持仓截图或 CSV，确认后即可查看收益和自动估值。</p>
            <div className="mt-5 flex justify-center gap-2">
              <button type="button" onClick={() => setImportOpen(true)} className="inline-flex items-center gap-1.5 rounded-btn bg-accent px-3 py-2 text-xs font-medium text-white hover:bg-accent/90"><Upload className="h-3.5 w-3.5" />同步持仓快照</button>
              <button type="button" onClick={openCreate} className="inline-flex items-center gap-1.5 rounded-btn border border-border bg-surface px-3 py-2 text-xs text-secondary hover:text-foreground"><Plus className="h-3.5 w-3.5" />手工添加</button>
            </div>
          </div>
        </section>
      ) : (
        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
          <section className="min-w-0 overflow-hidden rounded-card border border-border bg-surface">
            <div className="flex items-center justify-between border-b border-border px-4 py-3">
              <h2 className="text-xs font-semibold text-foreground">当前持仓</h2>
              <span className="text-[10px] text-muted">金额单位：人民币元</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1020px] text-left">
                <thead className="bg-elevated/60 text-[10px] font-medium text-muted">
                  <tr>
                    <th className="px-3 py-2.5">基金</th>
                    <th className="px-3 py-2.5 text-right">当前市值</th>
                    <th className="px-3 py-2.5 text-right">持仓成本</th>
                    <th className="px-3 py-2.5 text-right">持有收益</th>
                    <th className="px-3 py-2.5 text-right">单日收益</th>
                    <th className="px-3 py-2.5 text-right">估算净值</th>
                    <th className="px-3 py-2.5 text-right">份额</th>
                    <th className="w-20 px-3 py-2.5" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/60">
                  {sortedPositions.map(position => (
                    <tr key={position.code} className="text-xs hover:bg-elevated/35">
                      <td className="px-3 py-3">
                        <div className="max-w-[220px] truncate font-medium text-foreground">{position.name || `基金 ${position.code}`}</div>
                        <div className="mt-0.5 flex items-center gap-2 font-mono text-[10px] text-muted"><span>{position.code}</span><span>{position.quote_status === 'estimate' ? '估值' : position.quote_status === 'official' ? '净值' : '未刷新'}</span></div>
                      </td>
                      <td className="px-3 py-3 text-right font-mono font-semibold text-foreground">{money(position.market_value)}</td>
                      <td className="px-3 py-3 text-right font-mono text-secondary">{money(position.cost_amount)}</td>
                      <td className={`px-3 py-3 text-right font-mono font-semibold ${changeClass(position.holding_profit)}`}>
                        <div>{money(position.holding_profit, true)}</div>
                        <div className="mt-0.5 text-[10px]">{percent(position.holding_profit_pct)}</div>
                      </td>
                      <td className={`px-3 py-3 text-right font-mono ${changeClass(position.day_profit)}`}>
                        <div>{money(position.day_profit, true)}</div>
                        <div className="mt-0.5 text-[10px]">{percent(position.estimated_change_pct)} · {position.day_profit_estimated ? '估算' : '净值日'}</div>
                      </td>
                      <td className="px-3 py-3 text-right font-mono text-secondary">
                        <div>{numberText(position.estimated_nav ?? position.official_nav)}</div>
                        <div className="mt-0.5 text-[9px] text-muted">{position.quote_time || position.official_nav_date || '--'}</div>
                      </td>
                      <td className="px-3 py-3 text-right font-mono text-secondary">{numberText(position.shares)}</td>
                      <td className="px-3 py-3">
                        <div className="flex justify-end gap-1">
                          <button type="button" onClick={() => researchFund(position)} className="inline-flex h-7 w-7 items-center justify-center rounded-btn text-muted hover:bg-purple-500/10 hover:text-purple-400" title="AI 研究"><Bot className="h-3.5 w-3.5" /></button>
                          <button type="button" onClick={() => openEdit(position)} className="inline-flex h-7 w-7 items-center justify-center rounded-btn text-muted hover:bg-elevated hover:text-foreground" title="编辑持仓"><Edit3 className="h-3.5 w-3.5" /></button>
                          <button type="button" disabled={remove.isPending} onClick={() => deletePosition(position)} className="inline-flex h-7 w-7 items-center justify-center rounded-btn text-muted hover:bg-danger/10 hover:text-danger disabled:opacity-50" title="删除持仓"><Trash2 className="h-3.5 w-3.5" /></button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <aside className="rounded-card border border-border bg-surface p-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs font-semibold text-foreground">仓位分布</h2>
              <span className="text-[10px] text-muted">按当前市值</span>
            </div>
            <div className="mt-4 space-y-3">
              {sortedPositions.slice(0, 8).map((position, index) => {
                const ratio = allocationTotal > 0 ? Math.max(position.market_value ?? 0, 0) / allocationTotal * 100 : 0
                return (
                  <div key={position.code}>
                    <div className="flex items-center justify-between gap-3 text-[11px]">
                      <span className="min-w-0 truncate text-secondary">{position.name || position.code}</span>
                      <span className="shrink-0 font-mono text-foreground">{ratio.toFixed(1)}%</span>
                    </div>
                    <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-elevated">
                      <div className={`h-full rounded-full ${ALLOCATION_COLORS[index % ALLOCATION_COLORS.length]}`} style={{ width: `${Math.min(ratio, 100)}%` }} />
                    </div>
                  </div>
                )
              })}
            </div>
            <div className="mt-5 border-t border-border pt-4">
              <div className="flex items-start gap-2 text-[10px] leading-4 text-muted">
                {(summary.total_day_profit ?? 0) >= 0 ? <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-bull" /> : <ArrowDownRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-bear" />}
                盘中数据为公开估值推算；盘后回退到最近公布的正式净值。最终收益以基金公司净值和支付宝账单为准。
              </div>
            </div>
          </aside>
        </div>
      )}

      <FundImportDialog open={importOpen} onClose={() => setImportOpen(false)} />
      <FundPositionDialog open={editOpen} position={editing} onClose={() => setEditOpen(false)} />
    </div>
  )
}
