import type { ResearchDailyReview, ResearchStageReview } from '@/lib/api'

function pct(value?: number | null) {
  if (value == null) return '—'
  return `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}%`
}

const stateClass: Record<ResearchDailyReview['thesis_state'], string> = {
  '增强': 'text-bull', '维持': 'text-accent', '减弱': 'text-warning', '失效': 'text-danger',
}

export function ReviewTimeline({ reviews, stages }: { reviews: ResearchDailyReview[]; stages: ResearchStageReview[] }) {
  if (!reviews.length) return <div className="rounded-card border border-dashed border-border p-8 text-center text-xs text-muted">暂无可复盘记录。推荐后的下一个有效交易日开始生成轨迹。</div>
  const ordered = [...reviews].sort((a, b) => b.trade_date.localeCompare(a.trade_date))
  return (
    <div className="space-y-3">
      {ordered.map(review => {
        const stage = stages.find(item => item.batch_id === review.batch_id && item.symbol === review.symbol && item.stage_day === review.holding_day)
        return (
          <div key={`${review.batch_id}-${review.symbol}-${review.trade_date}`} className="relative border-l border-border pl-4">
            <span className="absolute -left-1 top-1 h-2 w-2 rounded-full bg-purple-400 ring-4 ring-base" />
            <div className="rounded-card border border-border bg-surface/70 p-3">
              <div className="flex flex-wrap items-center gap-2 text-[10px] text-muted">
                <span className="font-mono text-foreground">{review.symbol}</span>
                <span>{review.trade_date}</span>
                <span>第 {review.holding_day} 个交易日</span>
                {review.is_backfill && <span className="rounded bg-elevated px-1.5 py-0.5">补算</span>}
                <span className={`ml-auto font-medium ${stateClass[review.thesis_state]}`}>{review.thesis_state}</span>
              </div>
              <div className="mt-2 grid grid-cols-3 gap-2 text-center">
                <Metric label="累计" value={pct(review.cumulative_return)} />
                <Metric label="最大回撤" value={pct(review.max_drawdown)} />
                <Metric label="相对沪深300" value={pct(review.relative_return)} />
              </div>
              <p className="mt-2 text-[11px] leading-relaxed text-secondary">{review.reflection}</p>
              {(review.support_changes.length > 0 || review.counter_changes.length > 0 || review.risks.length > 0) && (
                <div className="mt-2 grid gap-1.5 text-[10px] sm:grid-cols-3">
                  <ChangeList label="支持变化" values={review.support_changes} className="text-bull" />
                  <ChangeList label="反向变化" values={review.counter_changes} className="text-warning" />
                  <ChangeList label="当前风险" values={review.risks} className="text-danger" />
                </div>
              )}
              {stage && <div className="mt-2 rounded-md bg-purple-500/10 px-2.5 py-2 text-[11px] text-purple-300">{stage.stage_day} 日阶段总结：{stage.summary}</div>}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function ChangeList({ label, values, className }: { label: string; values: string[]; className: string }) {
  return (
    <div className="rounded-md bg-elevated/35 p-2">
      <div className={className}>{label}</div>
      <div className="mt-0.5 leading-relaxed text-secondary">{values.length ? values.join('；') : '无新增'}</div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="rounded-md bg-elevated/45 px-1 py-2"><div className="font-mono text-[11px] text-foreground">{value}</div><div className="mt-0.5 text-[9px] text-muted">{label}</div></div>
}
