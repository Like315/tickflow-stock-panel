import { useState } from 'react'
import { ArrowUp, BookOpenText, Sparkles, Telescope } from 'lucide-react'
import { askResearchAgent, openResearchAgent } from '@/lib/researchAgentStore'

export function ResearchAgentTopBar() {
  const [question, setQuestion] = useState('')

  const submit = () => {
    if (!question.trim()) return
    askResearchAgent(question)
    setQuestion('')
  }

  return (
    <div className="sticky top-0 z-30 border-b border-border/70 bg-base/90 px-4 py-2 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1600px] items-center gap-2">
        <button
          type="button"
          onClick={() => openResearchAgent('chat')}
          className="hidden shrink-0 items-center gap-2 rounded-btn px-2 py-1.5 text-xs font-medium text-foreground hover:bg-elevated sm:flex"
          title="打开 AI 研究 Agent"
        >
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-purple-500/15 text-purple-400 ring-1 ring-purple-500/25">
            <Sparkles className="h-3.5 w-3.5" />
          </span>
          AI 研究 Agent
        </button>
        <div className="flex min-w-0 flex-1 items-center rounded-lg border border-border bg-surface/80 shadow-sm focus-within:border-purple-500/50 focus-within:ring-1 focus-within:ring-purple-500/20">
          <Sparkles className="ml-3 h-3.5 w-3.5 shrink-0 text-purple-400" />
          <input
            value={question}
            onChange={event => setQuestion(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) submit() }}
            placeholder="问信号、分析 A 股或查看选股原因…"
            className="min-w-0 flex-1 bg-transparent px-2.5 py-2 text-xs text-foreground outline-none placeholder:text-muted"
          />
          <button
            type="button"
            onClick={submit}
            disabled={!question.trim()}
            aria-label="发送给 AI 研究 Agent"
            className="mr-1.5 flex h-7 w-7 items-center justify-center rounded-md bg-purple-500 text-white transition-colors hover:bg-purple-400 disabled:cursor-not-allowed disabled:opacity-30"
          >
            <ArrowUp className="h-3.5 w-3.5" />
          </button>
        </div>
        <button
          type="button"
          onClick={() => openResearchAgent('picks')}
          className="hidden shrink-0 items-center gap-1 rounded-btn border border-border bg-surface px-2.5 py-2 text-[11px] text-secondary hover:border-purple-500/40 hover:text-foreground md:flex"
        >
          <Telescope className="h-3.5 w-3.5 text-purple-400" /> 今日选股
        </button>
        <button
          type="button"
          onClick={() => askResearchAgent('解释 MACD 金叉')}
          className="hidden shrink-0 items-center gap-1 rounded-btn px-2 py-2 text-[11px] text-muted hover:bg-elevated hover:text-foreground xl:flex"
        >
          <BookOpenText className="h-3.5 w-3.5" /> 术语解惑
        </button>
      </div>
    </div>
  )
}
