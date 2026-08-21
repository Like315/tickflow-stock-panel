import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { Modal } from '@/components/Modal'
import {
  api,
  type InvestmentExpertPortfolioSyncPosition,
  type InvestmentExpertPortfolioSyncPreview,
} from '@/lib/api'
import { investmentMoney as money, investmentPrice as price } from '@/lib/investmentExpertFormat'
import { QK } from '@/lib/queryKeys'

type BlockedReason = NonNullable<InvestmentExpertPortfolioSyncPreview['blocked_reason']>

const BLOCKED_LABELS: Record<BlockedReason, string> = {
  runtime_running: '请先停止 AI 投资专家盯盘，再同步持仓。',
  background_task_running: '后台任务进行中，请完成后再同步持仓。',
  source_portfolio_empty: '股票持仓为空，请先在“持股”页面录入持仓。',
  invalid_source_positions: '股票持仓包含无法同步的数据，请先修正。',
  stock_portfolio_service_unavailable: '股票持仓服务尚未初始化。',
}

interface PortfolioSyncDialogProps {
  onClose: () => void
  onSynced: () => void
}

/** 展示单项同步预检指标。 */
function SyncMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-base/50 px-3 py-2.5">
      <div className="text-[10px] text-muted">{label}</div>
      <div className="mt-1 text-sm font-medium tabular-nums text-foreground">{value}</div>
    </div>
  )
}

/** 展示预检统计与同步后的现金输入。 */
function SyncSummary({
  preview,
  availableCash,
  onAvailableCashChange,
}: {
  preview: InvestmentExpertPortfolioSyncPreview
  availableCash: string
  onAvailableCashChange: (value: string) => void
}) {
  const cashNumber = availableCash.trim() === '' ? 0 : Number(availableCash)
  const projectedEquity = Number.isFinite(cashNumber) && cashNumber >= 0
    ? cashNumber + (preview.source_total_market_value ?? 0)
    : null
  return <>
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <SyncMetric label="来源持仓" value={`${preview.position_count ?? 0} 只`} />
      <SyncMetric label="将替换 AI 持仓" value={`${preview.replace_position_count ?? 0} 个持仓批次`} />
      <SyncMetric label="持仓市值" value={money(preview.source_total_market_value)} />
      <SyncMetric label="当前 AI 现金" value={money(preview.current_available_cash)} />
    </div>
    <label className="block rounded-xl border border-blue-400/20 bg-blue-400/5 px-4 py-3">
      <span className="text-xs font-medium text-blue-100">同步后的可用现金</span>
      <div className="mt-2 flex items-center gap-2">
        <span className="text-sm text-muted">¥</span>
        <input type="number" min="0" step="0.01" value={availableCash} placeholder="留空表示 0"
          onChange={event => onAvailableCashChange(event.target.value)}
          className="min-w-0 flex-1 rounded-lg border border-border bg-base px-3 py-2 text-sm tabular-nums text-foreground outline-none focus:border-blue-400/50" />
      </div>
      <span className="mt-2 block text-[11px] text-muted">
        留空表示无可用资金。同步后账户总权益：{projectedEquity == null ? '--' : money(projectedEquity)}（持仓市值 + 可用现金）
      </span>
    </label>
  </>
}

/** 展示预检阻断原因、错误和降级警告。 */
function SyncMessages({ preview }: { preview: InvestmentExpertPortfolioSyncPreview }) {
  return <>
    {preview.blocked_reason && (
      <div className="rounded-lg border border-amber-300/20 bg-amber-300/5 px-3 py-2 text-xs text-amber-200">
        {BLOCKED_LABELS[preview.blocked_reason] ?? preview.blocked_reason}
      </div>
    )}
    {preview.errors.map(error => (
      <div key={error} className="rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-2 text-xs text-rose-300">{error}</div>
    ))}
    {preview.warnings.map(warning => (
      <div key={warning} className="rounded-lg border border-amber-300/20 bg-amber-300/5 px-3 py-2 text-xs text-amber-200">{warning}</div>
    ))}
  </>
}

/** 展示一行可同步持仓。 */
function PositionRow({ position }: { position: InvestmentExpertPortfolioSyncPosition }) {
  return (
    <tr className="border-t border-border/70">
      <td className="px-3 py-2">
        <div className="font-medium text-foreground">{position.name || position.symbol}</div>
        <div className="font-mono text-[10px] text-muted">{position.symbol}</div>
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{position.quantity.toLocaleString('zh-CN')}</td>
      <td className="px-3 py-2 text-right tabular-nums">{price(position.entry_price)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{price(position.current_price)}</td>
      <td className="px-3 py-2 text-right tabular-nums">{money(position.market_value)}</td>
      <td className="px-3 py-2 text-right text-muted">{position.acquired_date}</td>
    </tr>
  )
}

/** 展示全部可同步持仓。 */
function PositionTable({ positions }: { positions: InvestmentExpertPortfolioSyncPosition[] }) {
  if (positions.length === 0) return null
  const headers = ['股票', '数量', '成本价', '最新价', '市值', '持仓日期']
  return (
    <div className="overflow-hidden rounded-xl border border-border">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[620px] text-left text-xs">
          <thead className="bg-base/70 text-muted"><tr>
            {headers.map((header, index) => (
              <th key={header} className={`px-3 py-2 font-medium ${index > 0 ? 'text-right' : ''}`}>{header}</th>
            ))}
          </tr></thead>
          <tbody>{positions.map(position => <PositionRow key={position.symbol} position={position} />)}</tbody>
        </table>
      </div>
    </div>
  )
}

/** 管理股票持仓同步的预检、确认和错误状态。 */
export function PortfolioSyncDialog({ onClose, onSynced }: PortfolioSyncDialogProps) {
  const [availableCash, setAvailableCash] = useState('')
  const preview = useQuery({
    queryKey: QK.investmentExpertPortfolioSyncPreview,
    queryFn: api.investmentExpertPortfolioSyncPreview,
    refetchOnMount: 'always',
  })
  const sync = useMutation({
    mutationFn: api.investmentExpertPortfolioSync,
    onSuccess: () => { onSynced(); onClose() },
  })
  const cashNumber = availableCash.trim() === '' ? 0 : Number(availableCash)
  const cashIsValid = Number.isFinite(cashNumber) && cashNumber >= 0
  return (
    <Modal onClose={() => !sync.isPending && onClose()} labelledBy="investment-expert-portfolio-sync-title"
      panelClassName="w-[94vw] max-w-3xl rounded-2xl border border-border bg-surface shadow-2xl"
      closeOnBackdrop={!sync.isPending}>
      <header className="border-b border-border px-5 py-4">
        <h2 id="investment-expert-portfolio-sync-title" className="text-base font-semibold text-foreground">同步股票持仓到 AI 投资专家</h2>
        <p className="mt-1 text-xs leading-5 text-muted">股票持仓将覆盖 AI 当前持仓；可用现金留空时按 0 处理。旧挂单会清空，并从“持仓市值 + 可用现金”建立新风控基线。</p>
      </header>
      <div className="max-h-[65vh] space-y-4 overflow-y-auto px-5 py-4">
        {preview.isPending && <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted"><Loader2 className="h-4 w-4 animate-spin" />正在读取股票持仓…</div>}
        {preview.error && <div className="rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-2 text-xs text-rose-300">读取失败：{preview.error instanceof Error ? preview.error.message : String(preview.error)}</div>}
        {preview.data && <><SyncSummary preview={preview.data} availableCash={availableCash} onAvailableCashChange={setAvailableCash} /><SyncMessages preview={preview.data} /><PositionTable positions={preview.data.positions} /></>}
        {sync.error && <div className="rounded-lg border border-rose-400/20 bg-rose-400/5 px-3 py-2 text-xs text-rose-300">同步失败：{sync.error instanceof Error ? sync.error.message : String(sync.error)}</div>}
      </div>
      <footer className="flex items-center justify-end gap-2 border-t border-border px-5 py-4">
        <button type="button" onClick={onClose} disabled={sync.isPending} className="rounded-btn border border-border px-4 py-2 text-xs text-secondary hover:bg-elevated disabled:opacity-50">取消</button>
        <button type="button" onClick={() => sync.mutate(cashNumber)}
          disabled={!preview.data?.can_sync || !cashIsValid || sync.isPending}
          className="inline-flex items-center gap-1.5 rounded-btn bg-blue-500 px-4 py-2 text-xs font-medium text-white hover:bg-blue-400 disabled:cursor-not-allowed disabled:opacity-45">
          {sync.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}确认覆盖并同步
        </button>
      </footer>
    </Modal>
  )
}
