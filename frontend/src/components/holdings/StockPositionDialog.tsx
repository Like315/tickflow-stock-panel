import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Calculator,
  CheckCircle2,
  ImagePlus,
  Loader2,
  Save,
  Search,
  X,
} from 'lucide-react'
import { Modal } from '@/components/Modal'
import { toast } from '@/components/Toast'
import {
  api,
  type StockPortfolioImportCandidate,
  type StockPosition,
  type StockPositionInput,
} from '@/lib/api'
import { getOcrInstallHint } from '@/lib/ocrInstallHint'
import { QK } from '@/lib/queryKeys'

interface InstrumentOption {
  symbol: string
  name: string
  code: string
}

interface StockPositionDialogProps {
  position?: StockPosition | null
  positions: StockPosition[]
  onClose: () => void
}

function numberText(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : ''
}

function money(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return '--'
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function StockPositionDialog({ position, positions, onClose }: StockPositionDialogProps) {
  const queryClient = useQueryClient()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [mode, setMode] = useState<'manual' | 'image'>('manual')
  const [searchText, setSearchText] = useState(position ? `${position.name} ${position.symbol}` : '')
  const [searchTerm, setSearchTerm] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [highlighted, setHighlighted] = useState(0)
  const [selected, setSelected] = useState<InstrumentOption | null>(position ? {
    symbol: position.symbol,
    name: position.name,
    code: position.symbol.split('.')[0],
  } : null)
  const [shareQuantity, setShareQuantity] = useState(numberText(position?.quantity))
  const [costPrice, setCostPrice] = useState(numberText(position?.buy_price))
  const [previewUrl, setPreviewUrl] = useState('')
  const [candidates, setCandidates] = useState<StockPortfolioImportCandidate[]>([])
  const [warnings, setWarnings] = useState<string[]>([])

  useEffect(() => {
    const timer = window.setTimeout(() => setSearchTerm(searchText.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [searchText])

  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl)
  }, [previewUrl])

  const search = useQuery({
    queryKey: QK.instrumentSearch(searchTerm, 'stock'),
    queryFn: () => api.instrumentSearch(searchTerm, 12, 'stock'),
    enabled: !position && mode === 'manual' && searchOpen && searchTerm.length > 0,
    staleTime: 60_000,
  })
  const results = search.data?.results ?? []

  useEffect(() => setHighlighted(0), [searchTerm, results.length])

  const ocrStatus = useQuery({
    queryKey: QK.stockPortfolioOcrStatus,
    queryFn: api.stockPortfolioOcrStatus,
    enabled: !position && mode === 'image',
    staleTime: 60_000,
    retry: false,
  })

  const save = useMutation({
    mutationFn: ({ symbol, input }: { symbol: string; input: StockPositionInput }) =>
      api.stockPortfolioUpsert(symbol, input),
    onSuccess: data => {
      queryClient.setQueryData(QK.stockPortfolio, data)
      toast(position ? '持仓已更新' : '持仓已添加', 'success')
      onClose()
    },
  })

  const imagePreview = useMutation({
    mutationFn: (file: File) => api.stockPortfolioImportPreview(file),
    onSuccess: result => {
      setCandidates(result.candidates)
      setWarnings(result.warnings)
      if (result.candidates.length === 1) selectImportCandidate(result.candidates[0])
    },
  })

  const selectInstrument = (instrument: InstrumentOption) => {
    setSelected(instrument)
    setSearchText(`${instrument.name} ${instrument.symbol}`)
    setSearchOpen(false)
    const existing = positions.find(row => row.symbol === instrument.symbol)
    setShareQuantity(numberText(existing?.quantity))
    setCostPrice(numberText(existing?.buy_price))
  }

  function selectImportCandidate(candidate: StockPortfolioImportCandidate) {
    setSelected({ symbol: candidate.symbol, name: candidate.name, code: candidate.code })
    setShareQuantity(numberText(candidate.quantity))
    setCostPrice(numberText(candidate.buy_price))
  }

  const handleSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!searchOpen || results.length === 0) return
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setHighlighted(index => Math.min(index + 1, results.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setHighlighted(index => Math.max(index - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const instrument = results[highlighted]
      if (instrument) selectInstrument(instrument)
    } else if (event.key === 'Escape') {
      setSearchOpen(false)
    }
  }

  const runImagePreview = (file: File | undefined) => {
    if (!file) return
    if (!file.type.startsWith('image/') && !/\.(jpe?g|png|webp|bmp|gif)$/i.test(file.name)) {
      toast('请选择 PNG、JPG、WebP、BMP 或 GIF 图片', 'error')
      return
    }
    if (file.size > 12 * 1024 * 1024) {
      toast('图片过大，最大允许 12MB', 'error')
      return
    }
    if (previewUrl) URL.revokeObjectURL(previewUrl)
    setPreviewUrl(URL.createObjectURL(file))
    setCandidates([])
    setWarnings([])
    setSelected(null)
    setShareQuantity('')
    setCostPrice('')
    imagePreview.mutate(file)
  }

  const parsedQuantity = Number(shareQuantity)
  const parsedCostPrice = Number(costPrice)
  const positionCost = parsedQuantity > 0 && parsedCostPrice > 0 ? parsedQuantity * parsedCostPrice : null
  const selectedExists = selected ? positions.some(row => row.symbol === selected.symbol) : false
  const canSubmit = Boolean(
    selected
    && positionCost != null
    && Number.isFinite(positionCost)
    && Number.isFinite(parsedQuantity)
    && Number.isFinite(parsedCostPrice)
    && !save.isPending,
  )

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!selected || !canSubmit) return
    save.mutate({
      symbol: selected.symbol,
      input: { name: selected.name, buy_price: parsedCostPrice, quantity: parsedQuantity },
    })
  }

  return (
    <Modal
      onClose={onClose}
      labelledBy="stock-position-dialog-title"
      closeOnBackdrop={!save.isPending && !imagePreview.isPending}
      panelClassName="flex max-h-[92vh] w-[94vw] max-w-2xl flex-col overflow-hidden rounded-card border border-border bg-surface shadow-xl"
    >
      <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col">
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div>
            <h2 id="stock-position-dialog-title" className="text-sm font-semibold text-foreground">
              {position ? '编辑持股' : '添加持股'}
            </h2>
            <p className="mt-1 text-[11px] text-muted">
              {position ? '修改买入数量和成本价，系统会重新计算持仓数据。' : '选择股票后填写买入数量和成本价。'}
            </p>
          </div>
          <button type="button" onClick={onClose} disabled={save.isPending || imagePreview.isPending} className="inline-flex h-8 w-8 items-center justify-center rounded-btn text-muted hover:bg-elevated hover:text-foreground disabled:opacity-40" aria-label="关闭">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {!position && (
            <div className="inline-flex rounded-btn border border-border bg-elevated p-0.5">
              <button type="button" onClick={() => setMode('manual')} className={`inline-flex items-center gap-1.5 rounded-btn px-3 py-1.5 text-xs ${mode === 'manual' ? 'bg-surface text-foreground shadow-sm' : 'text-muted hover:text-foreground'}`}>
                <Search className="h-3.5 w-3.5" />搜索股票
              </button>
              <button type="button" onClick={() => setMode('image')} className={`inline-flex items-center gap-1.5 rounded-btn px-3 py-1.5 text-xs ${mode === 'image' ? 'bg-surface text-foreground shadow-sm' : 'text-muted hover:text-foreground'}`}>
                <ImagePlus className="h-3.5 w-3.5" />图片导入
              </button>
            </div>
          )}

          {!position && mode === 'manual' && (
            <div className="mt-4">
              <label className="relative block">
                <span className="mb-1.5 block text-[11px] font-medium text-secondary">1. 搜索并选择股票</span>
                <span className="relative block">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
                  <input
                    value={searchText}
                    onChange={event => {
                      setSearchText(event.target.value)
                      setSelected(null)
                      setSearchOpen(true)
                    }}
                    onFocus={() => setSearchOpen(true)}
                    onBlur={() => window.setTimeout(() => setSearchOpen(false), 120)}
                    onKeyDown={handleSearchKeyDown}
                    placeholder="输入股票代码或名称，如 600519 / 贵州茅台"
                    role="combobox"
                    aria-autocomplete="list"
                    aria-expanded={searchOpen}
                    aria-controls="stock-dialog-search-results"
                    autoFocus
                    className="h-11 w-full rounded-btn border border-border bg-elevated pl-10 pr-9 text-xs text-foreground outline-none transition-colors placeholder:text-muted focus:border-accent/60"
                  />
                  {search.isFetching && <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-accent" />}
                  {!search.isFetching && searchText && (
                    <button type="button" onClick={() => { setSearchText(''); setSelected(null); setSearchOpen(true) }} className="absolute right-2 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded text-muted hover:bg-surface hover:text-foreground" aria-label="清空股票搜索">
                      <X className="h-3.5 w-3.5" />
                    </button>
                  )}
                </span>

                {searchOpen && searchTerm && (
                  <div id="stock-dialog-search-results" role="listbox" className="absolute z-30 mt-1 max-h-64 w-full overflow-auto rounded-card border border-border bg-surface p-1 shadow-xl">
                    {search.isFetching ? (
                      <div className="flex items-center justify-center gap-2 px-3 py-5 text-xs text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" />搜索中</div>
                    ) : results.length > 0 ? results.map((instrument, index) => {
                      const exists = positions.some(row => row.symbol === instrument.symbol)
                      return (
                        <button
                          key={instrument.symbol}
                          type="button"
                          role="option"
                          aria-selected={highlighted === index}
                          onMouseDown={event => event.preventDefault()}
                          onMouseEnter={() => setHighlighted(index)}
                          onClick={() => selectInstrument(instrument)}
                          className={`flex w-full items-center justify-between gap-3 rounded-btn px-3 py-2 text-left ${highlighted === index ? 'bg-accent/10' : 'hover:bg-elevated'}`}
                        >
                          <span className="min-w-0">
                            <span className="block truncate text-xs font-medium text-foreground">{instrument.name}</span>
                            <span className="mt-0.5 block font-mono text-[10px] text-muted">{instrument.symbol}</span>
                          </span>
                          {exists && <span className="shrink-0 rounded-full bg-warning/10 px-2 py-0.5 text-[9px] text-warning">已持有</span>}
                        </button>
                      )
                    }) : (
                      <div className="px-3 py-5 text-center text-xs text-muted">未找到匹配股票，请检查本地标的维表</div>
                    )}
                  </div>
                )}
              </label>
            </div>
          )}

          {!position && mode === 'image' && (
            <div className="mt-4">
              <span className="mb-1.5 block text-[11px] font-medium text-secondary">1. 上传持仓或成交截图</span>
              <input
                ref={fileInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp,image/bmp,image/gif,.jpg,.jpeg,.png"
                className="hidden"
                onChange={event => {
                  runImagePreview(event.target.files?.[0])
                  event.target.value = ''
                }}
              />
              {ocrStatus.data?.available === false ? (
                <div className="rounded-btn border border-danger/25 bg-danger/5 px-3 py-3 text-xs text-danger">
                  <div className="flex items-center gap-1.5 font-medium"><AlertTriangle className="h-4 w-4" />本机 OCR 不可用</div>
                  <p className="mt-1.5 whitespace-pre-line text-[10px] leading-4 text-muted">{getOcrInstallHint()}</p>
                </div>
              ) : (
                <button
                  type="button"
                  disabled={ocrStatus.isLoading || imagePreview.isPending}
                  onClick={() => fileInputRef.current?.click()}
                  onDragOver={event => event.preventDefault()}
                  onDrop={event => {
                    event.preventDefault()
                    runImagePreview(event.dataTransfer.files?.[0])
                  }}
                  className="flex min-h-28 w-full flex-col items-center justify-center gap-2 rounded-btn border border-dashed border-border bg-elevated/35 px-4 py-4 text-xs text-secondary transition-colors hover:border-accent/40 hover:bg-elevated/60 disabled:cursor-not-allowed disabled:opacity-55"
                >
                  {ocrStatus.isLoading || imagePreview.isPending ? <Loader2 className="h-6 w-6 animate-spin text-accent" /> : <ImagePlus className="h-6 w-6 text-accent" />}
                  <span>{imagePreview.isPending ? '正在识别图片' : ocrStatus.isLoading ? '正在检查 OCR' : '点击选择或拖入图片'}</span>
                  <span className="text-[10px] text-muted">支持 PNG、JPG、WebP、BMP、GIF，最大 12MB</span>
                </button>
              )}

              {previewUrl && (
                <div className="mt-3 flex items-start gap-3 rounded-btn border border-border bg-base p-2.5">
                  <img src={previewUrl} alt="待识别的持仓截图" className="h-20 w-24 shrink-0 rounded-btn border border-border object-contain" />
                  <div className="min-w-0 flex-1 text-[10px] leading-5 text-muted">
                    图片只发送给本机服务进行 OCR，不上传第三方。识别结果必须经你确认后才会保存。
                  </div>
                </div>
              )}

              {warnings.length > 0 && (
                <div className="mt-3 border-l-2 border-warning bg-warning/5 px-3 py-2 text-[10px] leading-5 text-secondary">
                  {warnings.map(warning => <div key={warning}>{warning}</div>)}
                </div>
              )}

              {candidates.length > 0 && (
                <div className="mt-3">
                  <div className="mb-1.5 text-[10px] text-muted">识别到 {candidates.length} 只股票，请选择要导入的记录</div>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {candidates.map(candidate => {
                      const active = selected?.symbol === candidate.symbol
                      return (
                        <button key={candidate.symbol} type="button" onClick={() => selectImportCandidate(candidate)} className={`rounded-btn border px-3 py-2.5 text-left transition-colors ${active ? 'border-accent bg-accent/10' : 'border-border bg-elevated/35 hover:border-accent/40'}`}>
                          <span className="flex items-center justify-between gap-2">
                            <span className="truncate text-xs font-medium text-foreground">{candidate.name || candidate.symbol}</span>
                            {active && <CheckCircle2 className="h-4 w-4 shrink-0 text-accent" />}
                          </span>
                          <span className="mt-1 block font-mono text-[10px] text-muted">{candidate.symbol}</span>
                          <span className="mt-1.5 block text-[10px] text-secondary">数量 {numberText(candidate.quantity) || '--'} · 成本 ¥{money(candidate.cost_amount)}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>
          )}

          {position && selected && (
            <div className="rounded-btn border border-border bg-elevated/45 px-3 py-3">
              <div className="text-xs font-medium text-foreground">{selected.name || selected.symbol}</div>
              <div className="mt-1 font-mono text-[10px] text-muted">{selected.symbol}</div>
            </div>
          )}

          {selected && (
            <div className="mt-4 border-t border-border pt-4">
              {!position && (
                <div className="mb-3 flex items-center gap-2 rounded-btn border border-accent/25 bg-accent/5 px-3 py-2.5">
                  <CheckCircle2 className="h-4 w-4 shrink-0 text-accent" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium text-foreground">{selected.name || selected.symbol}</div>
                    <div className="mt-0.5 font-mono text-[10px] text-muted">{selected.symbol}</div>
                  </div>
                  {selectedExists && <span className="shrink-0 rounded-full bg-warning/10 px-2 py-0.5 text-[9px] text-warning">保存后覆盖原持仓</span>}
                </div>
              )}

              <div className="mb-1.5 text-[11px] font-medium text-secondary">2. 填写持仓数据</div>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1.5 block text-[10px] text-muted">买入数量（股）</span>
                  <input type="number" min="0" step="0.0001" value={shareQuantity} onChange={event => setShareQuantity(event.target.value)} onWheel={event => event.currentTarget.blur()} placeholder="例如 100" className="h-10 w-full rounded-btn border border-border bg-elevated px-3 font-mono text-xs text-foreground outline-none placeholder:text-muted focus:border-accent/60" />
                </label>
                <label className="block">
                  <span className="mb-1.5 block text-[10px] text-muted">买入成本价（元/股）</span>
                  <input type="number" min="0" step="0.0001" value={costPrice} onChange={event => setCostPrice(event.target.value)} onWheel={event => event.currentTarget.blur()} placeholder="例如 1500.00" className="h-10 w-full rounded-btn border border-border bg-elevated px-3 font-mono text-xs text-foreground outline-none placeholder:text-muted focus:border-accent/60" />
                </label>
              </div>

              <div className="mt-3 flex items-center gap-3 rounded-btn border border-border/70 bg-base px-3 py-2.5">
                <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-btn bg-accent/10 text-accent"><Calculator className="h-4 w-4" /></span>
                <div className="min-w-0 flex-1">
                  <div className="text-[9px] text-muted">系统计算持仓总成本</div>
                  <div className="mt-0.5 font-mono text-sm font-semibold text-foreground">¥{money(positionCost)}</div>
                </div>
                <div className="text-right text-[9px] leading-4 text-muted">成本价 × 数量<br />保存后自动生成市值与盈亏</div>
              </div>
            </div>
          )}
        </div>

        <div className="flex shrink-0 items-center justify-between gap-3 border-t border-border px-5 py-3">
          <span className="text-[10px] text-muted">{selected ? `已选择 ${selected.symbol}` : '请先选择一只股票'}</span>
          <div className="flex items-center gap-2">
            <button type="button" onClick={onClose} disabled={save.isPending || imagePreview.isPending} className="rounded-btn border border-border px-3 py-1.5 text-xs text-secondary hover:bg-elevated disabled:opacity-40">取消</button>
            <button type="submit" disabled={!canSubmit} className="inline-flex items-center gap-1.5 rounded-btn bg-accent px-3 py-1.5 text-xs font-medium text-white hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-45">
              {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              {position || selectedExists ? '保存修改' : '添加持股'}
            </button>
          </div>
        </div>
      </form>
    </Modal>
  )
}
