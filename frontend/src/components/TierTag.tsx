import { tierBaseName, tierStyle } from '@/lib/capability-labels'

/** 渲染与左侧菜单一致的档位胶囊标签。 */
export function TierTag({ label, className = '' }: { label: string; className?: string }) {
  const style = tierStyle(label)
  const base = tierBaseName(label)
  const display = base === 'none' ? 'None' : base

  return (
    <span
      className={`inline-flex h-[18px] max-w-[80px] shrink-0 items-center overflow-hidden rounded px-1.5 text-[10px] font-bold font-mono leading-none ${className}`}
      style={style.tagBg}
    >
      <span className="truncate capitalize" style={style.labelTextStyle}>{display}</span>
    </span>
  )
}
