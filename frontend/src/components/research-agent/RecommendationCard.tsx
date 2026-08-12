import { AlertTriangle, ChevronDown, ChevronUp, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import type { ResearchRecommendationPick } from '@/lib/api'

const stanceClass: Record<ResearchRecommendationPick['stance'], string> = {
  '偏买入': 'border-bull/30 bg-bull/10 text-bull',
  '观察': 'border-warning/30 bg-warning/10 text-warning',
  '偏卖出': 'border-bear/30 bg-bear/10 text-bear',
  '回避': 'border-danger/30 bg-danger/10 text-danger',
}

export function RecommendationCard({ pick }: { pick: ResearchRecommendationPick }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <article className="rounded-card border border-border bg-surface/70 p-3.5">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-foreground">{pick.name}</span>
            <span className="font-mono text-[11px] text-muted">{pick.symbol}</span>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${stanceClass[pick.stance]}`}>
              {pick.stance}
            </span>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-secondary">{pick.thesis}</p>
        </div>
        <div className="shrink-0 text-right">
          <div className="font-mono text-sm font-semibold text-purple-400">{pick.confidence}</div>
          <div className="text-[9px] text-muted">置信度</div>
        </div>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {pick.evidence.slice(0, expanded ? undefined : 2).map((item, index) => (
          <div key={`${item.dimension}-${index}`} className="rounded-md bg-elevated/45 p-2.5">
            <div className="flex items-center gap-1 text-[10px] font-medium text-purple-400">
              <ShieldCheck className="h-3 w-3" /> {item.dimension}
              {item.as_of && <span className="ml-auto font-normal text-muted">{item.as_of}</span>}
            </div>
            <div className="mt-1 text-[11px] leading-relaxed text-foreground">{item.conclusion}</div>
            {expanded && item.supports.length > 0 && (
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[10px] text-secondary">
                {item.supports.map(value => <li key={value}>{value}</li>)}
              </ul>
            )}
            {expanded && item.risks.length > 0 && (
              <div className="mt-1 text-[10px] text-warning">该维度风险：{item.risks.join('；')}</div>
            )}
            <div className="mt-1 text-[9px] text-muted">
              来源：{item.source_url ? (
                <a className="text-purple-400 hover:underline" href={item.source_url} target="_blank" rel="noreferrer">
                  {item.source}
                </a>
              ) : item.source}
            </div>
          </div>
        ))}
      </div>

      {expanded && (
        <div className="mt-3 space-y-2 text-[11px] leading-relaxed">
          <section className="rounded-md border border-warning/20 bg-warning/[0.04] p-2.5">
            <div className="flex items-center gap-1 font-medium text-warning"><AlertTriangle className="h-3 w-3" />反向证据</div>
            <ul className="mt-1 list-disc space-y-1 pl-4 text-secondary">{pick.counter_evidence.map(item => <li key={item}>{item}</li>)}</ul>
          </section>
          <section className="rounded-md border border-danger/20 bg-danger/[0.04] p-2.5">
            <div className="font-medium text-danger">主要风险</div>
            <ul className="mt-1 list-disc space-y-1 pl-4 text-secondary">{pick.risks.map(item => <li key={item}>{item}</li>)}</ul>
          </section>
          <div className="grid gap-2 sm:grid-cols-2">
            <section className="rounded-md bg-elevated/40 p-2.5">
              <div className="font-medium text-foreground">研究周期</div>
              <div className="mt-1 text-secondary">{pick.horizon_days}</div>
            </section>
            <section className="rounded-md bg-elevated/40 p-2.5">
              <div className="font-medium text-foreground">潜在催化</div>
              <div className="mt-1 text-secondary">{pick.catalysts.length ? pick.catalysts.join('；') : '暂无明确催化'}</div>
            </section>
          </div>
          <section className="rounded-md border border-border bg-elevated/25 p-2.5">
            <div className="font-medium text-foreground">假设失效条件</div>
            <div className="mt-1 text-secondary">{pick.invalidation_conditions.length ? pick.invalidation_conditions.join('；') : '尚未给出明确条件'}</div>
          </section>
          {(pick.watch_zone || pick.risk_level) && (
            <div className="grid grid-cols-2 gap-2 text-secondary">
              <div className="rounded-md bg-elevated/40 p-2"><span className="text-muted">参考关注区：</span>{pick.watch_zone || '未给出'}</div>
              <div className="rounded-md bg-elevated/40 p-2"><span className="text-muted">风险位置：</span>{pick.risk_level || '未给出'}</div>
            </div>
          )}
          {pick.missing_data.length > 0 && <div className="text-muted">缺失数据：{pick.missing_data.join('、')}</div>}
        </div>
      )}
      <button type="button" onClick={() => setExpanded(value => !value)} className="mt-3 flex w-full items-center justify-center gap-1 border-t border-border/50 pt-2 text-[10px] text-muted hover:text-foreground">
        {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        {expanded ? '收起完整证据' : '查看完整证据与风险'}
      </button>
    </article>
  )
}
