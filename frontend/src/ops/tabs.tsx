// 画面の上に並べるタブ（対応待ち・設定）。1 つのメニューの下の画面を切り替える
import { Link, Navigate, useLocation } from 'react-router'
import { useOps } from './state'

interface Tab {
  to: string
  label: string
  count?: number
}

function PageTabs({ label, tabs }: { label: string; tabs: Tab[] }) {
  const { pathname } = useLocation()
  return (
    <nav className="tabs page-tabs" aria-label={label}>
      {tabs.map((t) => (
        <Link key={t.to} to={t.to} className="tab" aria-current={pathname === t.to ? 'page' : undefined}>
          {t.label}
          {t.count !== undefined && <span className="n">{t.count}</span>}
        </Link>
      ))}
    </nav>
  )
}

export const QUEUE_PATHS = ['/ops/pii', '/ops/review', '/ops/escalations'] as const

/** 対応待ち（個人情報の確認・分類の確認・エスカレーション）。 */
export function QueueTabs() {
  const counts = useOps().overview?.counts
  return (
    <PageTabs
      label="対応待ち"
      tabs={[
        { to: '/ops/pii', label: '個人情報の確認', count: counts?.pii_review },
        { to: '/ops/review', label: '分類の確認', count: counts?.review },
        { to: '/ops/escalations', label: 'エスカレーション', count: counts?.escalated },
      ]}
    />
  )
}

/** 対応待ちを開く。件のあるいちばん手前の画面にする。 */
export function WaitingRedirect() {
  const counts = useOps().overview?.counts
  if (!counts) return null
  const to = counts.pii_review ? '/ops/pii' : counts.review ? '/ops/review' : '/ops/escalations'
  return <Navigate replace to={to} />
}

export const SETTINGS_PATHS = ['/ops/settings', '/ops/staff', '/ops/settings/slack'] as const

export function SettingsTabs() {
  return (
    <PageTabs
      label="設定"
      tabs={[
        { to: '/ops/settings', label: '分類' },
        { to: '/ops/staff', label: '担当者' },
        { to: '/ops/settings/slack', label: 'Slack' },
      ]}
    />
  )
}
