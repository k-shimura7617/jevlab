import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, Link, Navigate, Outlet, RouterProvider, useLocation, useParams, useRouteError } from 'react-router'
import { ErrorScreen, GlobalErrorNotices, installGlobalErrorHandlers, RootErrorBoundary, RouteError } from './errors'
import { AppPage } from './pages/AppPage'
import { Dashboard } from './pages/Dashboard'
import { RunPage } from './pages/RunPage'
import { Channels } from './ops/pages/Channels'
import { Connectors, SlackSettings } from './ops/pages/Connectors'
import { Escalations } from './ops/pages/Escalations'
import { Inbox } from './ops/pages/Inbox'
import { ItemPage } from './ops/pages/ItemPage'
import { OpsHome } from './ops/pages/Home'
import { AdminHome } from './ops/pages/OpsHome'
import { AuditLog } from './ops/pages/AuditLog'
import { AdminSettings, OpsSettings } from './ops/pages/OpsSettings'
import { PiiReview } from './ops/pages/PiiReview'
import { Review } from './ops/pages/Review'
import { Staff } from './ops/pages/Staff'
import { AdminPii, Tuning } from './ops/pages/Tuning'
import { WaitingRedirect } from './ops/tabs'
import { OpsProvider } from './ops/state'
import { Page, ShellProvider } from './shell'
import { ContractPage } from './tools/ContractPage'
import { ReplyPage } from './tools/ReplyPage'
import { TonePage } from './tools/TonePage'
import './styles.css'
import './ops/ops.css'
import './tools/tools.css'

/** パンくずの先頭（いまの URL がどの区分か）。 */
function sectionCrumb(pathname: string): { label: string; to?: string } {
  if (pathname.startsWith('/eval')) return { label: '評価ダッシュボード', to: '/eval' }
  if (pathname.startsWith('/tools')) return { label: 'ツール' }
  if (pathname.startsWith('/admin')) return { label: '管理', to: '/admin' }
  return { label: '運用', to: '/ops' }
}

function NotFound() {
  const { pathname } = useLocation()
  return (
    <Page crumbs={[sectionCrumb(pathname), { label: '見つかりません' }]}>
      <h1>ページが見つかりません</h1>
      <Link to="/ops">運用ダッシュボードへ戻る</Link>
    </Page>
  )
}

/** 各画面で起きた描画エラー。サイドバーは残し、画面の中にエラーを出す。 */
function PageError() {
  const error = useRouteError()
  const { pathname } = useLocation()
  console.error('画面の表示中にエラーが起きました', error)
  return (
    <Page crumbs={[sectionCrumb(pathname), { label: 'エラー' }]}>
      <ErrorScreen error={error} inline />
    </Page>
  )
}

/** 以前の評価ダッシュボードの URL（/apps/…）を、/eval/apps/… に移す（ブックマークを壊さないため）。 */
function LegacyAppRedirect({ run }: { run: boolean }) {
  const { name = '' } = useParams()
  const { search } = useLocation()
  return <Navigate replace to={`/eval/apps/${encodeURIComponent(name)}${run ? '/run' : ''}${search}`} />
}

const router = createBrowserRouter([
  {
    element: (
      <ShellProvider>
        <OpsProvider>
          <Outlet />
        </OpsProvider>
      </ShellProvider>
    ),
    errorElement: <RouteError />,
    children: [
      {
        // 画面ごとの描画エラーはここで受け止める（サイドバーを残す）
        errorElement: <PageError />,
        children: [
          // 運用ダッシュボードを入口にする。評価ダッシュボードは /eval の下
          { path: '/', element: <Navigate replace to="/ops" /> },
          { path: '/eval', element: <Dashboard /> },
          { path: '/eval/apps/:name', element: <AppPage /> },
          { path: '/eval/apps/:name/run', element: <RunPage /> },
          { path: '/apps/:name', element: <LegacyAppRedirect run={false} /> },
          { path: '/apps/:name/run', element: <LegacyAppRedirect run /> },
          { path: '/ops', element: <OpsHome /> },
          { path: '/ops/inbox', element: <Inbox /> },
          { path: '/ops/pii', element: <PiiReview /> },
          { path: '/ops/review', element: <Review /> },
          { path: '/ops/escalations', element: <Escalations /> },
          { path: '/ops/waiting', element: <WaitingRedirect /> },
          { path: '/ops/settings', element: <OpsSettings /> },
          { path: '/ops/settings/slack', element: <SlackSettings /> },
          { path: '/ops/tuning', element: <Tuning /> },
          { path: '/ops/staff', element: <Staff /> },
          // 管理（開発側）。利用者のメニューには出さない
          { path: '/admin', element: <AdminHome /> },
          { path: '/admin/settings', element: <AdminSettings /> },
          { path: '/admin/pii', element: <AdminPii /> },
          { path: '/admin/connectors', element: <Connectors /> },
          { path: '/admin/channels', element: <Channels /> },
          { path: '/admin/audit', element: <AuditLog /> },
          // 以前の URL を移す（ブックマークを壊さないため）
          { path: '/ops/channels', element: <Navigate replace to="/admin/channels" /> },
          { path: '/ops/connectors', element: <Navigate replace to="/admin/connectors" /> },
          { path: '/ops/audit', element: <Navigate replace to="/admin/audit" /> },
          { path: '/tools/tone', element: <TonePage /> },
          { path: '/tools/contract', element: <ContractPage /> },
          { path: '/tools/reply', element: <ReplyPage /> },
          { path: '/ops/items/:id', element: <ItemPage /> },
          { path: '*', element: <NotFound /> },
        ],
      },
    ],
  },
])

const root = document.getElementById('root')
if (root === null) throw new Error('#root が見つからない')
installGlobalErrorHandlers()
createRoot(root).render(
  <StrictMode>
    <RootErrorBoundary>
      <RouterProvider router={router} />
      <GlobalErrorNotices />
    </RootErrorBoundary>
  </StrictMode>,
)
