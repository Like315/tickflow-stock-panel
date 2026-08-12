import { useSyncExternalStore } from 'react'
import { api, type ResearchTerm } from './api'

export type ResearchAgentTab = 'chat' | 'picks' | 'reviews'
export type ResearchAgentPhase = 'idle' | 'loading' | 'streaming' | 'done' | 'error'
export type ResearchAgentContext = 'general' | 'fund_portfolio' | 'fund'

export interface ResearchAgentRequestOptions {
  context?: ResearchAgentContext
  fundCode?: string
  contextLabel?: string
}

export interface ResearchAgentChat {
  id: string
  question: string
  phase: ResearchAgentPhase
  content: string
  error: string
  mode?: string
  symbol?: string | null
  asOf?: string | null
  term?: ResearchTerm
  context: ResearchAgentContext
  fundCode?: string
  contextLabel?: string
}

interface State {
  open: boolean
  tab: ResearchAgentTab
  chat: ResearchAgentChat | null
}

let state: State = { open: false, tab: 'chat', chat: null }
let snapshot = state
const listeners = new Set<() => void>()

function emit() {
  snapshot = state
  listeners.forEach(listener => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

function getSnapshot() { return snapshot }

export function useResearchAgentState() {
  return useSyncExternalStore(subscribe, getSnapshot, () => state)
}

export function openResearchAgent(tab: ResearchAgentTab = 'chat') {
  state = { ...state, open: true, tab }
  emit()
}

export function closeResearchAgent() {
  state = { ...state, open: false }
  emit()
}

export function setResearchAgentTab(tab: ResearchAgentTab) {
  state = { ...state, open: true, tab }
  emit()
}

function patchChat(id: string, patch: Partial<ResearchAgentChat>) {
  if (!state.chat || state.chat.id !== id) return
  state = { ...state, chat: { ...state.chat, ...patch } }
  emit()
}

export function askResearchAgent(question: string, options: ResearchAgentRequestOptions = {}) {
  const normalized = question.trim()
  if (!normalized) return
  const id = `research_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`
  const context = options.context ?? 'general'
  state = {
    open: true,
    tab: 'chat',
    chat: {
      id,
      question: normalized,
      phase: 'loading',
      content: '',
      error: '',
      context,
      fundCode: options.fundCode,
      contextLabel: options.contextLabel,
    },
  }
  emit()
  void runChat(id, normalized, options)
}

async function runChat(id: string, question: string, options: ResearchAgentRequestOptions) {
  try {
    let content = ''
    let sawDone = false
    for await (const event of api.researchAgentChatStream(question, {
      context: options.context,
      fundCode: options.fundCode,
    })) {
      if (event.type === 'meta') {
        patchChat(id, {
          mode: event.mode,
          symbol: event.symbol,
          asOf: event.as_of,
          term: event.term,
        })
      } else if (event.type === 'delta') {
        content += event.content ?? ''
        patchChat(id, { phase: 'streaming', content })
      } else if (event.type === 'error') {
        patchChat(id, { phase: 'error', error: event.message ?? '分析失败' })
        return
      } else if (event.type === 'done') {
        sawDone = true
        patchChat(id, { phase: content ? 'done' : 'error', error: content ? '' : '没有返回分析内容' })
      }
    }
    if (!sawDone) {
      patchChat(id, { phase: 'error', error: '连接提前结束，请重试' })
    }
  } catch (error) {
    patchChat(id, { phase: 'error', error: error instanceof Error ? error.message : '分析失败' })
  }
}
