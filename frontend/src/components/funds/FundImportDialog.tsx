import { useEffect, useRef, useState } from 'react'
import { FileSpreadsheet, ImagePlus, Loader2, Plus, ShieldCheck, Trash2, Upload, X } from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Modal } from '@/components/Modal'
import { toast } from '@/components/Toast'
import { api, type FundImportPreview, type FundPositionInput } from '@/lib/api'
import { QK } from '@/lib/queryKeys'

interface Props {
  open: boolean
  onClose: () => void
}

type ImportMode = 'screenshot' | 'csv'
type NumericField = 'holding_amount' | 'shares' | 'cost_amount' | 'holding_profit' | 'holding_profit_pct' | 'day_profit'

const MAX_SCREENSHOTS = 10
const NUMERIC_FIELDS: Array<{ key: NumericField; label: string; step: string }> = [
  { key: 'holding_amount', label: '持有金额', step: '0.01' },
  { key: 'shares', label: '持有份额', step: '0.0001' },
  { key: 'cost_amount', label: '持仓成本', step: '0.01' },
  { key: 'holding_profit', label: '持有收益', step: '0.01' },
  { key: 'holding_profit_pct', label: '收益率 %', step: '0.01' },
  { key: 'day_profit', label: '昨日收益', step: '0.01' },
]

function mergeCandidates(groups: FundPositionInput[][]): FundPositionInput[] {
  const merged = new Map<string, FundPositionInput>()
  for (const group of groups) {
    for (const candidate of group) {
      const previous = merged.get(candidate.code)
      if (!previous) {
        merged.set(candidate.code, candidate)
        continue
      }
      merged.set(candidate.code, {
        ...previous,
        ...Object.fromEntries(
          Object.entries(candidate).filter(([, value]) => value !== null && value !== ''),
        ),
      } as FundPositionInput)
    }
  }
  return [...merged.values()]
}

export function FundImportDialog({ open, onClose }: Props) {
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const [mode, setMode] = useState<ImportMode>('screenshot')
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState('')
  const [provider, setProvider] = useState('')
  const [ocrAvailable, setOcrAvailable] = useState<boolean | null>(null)
  const [candidates, setCandidates] = useState<FundPositionInput[]>([])
  const [warnings, setWarnings] = useState<string[]>([])
  const [previewUrls, setPreviewUrls] = useState<string[]>([])

  const confirm = useMutation({
    mutationFn: () => api.fundImportConfirm(mode === 'csv' ? 'csv' : 'alipay_screenshot', candidates),
    onSuccess: portfolio => {
      queryClient.setQueryData(QK.fundPortfolio, portfolio)
      toast(`已同步 ${portfolio.summary.position_count} 只基金`, 'success')
      onClose()
    },
  })

  const reset = () => {
    abortRef.current?.abort()
    abortRef.current = null
    setBusy(false)
    setProgress('')
    setProvider('')
    setCandidates([])
    setWarnings([])
    setPreviewUrls(previous => {
      previous.forEach(URL.revokeObjectURL)
      return []
    })
    if (inputRef.current) inputRef.current.value = ''
  }

  useEffect(() => {
    if (!open) {
      reset()
      return
    }
    let cancelled = false
    void api.fundOcrStatus().then(
      result => { if (!cancelled) setOcrAvailable(result.available) },
      () => { if (!cancelled) setOcrAvailable(false) },
    )
    return () => { cancelled = true }
    // Reset is intentionally scoped to the dialog lifecycle.
  }, [open])

  useEffect(() => () => previewUrls.forEach(URL.revokeObjectURL), [previewUrls])

  if (!open) return null

  const setImportMode = (next: ImportMode) => {
    reset()
    setMode(next)
  }

  const runPreview = async (files: File[]) => {
    const queue = mode === 'csv' ? files.slice(0, 1) : files.slice(0, MAX_SCREENSHOTS)
    if (queue.length === 0) return
    if (mode === 'csv' && !/\.csv$/i.test(queue[0].name) && queue[0].type !== 'text/csv') {
      toast('请选择 CSV 文件', 'error')
      return
    }
    if (mode === 'screenshot' && queue.some(file => !file.type.startsWith('image/'))) {
      toast('截图模式仅支持图片文件', 'error')
      return
    }
    if (files.length > queue.length) {
      toast(mode === 'csv' ? 'CSV 模式一次导入一份文件' : `一次最多识别 ${MAX_SCREENSHOTS} 张截图`, 'error')
    }

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setBusy(true)
    setCandidates([])
    setWarnings([])
    setPreviewUrls(previous => {
      previous.forEach(URL.revokeObjectURL)
      return mode === 'screenshot' ? queue.map(URL.createObjectURL) : []
    })
    const results: FundImportPreview[] = []
    const errors: string[] = []
    try {
      for (let index = 0; index < queue.length; index += 1) {
        setProgress(queue.length > 1 ? `正在识别 ${index + 1}/${queue.length}` : '正在生成预览')
        try {
          results.push(await api.fundImportPreview(queue[index], controller.signal, true))
        } catch (error) {
          if (controller.signal.aborted) return
          errors.push(error instanceof Error ? error.message : '识别失败')
        }
      }
      const nextCandidates = mergeCandidates(results.map(result => result.candidates))
      setCandidates(nextCandidates)
      setProvider(results.at(-1)?.provider ?? '')
      setWarnings([...new Set([...results.flatMap(result => result.warnings), ...errors])])
      if (nextCandidates.length === 0) toast(errors.at(-1) ?? '没有识别到基金持仓', 'error')
    } finally {
      setBusy(false)
      setProgress('')
    }
  }

  const updateCandidate = (index: number, field: keyof FundPositionInput, raw: string) => {
    setCandidates(previous => previous.map((candidate, rowIndex) => {
      if (rowIndex !== index) return candidate
      const value = NUMERIC_FIELDS.some(item => item.key === field)
        ? raw === '' ? null : Number(raw)
        : raw
      return { ...candidate, [field]: value }
    }))
  }

  const invalidRows = candidates.filter(candidate => (
    !/^\d{6}$/.test(candidate.code)
    || (candidate.holding_amount == null && candidate.shares == null)
  ))

  return (
    <Modal
      onClose={onClose}
      labelledBy="fund-import-title"
      panelClassName="flex max-h-[90vh] w-[96vw] max-w-6xl flex-col rounded-card border border-border bg-surface shadow-xl"
    >
      <div className="flex shrink-0 items-start justify-between gap-4 border-b border-border px-4 py-3">
        <div>
          <h2 id="fund-import-title" className="text-sm font-semibold text-foreground">同步支付宝基金快照</h2>
          <div className="mt-1 flex items-center gap-1.5 text-[11px] text-muted">
            <ShieldCheck className="h-3.5 w-3.5 text-bear" />
            文件只发送到本机服务识别，不连接支付宝账户，也不保存账号密码
          </div>
        </div>
        <button type="button" onClick={onClose} className="inline-flex h-8 w-8 items-center justify-center rounded-btn text-secondary hover:bg-elevated" title="关闭">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-3">
        <div className="inline-flex w-fit rounded-btn border border-border bg-elevated p-0.5">
          <button type="button" onClick={() => setImportMode('screenshot')} className={`inline-flex items-center gap-1.5 rounded-btn px-3 py-1.5 text-xs ${mode === 'screenshot' ? 'bg-surface text-foreground shadow-sm' : 'text-muted hover:text-foreground'}`}>
            <ImagePlus className="h-3.5 w-3.5" />截图
          </button>
          <button type="button" onClick={() => setImportMode('csv')} className={`inline-flex items-center gap-1.5 rounded-btn px-3 py-1.5 text-xs ${mode === 'csv' ? 'bg-surface text-foreground shadow-sm' : 'text-muted hover:text-foreground'}`}>
            <FileSpreadsheet className="h-3.5 w-3.5" />CSV
          </button>
        </div>

        <input
          ref={inputRef}
          type="file"
          multiple={mode === 'screenshot'}
          accept={mode === 'screenshot' ? 'image/jpeg,image/png,image/webp,image/bmp,image/gif' : '.csv,text/csv'}
          className="hidden"
          onChange={event => {
            void runPreview(Array.from(event.target.files ?? []))
            event.target.value = ''
          }}
        />

        <button
          type="button"
          disabled={busy || (mode === 'screenshot' && ocrAvailable === false)}
          onClick={() => inputRef.current?.click()}
          onDragOver={event => event.preventDefault()}
          onDrop={event => {
            event.preventDefault()
            void runPreview(Array.from(event.dataTransfer.files))
          }}
          className="mt-3 flex min-h-24 w-full flex-col items-center justify-center gap-2 rounded-btn border border-dashed border-border bg-elevated/35 px-4 py-4 text-xs text-secondary transition-colors hover:bg-elevated/60 disabled:cursor-not-allowed disabled:opacity-55"
        >
          {busy ? <Loader2 className="h-6 w-6 animate-spin text-accent" /> : mode === 'screenshot' ? <ImagePlus className="h-6 w-6 text-accent" /> : <FileSpreadsheet className="h-6 w-6 text-bear" />}
          <span>{progress || (mode === 'screenshot' ? (ocrAvailable === false ? '本机 OCR 不可用，请改用 CSV' : '选择或拖入支付宝基金持仓截图') : '选择或拖入基金持仓 CSV')}</span>
          <span className="text-[10px] text-muted">{mode === 'screenshot' ? '支持多张，单次最多 10 张' : '表头支持基金代码、名称、持有金额、份额、成本和收益'}</span>
        </button>

        {previewUrls.length > 0 && (
          <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
            {previewUrls.map((url, index) => (
              <img key={url} src={url} alt={`截图 ${index + 1}`} className="h-20 w-20 shrink-0 rounded-btn border border-border bg-base object-contain" />
            ))}
          </div>
        )}

        {warnings.length > 0 && (
          <div className="mt-3 border-l-2 border-warning bg-warning/5 px-3 py-2 text-[11px] leading-5 text-secondary">
            {warnings.map(warning => <div key={warning}>{warning}</div>)}
          </div>
        )}

        {candidates.length > 0 && (
          <div className="mt-3 min-w-0 overflow-x-auto rounded-btn border border-border">
            <table className="w-full min-w-[1080px] text-left text-xs">
              <thead className="bg-elevated/70 text-[10px] font-medium text-muted">
                <tr>
                  <th className="px-2.5 py-2">基金代码</th>
                  <th className="px-2.5 py-2">基金名称</th>
                  {NUMERIC_FIELDS.map(field => <th key={field.key} className="px-2.5 py-2">{field.label}</th>)}
                  <th className="w-10 px-2 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {candidates.map((candidate, index) => (
                  <tr key={`${candidate.code}-${index}`} className="bg-surface hover:bg-elevated/30">
                    <td className="p-1.5"><input value={candidate.code} onChange={event => updateCandidate(index, 'code', event.target.value.replace(/\D/g, '').slice(0, 6))} className="w-24 rounded-btn border border-border bg-base px-2 py-1.5 font-mono outline-none focus:border-accent" /></td>
                    <td className="p-1.5"><input value={candidate.name} onChange={event => updateCandidate(index, 'name', event.target.value)} className="w-48 rounded-btn border border-border bg-base px-2 py-1.5 outline-none focus:border-accent" /></td>
                    {NUMERIC_FIELDS.map(field => (
                      <td key={field.key} className="p-1.5">
                        <input type="number" step={field.step} value={candidate[field.key] ?? ''} onChange={event => updateCandidate(index, field.key, event.target.value)} className="w-28 rounded-btn border border-border bg-base px-2 py-1.5 text-right font-mono outline-none focus:border-accent" />
                      </td>
                    ))}
                    <td className="p-1.5 text-center">
                      <button type="button" onClick={() => setCandidates(previous => previous.filter((_, rowIndex) => rowIndex !== index))} className="inline-flex h-7 w-7 items-center justify-center rounded-btn text-muted hover:bg-danger/10 hover:text-danger" title="移除">
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {candidates.length > 0 && (
          <button type="button" onClick={() => setCandidates(previous => [...previous, { code: '', name: '', holding_amount: null, shares: null, cost_amount: null, holding_profit: null, holding_profit_pct: null, day_profit: null }])} className="mt-2 inline-flex w-fit items-center gap-1 text-[11px] text-accent hover:underline">
            <Plus className="h-3.5 w-3.5" />补充一只基金
          </button>
        )}
      </div>

      <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-border px-4 py-3">
        <span className="text-[11px] text-muted">
          {candidates.length > 0 ? `确认后将用这 ${candidates.length} 条持仓覆盖当前快照${provider ? ` · ${provider}` : ''}` : '先上传文件生成可编辑预览'}
        </span>
        <div className="flex items-center gap-2">
          <button type="button" onClick={onClose} className="rounded-btn border border-border px-3 py-1.5 text-xs text-secondary hover:bg-elevated">取消</button>
          <button
            type="button"
            disabled={candidates.length === 0 || invalidRows.length > 0 || confirm.isPending}
            onClick={() => confirm.mutate()}
            className="inline-flex items-center gap-1.5 rounded-btn bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
            title={invalidRows.length > 0 ? '请补全基金代码以及持有金额或份额' : '确认同步'}
          >
            {confirm.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
            确认同步 {candidates.length || ''}
          </button>
        </div>
      </div>
    </Modal>
  )
}
