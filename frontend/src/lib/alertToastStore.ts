import type { AlertEvent } from '@/lib/api'
import { playNotificationSound } from '@/lib/notificationSound'
import { speakAlerts } from '@/lib/voiceBroadcast'

/** 告警 Toast 队列项。 */
export interface AlertToastItem {
  id: number
  alert: AlertEvent
}

let nextId = 0
let queue: AlertToastItem[] = []
const AUTO_DISMISS_MS = 5_000
const listeners = new Set<(items: AlertToastItem[]) => void>()

/** 读取告警 Toast 开关，存储不可用时默认开启。 */
function isAlertToastEnabled(): boolean {
  try {
    const value = localStorage.getItem('alert_toast_enabled')
    return value === null ? true : value === '1'
  } catch {
    return true
  }
}

/** 读取最大可见数量，并限制在 1 到 10 条。 */
function maxVisibleAlerts(): number {
  try {
    const value = Number.parseInt(localStorage.getItem('alert_toast_max') || '', 10)
    return value >= 1 && value <= 10 ? value : 3
  } catch {
    return 3
  }
}

/** 向所有订阅者发布当前队列快照。 */
function emitAlertToasts(): void {
  const snapshot = [...queue]
  listeners.forEach(listener => listener(snapshot))
}

/** 订阅告警 Toast 队列变化。 */
export function subscribeAlertToasts(listener: (items: AlertToastItem[]) => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** 通知设置页配置已变化。 */
export function refreshAlertToastConfig(): void {
  emitAlertToasts()
}

/** 推入单条监控告警，声音由批量入口统一触发。 */
export function pushAlertToast(alert: AlertEvent): void {
  pushAlertToasts([alert])
}

/** 批量推入监控告警，并为整批统一触发声音和语音。 */
export function pushAlertToasts(alerts: AlertEvent[]): void {
  if (alerts.length === 0 || !isAlertToastEnabled()) return

  const newItems = alerts.map(alert => ({ id: ++nextId, alert }))
  queue = [...queue, ...newItems].slice(-maxVisibleAlerts())
  emitAlertToasts()

  newItems.forEach(item => {
    window.setTimeout(() => dismissAlertToast(item.id), AUTO_DISMISS_MS)
  })
  playNotificationSound()
  speakAlerts(alerts)
}

/** 按队列 ID 关闭一条告警 Toast。 */
export function dismissAlertToast(id: number): void {
  queue = queue.filter(item => item.id !== id)
  emitAlertToasts()
}
