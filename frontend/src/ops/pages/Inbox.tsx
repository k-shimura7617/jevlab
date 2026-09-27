import { useState } from 'react'
import { Page, useTitle } from '../../shell'
import { ops, type Item, type Status } from '../api'
import { Empty, ItemRow } from '../components'
import { matchesWho, useAssigneeFilter } from '../assigneeFilter'
import { ItemPanel } from '../ItemPanel'
import { useOps, usePolling } from '../state'

type Tab = 'all' | 'active' | 'human' | 'done' | 'error'
const TABS: { id: Tab; label: string; statuses: Status[] | null }[] = [
  { id: 'all', label: 'すべて', statuses: null },
  { id: 'active', label: '処理中', statuses: ['queued', 'processing'] },
  { id: 'human', label: '対応待ち', statuses: ['pii_review', 'review', 'escalated'] },
  { id: 'done', label: '振り分け済み・完了', statuses: ['routed', 'closed'] },
  { id: 'error', label: 'エラー', statuses: ['error'] },
]

export function Inbox() {
  useTitle('受付箱')
  const { meta, settings } = useOps()
  const [tab, setTab] = useState<Tab>('all')
  const [query, setQuery] = useState('')
  // 完了した件は既定で隠す（人の対応が要る件を見つけやすくするため）
  const [showClosed, setShowClosed] = useState(false)
  const list = usePolling(() => ops.items())
  const { id: selected, who, select, setWho } = useAssigneeFilter(list.data)
  const items = list.data ?? []
  const staff = settings?.staff ?? []
  const q = query.trim()
  const inTab = (i: Item, t: Tab) => {
    // 完了した件は「完了を表示」を付けたときだけ出す（開いている件も同じ。詳細は右に出したままにする）
    if (!showClosed && i.status === 'closed') return false
    const statuses = TABS.find((x) => x.id === t)?.statuses
    return !statuses || statuses.includes(i.status)
  }
  const visible = items.filter(
    (i) =>
      inTab(i, tab) &&
      matchesWho(i, who) &&
      (!q || `${i.id} ${i.from_name} ${i.subject} ${i.body}`.includes(q)),
  )
  return (
    <Page wide crumbs={[{ label: '運用', to: '/ops' }, { label: '受付箱' }]}>
      <div className="panel-head">
        <h1>受付箱</h1>
      </div>
      <div className="inbox-layout">
        <section className="panel list-pane">
          <div className="tabs" role="tablist">
            {TABS.map((t) => (
              <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className="tab" onClick={() => setTab(t.id)}>
                {t.id === 'done' && !showClosed ? '振り分け済み' : t.label}
                <span className="n">{items.filter((i) => inTab(i, t.id)).length}</span>
              </button>
            ))}
          </div>
          <div className="row">
            <input className="search" type="search" placeholder="件名・本文・差出人・ID で検索" value={query} onChange={(e) => setQuery(e.target.value)} />
            <select aria-label="担当者" value={who} onChange={(e) => setWho(e.target.value)}>
              <option value="all">全員</option>
              <option value="none">未割り当て</option>
              {staff.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
            <label className="small">
              <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} /> 完了を表示
            </label>
          </div>
          {list.error && <div className="error small">{list.error}</div>}
          <div className="item-list" data-testid="inbox-list">
            {visible.map((i) => (
              <button key={i.id} type="button" className="row-button" aria-current={i.id === selected ? 'true' : undefined} aria-label={`${i.id} ${i.subject || i.body.slice(0, 30)}`} onClick={() => select(i.id)}>
                <ItemRow item={i} meta={meta} active={i.id === selected} />
              </button>
            ))}
            {list.data && visible.length === 0 && (
              <Empty>
                {items.length === 0 ? 'まだ何も届いていません' : '該当する件はありません'}
              </Empty>
            )}
          </div>
        </section>
        <section className="panel detail-pane">
          {selected ? <ItemPanel id={selected} /> : <Empty>左の一覧から件を選んでください。</Empty>}
        </section>
      </div>
    </Page>
  )
}
