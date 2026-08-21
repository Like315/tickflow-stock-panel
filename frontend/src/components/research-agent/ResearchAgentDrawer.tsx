import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpenText, Bot, Loader2, MessageSquareText, RefreshCw, RotateCcw, Telescope, X } from 'lucide-react'
import { MarkdownRenderer } from '@/components/financials/MarkdownRenderer'
import { api } from '@/lib/api'
import { QK } from '@/lib/queryKeys'
import {
  askResearchAgent,
  closeResearchAgent,
  setResearchAgentTab,
  useResearchAgentState,
  type ResearchAgentTab,
} from '@/lib/researchAgentStore'
import { RecommendationCard } from './RecommendationCard'
import { ReviewTimeline } from './ReviewTimeline'

const tabs: Array<{ id: ResearchAgentTab; label: string; icon: typeof Bot }> = [
  { id: 'chat', label: 'AI 问答', icon: MessageSquareText },
  { id: 'picks', label: '今日选股', icon: Telescope },
  { id: 'reviews', label: '推荐复盘', icon: RotateCcw },
]

export function ResearchAgentDrawer() {
  const state = useResearchAgentState()
  const [question, setQuestion] = useState('')
  useEffect(() => {
    if (!state.open) return
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') closeResearchAgent() }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [state.open])
  if (!state.open) return null

  const submit = () => {
    if (!question.trim()) return
    askResearchAgent(question, state.chat ? {
      context: state.chat.context,
      fundCode: state.chat.fundCode,
      contextLabel: state.chat.contextLabel,
    } : undefined)
    setQuestion('')
  }
  return (
    <div className="fixed inset-0 z-[65] flex justify-end" role="dialog" aria-modal="true" aria-label="AI 研究 Agent">
      <button type="button" aria-label="关闭 Agent" onClick={closeResearchAgent} className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" />
      <section className="relative flex h-full w-full flex-col border-l border-border bg-base shadow-2xl sm:max-w-[620px]">
        <header className="flex items-center gap-3 border-b border-border bg-surface/80 px-4 py-3">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-purple-500/15 text-purple-400 ring-1 ring-purple-500/25"><Bot className="h-4 w-4" /></span>
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-foreground">AI 研究 Agent</h2>
            <p className="text-[10px] text-muted">{state.chat?.context === 'fund_portfolio' ? '基金组合研究' : state.chat?.context === 'fund' ? `基金研究 · ${state.chat.contextLabel || state.chat.fundCode || ''}` : state.chat?.context === 'fund_market' ? '基金研究 · 历史数据 + 大盘趋势' : '多维分析 · 保守型 · 5–20 个交易日'}</p>
          </div>
          <button type="button" onClick={closeResearchAgent} className="rounded-md p-2 text-muted hover:bg-elevated hover:text-foreground" aria-label="关闭"><X className="h-4 w-4" /></button>
        </header>
        <nav className="grid grid-cols-3 border-b border-border bg-surface/50 px-3 pt-2">
          {tabs.map(({ id, label, icon: Icon }) => (
            <button key={id} type="button" onClick={() => setResearchAgentTab(id)} className={`flex items-center justify-center gap-1.5 border-b-2 px-2 py-2.5 text-[11px] transition-colors ${state.tab === id ? 'border-purple-400 text-purple-400' : 'border-transparent text-muted hover:text-foreground'}`}>
              <Icon className="h-3.5 w-3.5" />{label}
            </button>
          ))}
        </nav>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {state.tab === 'chat' && <ChatPanel />}
          {state.tab === 'picks' && <PicksPanel />}
          {state.tab === 'reviews' && <ReviewsPanel />}
        </div>
        {state.tab === 'chat' && (
          <footer className="border-t border-border bg-surface/80 p-3">
            <div className="flex items-center rounded-lg border border-border bg-base focus-within:border-purple-500/50">
              <input value={question} onChange={event => setQuestion(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) submit() }} placeholder={state.chat?.context.startsWith('fund') ? '继续追问当前基金研究…' : '继续追问或输入股票代码…'} className="min-w-0 flex-1 bg-transparent px-3 py-2.5 text-xs outline-none placeholder:text-muted" />
              <button type="button" onClick={submit} disabled={!question.trim()} className="mr-1.5 rounded-md bg-purple-500 px-3 py-1.5 text-[11px] font-medium text-white disabled:opacity-30">发送</button>
            </div>
            <p className="mt-1.5 text-center text-[9px] text-muted">仅供研究参考，不构成自动交易指令</p>
          </footer>
        )}
      </section>
    </div>
  )
}

function ChatPanel() {
  const { chat } = useResearchAgentState()
  if (!chat) {
    return (
      <div className="space-y-4">
        <div className="rounded-card border border-purple-500/20 bg-purple-500/[0.06] p-4">
          <BookOpenText className="h-5 w-5 text-purple-400" />
          <h3 className="mt-2 text-sm font-medium">从一个问题开始</h3>
          <p className="mt-1 text-xs leading-relaxed text-secondary">我可以解释信号与术语、分析指定 A 股，或说明今日候选的技术面、情绪面、基本面和信息面依据。</p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          {['解释 MACD 金叉', '市场宽度怎么看？', '分析 600000.SH', '今天适合关注什么类型的股票？'].map(question => (
            <button key={question} type="button" onClick={() => askResearchAgent(question)} className="rounded-card border border-border bg-surface p-3 text-left text-xs text-secondary hover:border-purple-500/35 hover:text-foreground">{question}</button>
          ))}
        </div>
      </div>
    )
  }
  return (
    <div>
      <div className="mb-4 ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-purple-500 px-3.5 py-2.5 text-xs leading-relaxed text-white">{chat.question}</div>
      <div className="rounded-card border border-border bg-surface/70 p-4">
        <div className="mb-3 flex items-center gap-2 text-[10px] text-muted"><Bot className="h-3.5 w-3.5 text-purple-400" />{chat.mode === 'term' ? '内置知识库' : chat.mode === 'fund_portfolio' ? '基金组合研究' : chat.mode === 'fund' ? `单基金研究 · ${chat.contextLabel || chat.fundCode || ''}` : chat.mode === 'fund_market' ? '基金市场研究' : '多维研究分析'}{chat.asOf && <span>· 数据截至 {chat.asOf}</span>}</div>
        {(chat.phase === 'loading') && <div className="flex items-center gap-2 py-8 text-xs text-muted"><Loader2 className="h-4 w-4 animate-spin text-purple-400" />{chat.context === 'fund_market' ? '正在读取基金历史净值与大盘趋势…' : chat.context.startsWith('fund') ? '正在读取基金持仓与公开净值…' : '正在聚合证据并分析…'}</div>}
        {chat.content && <MarkdownRenderer content={chat.content} />}
        {chat.phase === 'streaming' && <span className="inline-block h-4 w-1 animate-pulse bg-purple-400 align-middle" />}
        {chat.phase === 'error' && <div className="rounded-md border border-danger/25 bg-danger/[0.06] p-3 text-xs text-danger">{chat.error}</div>}
      </div>
    </div>
  )
}

function PicksPanel() {
  const qc = useQueryClient()
  const latest = useQuery({ queryKey: QK.researchAgentLatest, queryFn: api.researchAgentLatest })
  const run = useMutation({
    mutationFn: (force: boolean) => api.researchAgentRunRecommendations(force),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: QK.researchAgentLatest }) },
  })
  const batch = latest.data?.batch
  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2 rounded-card border border-border bg-surface/70 p-3">
        <div className="min-w-0 flex-1"><div className="text-xs font-medium">每日研究候选</div><p className="mt-1 text-[10px] leading-relaxed text-muted">全 A 股量化预筛后由 AI 比较，证据不足时少于 5 只。同日默认复用历史结论。</p></div>
        <button type="button" onClick={() => run.mutate(Boolean(batch))} disabled={run.isPending} className="flex shrink-0 items-center gap-1 rounded-btn bg-purple-500 px-2.5 py-1.5 text-[10px] font-medium text-white disabled:opacity-50">{run.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}{batch ? '重新分析' : '生成今日选股'}</button>
      </div>
      {latest.isLoading && <Loading label="正在读取最新推荐…" />}
      {run.data?.message && <Notice>{run.data.message}</Notice>}
      {run.isError && <Notice>{run.error instanceof Error ? run.error.message : '生成推荐失败，请稍后重试。'}</Notice>}
      {latest.isError && <Notice>读取推荐失败，请稍后重试。</Notice>}
      {!latest.isLoading && !batch && !run.data?.message && <Empty label="尚未生成正式推荐。AI 未配置时仍可查看内置术语解释。" />}
      {batch && (
        <>
          <div className="flex flex-wrap items-center gap-2 px-1 text-[10px] text-muted"><span>数据日期 {batch.as_of}</span><span>版本 v{batch.version}</span><span>{batch.model || '当前 AI 模型'}</span><span className="ml-auto">{batch.picks.length} 只</span></div>
          {batch.picks.map(pick => <RecommendationCard key={pick.symbol} pick={pick} />)}
          <p className="px-1 text-[9px] leading-relaxed text-muted">参考关注区与风险位置为分析推断，不代表真实委托或保证成交。历史推荐不会因重新分析被覆盖。</p>
        </>
      )}
    </div>
  )
}

function ReviewsPanel() {
  const qc = useQueryClient()
  const batches = useQuery({ queryKey: QK.researchAgentRecommendations, queryFn: () => api.researchAgentRecommendations(20, 0) })
  const [batchId, setBatchId] = useState<string>('')
  useEffect(() => { if (!batchId && batches.data?.batches[0]?.id) setBatchId(batches.data.batches[0].id) }, [batchId, batches.data])
  const reviews = useQuery({ queryKey: QK.researchAgentReviews(batchId), queryFn: () => api.researchAgentReviews(batchId), enabled: Boolean(batchId) })
  const run = useMutation({ mutationFn: api.researchAgentRunReviews, onSuccess: async () => { await qc.invalidateQueries({ queryKey: QK.researchAgentReviewsRoot }) } })
  const selected = useMemo(() => batches.data?.batches.find(batch => batch.id === batchId), [batches.data, batchId])
  const latestReviewDate = reviews.data?.reviews.reduce(
    (latest, item) => item.trade_date > latest ? item.trade_date : latest,
    '',
  )
  const today = reviews.data?.reviews.filter(item => (
    item.trade_date === latestReviewDate
  )) ?? []
  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <select value={batchId} onChange={event => setBatchId(event.target.value)} className="min-w-0 flex-1 rounded-btn border border-border bg-surface px-2.5 py-2 text-[11px] text-foreground outline-none">
          {(batches.data?.batches ?? []).map(batch => <option key={batch.id} value={batch.id}>{batch.as_of} · v{batch.version} · {batch.picks.length}只</option>)}
        </select>
        <button type="button" onClick={() => run.mutate()} disabled={run.isPending} className="flex shrink-0 items-center gap-1 rounded-btn border border-border bg-surface px-2.5 py-2 text-[10px] text-secondary hover:text-foreground disabled:opacity-50">{run.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}复盘今日</button>
      </div>
      {selected && <div className="rounded-card bg-elevated/40 px-3 py-2 text-[10px] text-muted">原始推荐 {selected.as_of} · 复盘不会改写当时理由 · 第 5/10/20 日额外总结</div>}
      {run.data?.message && <Notice>{run.data.message}</Notice>}
      {run.isError && <Notice>{run.error instanceof Error ? run.error.message : '运行复盘失败，请稍后重试。'}</Notice>}
      {batches.isError && <Notice>读取历史推荐失败，请稍后重试。</Notice>}
      {reviews.isError && <Notice>读取复盘轨迹失败，请稍后重试。</Notice>}
      {(batches.isLoading || reviews.isLoading) && <Loading label="正在读取复盘轨迹…" />}
      {!batches.isLoading && !(batches.data?.batches.length) && <Empty label="暂无历史推荐，生成今日选股后开始跟踪。" />}
      {today.length > 0 && (
        <div className="rounded-card border border-purple-500/20 bg-purple-500/[0.05] p-3 text-[10px] text-secondary">
          今日复盘总览：{today.length} 只 · 增强 {today.filter(item => item.thesis_state === '增强').length} · 减弱/失效 {today.filter(item => ['减弱', '失效'].includes(item.thesis_state)).length}
        </div>
      )}
      <ReviewTimeline reviews={reviews.data?.reviews ?? []} stages={reviews.data?.stages ?? []} />
    </div>
  )
}

function Loading({ label }: { label: string }) { return <div className="flex items-center justify-center gap-2 py-12 text-xs text-muted"><Loader2 className="h-4 w-4 animate-spin text-purple-400" />{label}</div> }
function Notice({ children }: { children: string }) { return <div className="rounded-card border border-warning/25 bg-warning/[0.06] p-3 text-xs text-warning">{children}</div> }
function Empty({ label }: { label: string }) { return <div className="rounded-card border border-dashed border-border p-10 text-center text-xs leading-relaxed text-muted">{label}</div> }
