import { createBrowserRouter, Navigate } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Onboarding } from './pages/Onboarding'
import { Auth } from './pages/Auth'
import { OnboardingGuard } from './components/OnboardingGuard'
import {
  AnalysisDetail,
  Backtest,
  Branding,
  ConceptAnalysis,
  Dashboard,
  Data,
  Dev,
  Financials,
  FundPortfolio,
  Indices,
  IndustryAnalysis,
  InvestmentExpert,
  LeadingSector,
  LimitUpLadder,
  Monitor,
  Regime,
  Review,
  Screener,
  Settings,
  StockAnalysis,
  StockPortfolio,
  UsMarketDashboard,
  Watchlist,
} from './pages/lazyPages'

export const router = createBrowserRouter([
  { path: '/onboarding', element: <Onboarding /> },
  { path: '/login', element: <Auth /> },
  {
    path: '/',
    element: (
      <OnboardingGuard>
        <Layout />
      </OnboardingGuard>
    ),
    children: [
      { index: true, element: <Dashboard /> },
      { path: 'us-market', element: <UsMarketDashboard /> },
      { path: 'holdings', element: <StockPortfolio /> },
      { path: 'funds', element: <FundPortfolio /> },
      { path: 'overview', element: <Navigate to="/" replace /> },
      { path: 'analysis', element: <Navigate to="/settings?tab=ext-pages" replace /> },
      { path: 'analysis/:menuId', element: <AnalysisDetail /> },
      { path: 'concept-analysis', element: <ConceptAnalysis /> },
      { path: 'industry-analysis', element: <IndustryAnalysis /> },
      { path: 'leading-sector', element: <LeadingSector /> },
      { path: 'investment-expert', element: <InvestmentExpert /> },
      { path: 'stock-analysis', element: <StockAnalysis /> },
      { path: 'review', element: <Review /> },
      { path: 'watchlist', element: <Watchlist /> },
      { path: 'screener', element: <Screener /> },
      { path: 'backtest', element: <Backtest /> },
      { path: 'financials', element: <Financials /> },
      { path: 'data', element: <Data /> },
      { path: 'monitor', element: <Monitor /> },
      { path: 'limit-ladder', element: <LimitUpLadder /> },
      { path: 'indices', element: <Indices /> },
      { path: 'us-dashboard', element: <Navigate to="/us-market" replace /> },
    { path: 'regime', element: <Regime /> },
      { path: 'branding', element: <Branding /> },
      { path: 'settings', element: <Settings /> },
      // 隐藏路由：开发者工具（不暴露在菜单，仅供调试）
      { path: 'dev', element: <Dev /> },
      // 旧路由兼容重定向
      { path: 'settings/keys', element: <Navigate to="/settings?tab=account" replace /> },
      { path: 'settings/ai', element: <Navigate to="/settings?tab=ai" replace /> },
      { path: 'settings/queries', element: <Navigate to="/settings?tab=queries" replace /> },
    ],
  },
])
