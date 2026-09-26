import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { errorMessage } from '../../api'
import { Page, useTitle } from '../../shell'
import { ops, type Post } from '../api'
import { Empty } from '../components'
import { clockTime } from '../format'
import { useOps, usePolling } from '../state'

const PAGE = 300

const initialOf = (name: string) => (name.replace(/[（(].*$/, '').trim()[0] ?? '?').toUpperCase()

function Message({ post, fieldTitles }: { post: Post; fieldTitles: Record<string, string> }) {
  const fields = Object.entries(post.fields).filter(([, v]) => v !== null)
  const bot = post.author.startsWith('jevlab') || post.author.startsWith('担当者')
  return (
    <div className={`msg${bot ? ' bot' : ''}`} data-testid="post">
      <span className="avatar" aria-hidden>
        {bot ? '🤖' : initialOf(post.author)}
      </span>
      <div className="msg-body">
        <div className="msg-head">
          <strong>{post.author}</strong>
          {bot && <span className="app-badge">APP</span>}
          <span className="muted small">{clockTime(post.at)}</span>
        </div>
        <div className="msg-text">{post.text}</div>
        {(fields.length > 0 || post.item_id) && (
          <div className="msg-attach">
            {fields.map(([k, v]) => (
              <span key={k} className="kv">
                <span className="muted">{fieldTitles[k] ?? k}</span> {v}
              </span>
            ))}
            {post.item_id && <Link to={`/ops/inbox?id=${encodeURIComponent(post.item_id)}`}>{post.item_id} を開く</Link>}
          </div>
        )}
      </div>
    </div>
  )
}

function Composer({ channel, onSent }: { channel: string; onSent: () => void }) {
  const [name, setName] = useState('森野商店 小林')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const send = () => {
    if (!text.trim()) return
    setBusy(true)
    setError(null)
    ops
      .chat(name.trim() || 'ゲスト', text.trim())
      .then(() => {
        setText('')
        onSent()
      })
      .catch((e: unknown) => setError(errorMessage(e)))
      .finally(() => setBusy(false))
  }
  return (
    <div className="composer">
      <div className="row tight">
        <label className="muted small" htmlFor="chat-name">
          投稿者
        </label>
        <input id="chat-name" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="row tight">
        <textarea
          aria-label={`${channel} へのメッセージ`}
          rows={2}
          value={text}
          placeholder={`${channel} にメッセージを送信（Enter で送信、Shift+Enter で改行）`}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              send()
            }
          }}
        />
        <button type="button" disabled={busy || !text.trim()} onClick={send}>
          送信
        </button>
      </div>
      {error && <div className="error small">{error}</div>}
    </div>
  )
}

export function Channels() {
  useTitle('チャンネル')
  const { meta, refresh } = useOps()
  const [params, setParams] = useSearchParams()
  // 新しい順に limit 件を取る。古い投稿は「さらに読み込む」で増やす
  const [limit, setLimit] = useState(PAGE)
  const posts = usePolling(() => ops.posts(limit))
  const inbound = meta?.inbound_channel ?? '#お問い合わせ窓口'
  const channels = meta ? [inbound, ...Object.values(meta.route_channels), meta.escalation_channel] : [inbound]
  const active = params.get('c') ?? channels[1] ?? inbound
  const all = posts.data ?? []
  const shown = all.filter((p) => p.channel === active).sort((a, b) => a.id - b.id)
  const box = useRef<HTMLDivElement>(null)
  // いちばん下を見ているときだけ、新しい投稿に合わせて下までスクロールする（読み返しているときは動かさない）
  const stick = useRef(true)
  useEffect(() => {
    stick.current = true
  }, [active])
  const last = shown.at(-1)?.id
  useEffect(() => {
    const el = box.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [last, active])
  const onScroll = () => {
    const el = box.current
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }
  // 取った件数が上限に届いていれば、もっと古い投稿がある
  const more = all.length >= limit
  return (
    <Page wide crumbs={[{ label: '運用', to: '/ops' }, { label: 'チャンネル（疑似 Slack）' }]}>
      <div className="panel-head">
        <h1>チャンネル（疑似 Slack）</h1>
        <span className="muted small">振り分けの投稿と、チャットからの受信。</span>
      </div>
      <div className="slack">
        <nav className="slack-side" aria-label="チャンネル">
          <div className="ws">こもれび雑貨店</div>
          <div className="slack-section">受信</div>
          <button type="button" className={active === inbound ? 'ch-btn active' : 'ch-btn'} onClick={() => setParams({ c: inbound })}>
            {inbound}
            <span className="n">{all.filter((p) => p.channel === inbound).length}</span>
          </button>
          <div className="slack-section">振り分け先</div>
          {channels.slice(1).map((c) => (
            <button key={c} type="button" className={active === c ? 'ch-btn active' : 'ch-btn'} onClick={() => setParams({ c })}>
              {c}
              <span className="n">{all.filter((p) => p.channel === c).length}</span>
            </button>
          ))}
        </nav>
        <section className="slack-main">
          <header className="slack-head">
            <strong>{active}</strong>
            <span className="muted small">
              {active === inbound
                ? '書き込むと受付箱に届きます'
                : 'jevlab が仕分けた件の投稿先'}
            </span>
          </header>
          <div className="slack-log" ref={box} onScroll={onScroll} data-testid="slack-log">
            {posts.error && <div className="error small">{posts.error}</div>}
            {more && (
              <button
                type="button"
                className="link-btn load-more"
                onClick={() => {
                  stick.current = false
                  setLimit((n) => n + PAGE)
                }}
              >
                さらに読み込む
              </button>
            )}
            {shown.map((p) => (
              <Message key={p.id} post={p} fieldTitles={meta?.fields ?? {}} />
            ))}
            {posts.data && shown.length === 0 && <Empty>まだ投稿はありません</Empty>}
          </div>
          {active === inbound && (
            <Composer
              channel={inbound}
              onSent={() => {
                posts.reload()
                refresh()
              }}
            />
          )}
        </section>
      </div>
    </Page>
  )
}
