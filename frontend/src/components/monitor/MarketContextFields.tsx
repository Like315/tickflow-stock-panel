import type {
  MonitorRule,
  NewsContextMode,
  OvernightUsContextMode,
} from '@/lib/api'

type ContextFilters = MonitorRule['context_filters']
type ContextMode = NewsContextMode | OvernightUsContextMode

interface ContextOption<TMode extends ContextMode> {
  value: TMode
  label: string
  threshold: number
}

const OVERNIGHT_OPTIONS: ContextOption<OvernightUsContextMode>[] = [
  { value: 'risk_gate', label: '明显走弱时暂停新入场', threshold: -0.35 },
  { value: 'require_positive', label: '必须处于偏强环境', threshold: 0.15 },
  { value: 'display_only', label: '仅在通知中展示', threshold: -0.35 },
  { value: 'off', label: '不参与判断', threshold: -0.35 },
]

const NEWS_OPTIONS: ContextOption<NewsContextMode>[] = [
  { value: 'negative_veto', label: '明显负面新闻时拦截', threshold: -0.35 },
  { value: 'require_positive', label: '必须有正面新闻确认', threshold: 0.25 },
  { value: 'display_only', label: '仅在通知中展示', threshold: -0.35 },
  { value: 'off', label: '不参与判断', threshold: -0.35 },
]

/** 展示单个市场上下文来源的模式选择。 */
function ContextModeSelect<TMode extends ContextMode>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: TMode
  options: ContextOption<TMode>[]
  onChange: (option: ContextOption<TMode>) => void
}) {
  return (
    <label className="space-y-1.5">
      <span className="text-[11px] text-muted">{label}</span>
      <select value={value}
        onChange={event => {
          const option = options.find(item => item.value === event.target.value)
          if (option) onChange(option)
        }}
        className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs text-foreground">
        {options.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </label>
  )
}

/** 编辑策略入场提醒使用的隔夜美股与个股新闻条件。 */
export function MarketContextFields({
  value,
  onChange,
}: {
  value: ContextFilters
  onChange: (value: ContextFilters) => void
}) {
  return (
    <div className="space-y-3 border-t border-border/60 pt-3">
      <div>
        <div className="text-[11px] font-medium text-foreground">市场增强条件</div>
        <div className="mt-1 text-[10px] leading-relaxed text-muted">只过滤新的入场提醒，不影响卖出信号和移出结果通知。</div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <ContextModeSelect label="隔夜美股" value={value.overnight_us.mode}
          options={OVERNIGHT_OPTIONS}
          onChange={option => onChange({ ...value, overnight_us: { mode: option.value, threshold: option.threshold } })} />
        <ContextModeSelect label="个股新闻" value={value.news.mode}
          options={NEWS_OPTIONS}
          onChange={option => onChange({ ...value, news: { mode: option.value, threshold: option.threshold } })} />
      </div>
      <label className="space-y-1.5">
        <span className="text-[11px] text-muted">增强数据不可用时</span>
        <select value={value.unavailable_action}
          onChange={event => onChange({
            ...value,
            unavailable_action: event.target.value as ContextFilters['unavailable_action'],
          })}
          className="h-9 w-full rounded-btn border border-border bg-base px-3 text-xs text-foreground">
          <option value="degrade">降级执行并显示警告</option>
          <option value="pause">暂停新的入场提醒</option>
        </select>
      </label>
    </div>
  )
}
