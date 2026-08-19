import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BriefcaseBusiness,
  CalendarDays,
  Edit3,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
  TrendingUp,
} from 'lucide-react'
import { StockPositionDialog } from '@/components/holdings/StockPositionDialog'
import { toast } from '@/components/Toast'
import { api, type StockPosition } from '@/lib/api'
import { fmtPct } from '@/lib/format'
import { QK } from '@/lib/queryKeys'

function finite(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function money(value: number | null | undefined, signed = false): string {
  const number = finite(value)
  if (number == null) return '--'
  const prefix = signed && number > 0 ? '+' : ''
  return `${prefix}${number.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function price(value: number | null | undefined): string {
  const number = finite(value)
  if (number == null) return '--'
  return number.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 4 })
}

function quantity(value: number | null | undefined): string {
  const number = finite(value)
  if (number == null) return '--'
  return number.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}

function changeClass(value: number | null | undefined): string {
  const number = finite(value)
  if (number == null || number === 0) return 'text-foreground'
  return number > 0 ? 'text-bull' : 'text-bear'
}

function SummaryMetric({ label, value, detail, tone = 'text-foreground' }: {
  label: string
  value: string
  detail: string
  tone?: string
}) {
  return (
    <div className="min-w-0 px-4 py-3 md:px-5">
      <div className="text-[10px] text-muted">{label}</div>
      <div className={`mt-1 truncate font-mono text-xl font-semibold ${tone}`}>{value}</div>
      <div className="mt-1 truncate text-[10px] text-muted">{detail}</div>
    </div>
  )
}

export function StockPortfolio() {
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingPosition, setEditingPosition] = useState<StockPosition | null>(null)

  const portfolio = useQuery({
    queryKey: QK.stockPortfolio,
    queryFn: api.stockPortfolio,
    staleTime: 20_000,
  })

  const remove = useMutation({
    mutationFn: api.stockPortfolioDelete,
    onSuccess: data => {
      queryClient.setQueryData(QK.stockPortfolio, data)
      toast('持仓已删除', 'success')
    },
  })

  const openCreateDialog = () => {
    setEditingPosition(null)
    setDialogOpen(true)
  }

  const openEditDialog = (position: StockPosition) => {
    setEditingPosition(position)
    setDialogOpen(true)
  }

  const closeDialog = () => {
    setDialogOpen(false)
    setEditingPosition(null)
  }

  const deletePosition = (position: StockPosition) => {
    if (!window.confirm(`确认删除 ${position.name || position.symbol} 的本地持仓？`)) return
    remove.mutate(position.symbol)
  }

  const sortedPositions = useMemo(
    () => [...(portfolio.data?.positions ?? [])].sort((a, b) => (b.market_value ?? -1) - (a.market_value ?? -1)),
    [portfolio.data?.positions],
  )

  if (portfolio.isLoading) {
    return (
      <div className="flex min-h-[70vh] items-center justify-center text-sm text-muted">
        <Loader2 className="mr-2 h-4 w-4 animate-spin text-accent" />正在读取持股账本
      </div>
    )
  }

  if (portfolio.isError || !portfolio.data) {
    return (
      <div className="mx-auto flex min-h-[70vh] max-w-lg items-center px-6">
        <div className="w-full rounded-card border border-danger/30 bg-surface p-7 text-center">
          <BriefcaseBusiness className="mx-auto h-8 w-8 text-danger" />
          <h1 className="mt-3 text-base font-semibold text-foreground">持股账本暂时无法读取</h1>
          <p className="mt-2 text-xs text-muted">{portfolio.error instanceof Error ? portfolio.error.message : '请稍后重试'}</p>
          <button type="button" onClick={() => portfolio.refetch()} className="mt-5 inline-flex items-center gap-1.5 rounded-btn border border-border bg-elevated px-3 py-1.5 text-xs text-foreground hover:border-accent/50">
            <RefreshCw className="h-3.5 w-3.5" />重试
          </button>
        </div>
      </div>
    )
  }

  const data = portfolio.data
  const summary = data.summary

  return (
    <div className="mx-auto max-w-[1600px] p-4 md:p-5">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-border pb-4">
        <div className="flex items-start gap-3">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-card border border-accent/30 bg-accent/10 text-accent">
            <BriefcaseBusiness className="h-4.5 w-4.5" />
          </span>
          <div>
            <h1 className="text-base font-semibold text-foreground">持股</h1>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted">
              <span>{summary.position_count} 只股票</span>
              <span className="inline-flex items-center gap-1"><CalendarDays className="h-3 w-3" />行情日期 {data.price_date || '--'}</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={openCreateDialog} className="inline-flex items-center gap-1.5 rounded-btn bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/90">
            <Plus className="h-3.5 w-3.5" />添加持股
          </button>
          <button type="button" disabled={portfolio.isFetching} onClick={() => portfolio.refetch()} className="inline-flex items-center gap-1.5 rounded-btn border border-border bg-surface px-3 py-1.5 text-xs text-secondary hover:border-accent/40 hover:text-foreground disabled:opacity-50">
            <RefreshCw className={`h-3.5 w-3.5 ${portfolio.isFetching ? 'animate-spin' : ''}`} />刷新行情
          </button>
        </div>
      </header>

      <section className="mt-4 grid overflow-hidden rounded-card border border-border bg-surface sm:grid-cols-2 xl:grid-cols-4 sm:[&>*:nth-child(2n)]:border-l xl:[&>*+*]:border-l">
        <SummaryMetric label="总市值（CNY）" value={`¥${money(summary.total_market_value)}`} detail={summary.total_market_value == null && summary.position_count > 0 ? '部分持仓暂无未复权行情' : '按最新未复权价格计算'} />
        <SummaryMetric label="持仓成本" value={`¥${money(summary.total_cost_amount)}`} detail={`${summary.position_count} 只股票`} />
        <SummaryMetric label="持有盈亏" value={`¥${money(summary.total_profit_amount, true)}`} detail={fmtPct(summary.profit_pct)} tone={changeClass(summary.total_profit_amount)} />
        <SummaryMetric label="行情状态" value={portfolio.isFetching ? '刷新中' : data.price_date ? '已更新' : '暂无行情'} detail={data.price_date ? `最新交易日 ${data.price_date}` : '请先同步日线或开启实时行情'} tone={data.price_date ? 'text-bull' : 'text-muted'} />
      </section>

      {summary.position_count === 0 ? (
        <section className="mt-4 flex min-h-[340px] items-center justify-center rounded-card border border-dashed border-border bg-surface/45 px-6 text-center">
          <div className="max-w-sm">
            <BriefcaseBusiness className="mx-auto h-9 w-9 text-muted" />
            <h2 className="mt-3 text-sm font-semibold text-foreground">还没有持股记录</h2>
            <p className="mt-2 text-xs leading-5 text-muted">搜索股票或导入持仓截图，填写买入数量和成本价后，即可自动生成持仓成本、市值与盈亏。</p>
            <button type="button" onClick={openCreateDialog} className="mt-4 inline-flex items-center gap-1.5 rounded-btn bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/90">
              <Plus className="h-3.5 w-3.5" />添加第一只持股
            </button>
          </div>
        </section>
      ) : (
        <section className="mt-4 overflow-hidden rounded-card border border-border bg-surface">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
            <h2 className="flex items-center gap-1.5 text-xs font-semibold text-foreground"><TrendingUp className="h-3.5 w-3.5 text-accent" />当前持仓</h2>
            <span className="text-[10px] text-muted">买入价与最新价均为未复权价格</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[940px] text-left">
              <thead className="bg-elevated/60 text-[10px] font-medium text-muted">
                <tr>
                  <th className="px-3 py-2.5">股票</th>
                  <th className="px-3 py-2.5 text-right">买入价</th>
                  <th className="px-3 py-2.5 text-right">最新价</th>
                  <th className="px-3 py-2.5 text-right">数量</th>
                  <th className="px-3 py-2.5 text-right">持仓成本</th>
                  <th className="px-3 py-2.5 text-right">当前市值</th>
                  <th className="px-3 py-2.5 text-right">持有盈亏</th>
                  <th className="w-20 px-3 py-2.5" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {sortedPositions.map(position => (
                  <tr key={position.symbol} className="text-xs hover:bg-elevated/35">
                    <td className="px-3 py-3">
                      <div className="max-w-[220px] truncate font-medium text-foreground">{position.name || position.symbol}</div>
                      <div className="mt-0.5 font-mono text-[10px] text-muted">{position.symbol}</div>
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-secondary">{price(position.buy_price)}</td>
                    <td className={`px-3 py-3 text-right font-mono font-semibold ${changeClass(position.change_pct)}`}>
                      <div>{price(position.current_price)}</div>
                      <div className="mt-0.5 text-[10px]">{fmtPct(position.change_pct)}</div>
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-secondary">{quantity(position.quantity)}</td>
                    <td className="px-3 py-3 text-right font-mono text-secondary">{money(position.cost_amount)}</td>
                    <td className="px-3 py-3 text-right font-mono font-semibold text-foreground">{money(position.market_value)}</td>
                    <td className={`px-3 py-3 text-right font-mono font-semibold ${changeClass(position.profit_amount)}`}>
                      <div>{money(position.profit_amount, true)}</div>
                      <div className="mt-0.5 text-[10px]">{fmtPct(position.profit_pct)}</div>
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex justify-end gap-1">
                        <button type="button" onClick={() => openEditDialog(position)} className="inline-flex h-7 w-7 items-center justify-center rounded-btn text-muted hover:bg-elevated hover:text-foreground" title="编辑持仓"><Edit3 className="h-3.5 w-3.5" /></button>
                        <button type="button" disabled={remove.isPending} onClick={() => deletePosition(position)} className="inline-flex h-7 w-7 items-center justify-center rounded-btn text-muted hover:bg-danger/10 hover:text-danger disabled:opacity-50" title="删除持仓"><Trash2 className="h-3.5 w-3.5" /></button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {dialogOpen && (
        <StockPositionDialog
          position={editingPosition}
          positions={data.positions}
          onClose={closeDialog}
        />
      )}
    </div>
  )
}
