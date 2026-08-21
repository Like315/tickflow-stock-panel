import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useSettings } from '@/lib/useSharedQueries'
import { Logo } from '@/components/Logo'

/** 在首次使用向导完成前拦截主应用路由。 */
export function OnboardingGuard({ children }: { children: ReactNode }) {
  const settings = useSettings()

  if (settings.isLoading) {
    return (
      <div className="min-h-screen bg-base grid place-items-center">
        <div className="flex flex-col items-center gap-3 text-muted">
          <Logo size={28} className="text-foreground" />
          <div className="text-xs">加载中…</div>
        </div>
      </div>
    )
  }

  // 查询失败时保持放行，避免后端短暂不可用导致用户卡在空白页。
  if (settings.data?.onboarding_completed === false) {
    return <Navigate to="/onboarding" replace />
  }

  return <>{children}</>
}
