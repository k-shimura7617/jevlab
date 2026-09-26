import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router'
import { errorMessage } from '../../api'
import { Page, useTitle } from '../../shell'
import { ops, type Channel, type Item, type Status } from '../api'
import { Empty, ItemRow } from '../components'
import { CHANNEL_SHORT } from '../format'
import { ItemPanel } from '../ItemPanel'
import { useOps, usePolling } from '../state'

type Tab = 'all' | 'active' | 'human' | 'done' | 'error'
const TABS: { id: Tab; label: string; statuses: Status[] | null }[] = [
  { id: 'all', label: 'すべて', statuses: null },
  { id: 'active', label: '処理中', statuses: ['queued', 'processing'] },
  { id: 'human', label: '人の対応待ち', statuses: ['pii_review', 'review', 'escalated'] },
  { id: 'done', label: '振り分け済み・完了', statuses: ['routed', 'closed'] },
  { id: 'error', label: 'エラー', statuses: ['error'] },
]

function Compose({ onClose, onSent }: { onClose: () => void; onSent: (item: Item) => void }) {
  const [channel, setChannel] = useState<Extract<Channel, 'mail' | 'api'>>('mail')
  const [fromName, setFromName] = useState('')
  const [fromAddress, setFromAddress] = useState('')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const dirty = Boolean(body.trim() || subject.trim())
  const close = () => {
    if (!dirty || window.confirm('書きかけのメールを破棄して閉じますか？')) onClose()
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })
  const send = () => {
    if (!body.trim()) {
      setError('本文を入力してください')
      return
    }
    setBusy(true)
    setError(null)
    ops
      .ingest({ channel, from_name: fromName.trim() || 'デモの来場者', from_address: fromAddress.trim(), subject: subject.trim(), body })
      .then(onSent)
      .catch((e: unknown) => setError(errorMessage(e)))
      .finally(() => setBusy(false))
  }
  return (
    <>
      <div className="sheet-backdrop" onClick={close} />
      <aside className="sheet compose" role="dialog" aria-modal="true" aria-label="お客様としてメールを送る">
        <button type="button" className="close" onClick={close} aria-label="閉じる">
          ×
        </button>
        <h2>メールを送る（お客様役）</h2>
        <p className="muted small">
          support@komorebi.example に届いた扱いで処理されます。
        </p>
        <div className="form-grid">
          <label htmlFor="c-channel">受信経路</label>
          <select id="c-channel" value={channel} onChange={(e) => setChannel(e.target.value === 'api' ? 'api' : 'mail')}>
            <option value="mail">メール（support@komorebi.example）</option>
            <option value="api">API（外部システムからの登録）</option>
          </select>
          <label htmlFor="c-name">差出人</label>
          <input id="c-name" autoFocus value={fromName} placeholder="山田 花子" onChange={(e) => setFromName(e.target.value)} />
          <label htmlFor="c-addr">アドレス</label>
          <input id="c-addr" value={fromAddress} placeholder="hanako@example.com" onChange={(e) => setFromAddress(e.target.value)} />
          <label htmlFor="c-subject">件名</label>
          <input id="c-subject" value={subject} placeholder="注文した商品について" onChange={(e) => setSubject(e.target.value)} />
        </div>
        <textarea
          aria-label="本文"
          value={body}
          rows={9}
          placeholder={'例: 注文番号 KM-250926-0001 のマグカップが割れて届きました。明日の誕生日に使いたかったのに…。\n電話は 090-0000-1234 です。'}
          onChange={(e) => setBody(e.target.value)}
        />
        <div className="row">
          <button type="button" onClick={send} disabled={busy}>
            {busy ? '送信中…' : '送信'}
          </button>
          <button type="button" className="secondary" onClick={close}>
            キャンセル
          </button>
        </div>
        {error && <div className="error">{error}</div>}
      </aside>
    </>
  )
}

export function Inbox() {
  useTitle('受付箱')
  const { meta, settings, refresh } = useOps()
  const [params, setParams] = useSearchParams()
  const [tab, setTab] = useState<Tab>('all')
  const [query, setQuery] = useState('')
  const [channel, setChannel] = useState<Channel | 'all'>('all')
  const [composing, setComposing] = useState(false)
  const list = usePolling(() => ops.items())
  const selected = params.get('id')
  const items = list.data ?? []
  const q = query.trim()
  const inTab = (i: Item, t: Tab) => {
    const statuses = TABS.find((x) => x.id === t)?.statuses
    return !statuses || statuses.includes(i.status)
  }
  const visible = items.filter(
    (i) =>
      inTab(i, tab) &&
      (channel === 'all' || i.channel === channel) &&
      (!q || `${i.id} ${i.from_name} ${i.subject} ${i.body}`.includes(q)),
  )
  const select = (id: string | null) => setParams(id ? { id } : {}, { replace: true })
  return (
    <Page wide crumbs={[{ label: '運用', to: '/ops' }, { label: '受付箱' }]}>
      <div className="panel-head">
        <h1>受付箱</h1>
        <button type="button" onClick={() => setComposing(true)}>
          ✉ お客様としてメールを送る
        </button>
      </div>
      <div className="inbox-layout">
        <section className="panel list-pane">
          <div className="tabs" role="tablist">
            {TABS.map((t) => (
              <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className="tab" onClick={() => setTab(t.id)}>
                {t.label}
                <span className="n">{items.filter((i) => inTab(i, t.id)).length}</span>
              </button>
            ))}
          </div>
          <div className="row">
            <input className="search" type="search" placeholder="件名・本文・差出人・ID で検索" value={query} onChange={(e) => setQuery(e.target.value)} />
            <select aria-label="受信経路" value={channel} onChange={(e) => setChannel(e.target.value as Channel | 'all')}>
              <option value="all">すべての経路</option>
              {(Object.keys(CHANNEL_SHORT) as Channel[]).map((c) => (
                <option key={c} value={c}>
                  {CHANNEL_SHORT[c]}
                </option>
              ))}
            </select>
          </div>
          {list.error && <div className="error small">{list.error}</div>}
          <div className="item-list" data-testid="inbox-list">
            {visible.map((i) => (
              <button key={i.id} type="button" className="row-button" aria-current={i.id === selected ? 'true' : undefined} aria-label={`${i.id} ${i.subject || i.body.slice(0, 30)}`} onClick={() => select(i.id)}>
                <ItemRow item={i} meta={meta} weights={settings?.priority_weights} active={i.id === selected} />
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
      {composing && (
        <Compose
          onClose={() => setComposing(false)}
          onSent={(item) => {
            setComposing(false)
            list.reload()
            refresh()
            select(item.id)
          }}
        />
      )}
    </Page>
  )
}
