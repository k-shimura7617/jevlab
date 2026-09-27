import { useEffect } from 'react'
import { Link, useLocation } from 'react-router'
import { Page, useTitle } from '../../shell'
import { useOps } from '../state'
import { FlowSection, KevDownBanner } from './OpsHome'

/** 運用の入口。人の対応が必要な件の数と、片づける画面へのリンクだけを出す。 */
export function OpsHome() {
  useTitle('運用')
  const { overview, overviewError } = useOps()
  const c = overview?.counts
  // 取り込みから移ってきたときは、取り込んだ件数を処理フローの進み具合に使い、処理フローまで送る
  const { hash, state } = useLocation()
  const imported = typeof state === 'object' && state !== null && 'imported' in state && typeof state.imported === 'number' ? state.imported : null
  const flowShown = overview?.flow !== undefined
  useEffect(() => {
    if (hash === '#flow' && flowShown) document.getElementById('flow')?.scrollIntoView({ block: 'start' })
  }, [hash, flowShown])
  const rows = c
    ? [
        { label: '個人情報の確認', n: c.pii_review, to: '/ops/pii' },
        { label: '分類の確認', n: c.review, to: '/ops/review' },
        { label: 'エスカレーション', n: c.escalated, to: '/ops/escalations' },
        { label: '担当に回した問い合わせ（未完了）', n: c.routed, to: '/ops/inbox' },
        { label: 'エラー', n: c.error, to: '/ops/inbox' },
      ].filter((r) => r.n > 0)
    : []
  const total = rows.reduce((s, r) => s + r.n, 0)
  return (
    <Page wide crumbs={[{ label: '運用' }]}>
      <KevDownBanner />
      {overviewError && <div className="error">状態を取得できません: {overviewError}</div>}
      {c && (
        <section className="panel home" data-testid="home">
          <h1>{total ? `対応が必要な問い合わせ ${total} 件` : 'すべて対応済みです'}</h1>
          <ul className="home-rows">
            {rows.map((r) => (
              <li key={r.label}>
                <Link to={r.to}>
                  <span>{r.label}</span>
                  <strong>{r.n}</strong>
                </Link>
              </li>
            ))}
          </ul>
          <Link className="small" to="/ops/inbox">
            受付箱を開く
          </Link>
        </section>
      )}
      <FlowSection imported={imported} />
    </Page>
  )
}
