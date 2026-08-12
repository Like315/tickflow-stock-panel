import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, Loader2, Save, X } from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Modal } from '@/components/Modal'
import { toast } from '@/components/Toast'
import { api, type FundPosition, type FundPositionInput } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

interface Props {
  open: boolean
  position?: FundPosition | null
  onClose: () => void
}

const EMPTY: FundPositionInput = {
  code: '',
  name: '',
  holding_amount: null,
  shares: null,
  cost_amount: null,
  holding_profit: null,
  holding_profit_pct: null,
  day_profit: null,
}

const FIELDS: Array<{ key: keyof Omit<FundPositionInput, 'code' | 'name'>; label: string; hint: string; step: string }> = [
  { key: 'holding_amount', label: '持有金额', hint: '没有份额时用于计算当前市值', step: '0.01' },
  { key: 'shares', label: '持有份额', hint: '刷新估值后优先按份额计算市值', step: '0.0001' },
  { key: 'cost_amount', label: '持仓成本', hint: '用于计算持有收益和收益率', step: '0.01' },
  { key: 'holding_profit', label: '持有收益', hint: '没有成本时保留此导入值', step: '0.01' },
  { key: 'holding_profit_pct', label: '持有收益率 %', hint: '按百分数填写，例如 12.5', step: '0.01' },
  { key: 'day_profit', label: '昨日收益', hint: '行情刷新后会显示当日估算值', step: '0.01' },
]

export function FundPositionDialog({ open, position, onClose }: Props) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<FundPositionInput>(EMPTY)
  const [lookupStatus, setLookupStatus] = useState<'idle' | 'loading' | 'found' | 'missing'>('idle')
  const lastAutoName = useRef('')

  useEffect(() => {
    if (!open) return
    lastAutoName.current = ''
    setLookupStatus('idle')
    setForm(position ? {
      code: position.code,
      name: position.name,
      holding_amount: position.holding_amount,
      shares: position.shares,
      cost_amount: position.cost_amount,
      holding_profit: position.holding_profit,
      holding_profit_pct: position.holding_profit_pct,
      day_profit: position.day_profit,
    } : EMPTY)
  }, [open, position])

  useEffect(() => {
    if (!open || position || !/^\d{6}$/.test(form.code)) {
      setLookupStatus('idle')
      return
    }
    let cancelled = false
    const code = form.code
    setLookupStatus('loading')
    const timer = window.setTimeout(() => {
      void api.fundLookup(code).then(result => {
        if (cancelled) return
        setForm(previous => {
          if (previous.code !== code) return previous
          const canAutofill = !previous.name.trim() || previous.name === lastAutoName.current
          if (!canAutofill) return previous
          lastAutoName.current = result.name
          return { ...previous, name: result.name }
        })
        setLookupStatus('found')
      }).catch(() => {
        if (!cancelled) setLookupStatus('missing')
      })
    }, 350)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [form.code, open, position])

  const save = useMutation({
    mutationFn: () => {
      const { code, ...values } = form
      return api.fundUpsertPosition(code, values)
    },
    onSuccess: portfolio => {
      queryClient.setQueryData(QK.fundPortfolio, portfolio)
      toast(position ? '基金持仓已更新' : '基金持仓已添加', 'success')
      onClose()
    },
  })

  if (!open) return null

  const valid = /^\d{6}$/.test(form.code) && (form.holding_amount != null || form.shares != null)

  return (
    <Modal onClose={onClose} labelledBy="fund-position-title" panelClassName="w-[92vw] max-w-xl rounded-card border border-border bg-surface shadow-xl">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 id="fund-position-title" className="text-sm font-semibold text-foreground">{position ? '编辑基金持仓' : '手工添加基金'}</h2>
        <button type="button" onClick={onClose} className="inline-flex h-8 w-8 items-center justify-center rounded-btn text-secondary hover:bg-elevated" title="关闭"><X className="h-4 w-4" /></button>
      </div>
      <div className="grid gap-3 px-4 py-4 sm:grid-cols-2">
        <label className="block text-[11px] text-muted">
          基金代码
          <input autoFocus disabled={!!position} value={form.code} onChange={event => {
            const code = event.target.value.replace(/\D/g, '').slice(0, 6)
            setForm(previous => ({
              ...previous,
              code,
              name: previous.name === lastAutoName.current ? '' : previous.name,
            }))
          }} placeholder="6 位基金代码" className="mt-1 w-full rounded-btn border border-border bg-base px-3 py-2 font-mono text-xs text-foreground outline-none focus:border-accent disabled:opacity-60" />
          {!position && lookupStatus === 'loading' && <span className="mt-1 flex items-center gap-1 text-[10px] text-muted"><Loader2 className="h-3 w-3 animate-spin" />正在查询基金名称</span>}
          {!position && lookupStatus === 'missing' && <span className="mt-1 block text-[10px] text-warning">未查询到名称，可手工填写后保存</span>}
        </label>
        <label className="block text-[11px] text-muted">
          <span className="flex items-center gap-1">基金名称{lookupStatus === 'found' && <CheckCircle2 className="h-3 w-3 text-bear" />}</span>
          <input value={form.name} onChange={event => {
            lastAutoName.current = ''
            setForm(previous => ({ ...previous, name: event.target.value }))
          }} placeholder="输入代码后自动查询" className="mt-1 w-full rounded-btn border border-border bg-base px-3 py-2 text-xs text-foreground outline-none focus:border-accent" />
        </label>
        {FIELDS.map(field => (
          <label key={field.key} className="block text-[11px] text-muted">
            {field.label}
            <input type="number" step={field.step} value={form[field.key] ?? ''} onChange={event => setForm(previous => ({ ...previous, [field.key]: event.target.value === '' ? null : Number(event.target.value) }))} className="mt-1 w-full rounded-btn border border-border bg-base px-3 py-2 text-right font-mono text-xs text-foreground outline-none focus:border-accent" />
            <span className="mt-1 block text-[10px] text-muted/80">{field.hint}</span>
          </label>
        ))}
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
        <button type="button" onClick={onClose} className="rounded-btn border border-border px-3 py-1.5 text-xs text-secondary hover:bg-elevated">取消</button>
        <button type="button" disabled={!valid || save.isPending} onClick={() => save.mutate()} className="inline-flex items-center gap-1.5 rounded-btn bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/90 disabled:opacity-50">
          {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
          保存
        </button>
      </div>
    </Modal>
  )
}
