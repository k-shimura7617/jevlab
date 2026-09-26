// 1 件の詳細（受付箱の右側と、件の詳細ページで使う）
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { errorMessage, type QuestionInfo } from '../api'
import { ProbabilityRows } from '../components/report'
import { pct } from '../format'
import { ops, type Item, type Meta, type PiiType, type Status } from './api'
import { CategoryTag, costText, FieldsList, PiiText, PriorityBadge, StatusChip, Timeline } from './components'
import { CHANNEL_SHORT, PRIORITY_LABELS, titleOf } from './format'
import { canMail, replyHref } from './mail'
import { SelectableText } from './SelectableText'
import { POLL_MS, useOps, usePolling } from './state'

const SUB_QUESTIONS: Record<string, Omit<QuestionInfo, 'id'>> = {
  frustration: { type: 'score', title: '不満度', instructions: '', options: { '0': 'なし', '1': '不満', '2': '強い不満' } },
  urgent: { type: 'noul', title: '緊急', instructions: '', options: { true: '緊急', false: '通常' } },
  refund: { type: 'score', title: '返金・補償の要求', instructions: '', options: { '0': 'なし', '1': '検討・質問', '2': 'はっきり要求' } },
  publicity: { type: 'noul', title: '公になる恐れ', instructions: '', options: { true: 'あり', false: 'なし' } },
}

function Judgement({ item, meta }: { item: Item; meta: Meta | null }) {
  const cat = item.answers.category
  if (!cat || !meta) return <p className="muted small">まだ仕分けていません</p>
  const q: QuestionInfo = { id: 'category', type: 'choice', title: '分類', instructions: '', options: meta.categories }
  return (
    <div className="judgement">
      <div>
        <h4>分類</h4>
        <ProbabilityRows q={q} a={cat} />
      </div>
      <div>
        <h4>チケット項目</h4>
        <FieldsList fields={item.fields} meta={meta} />
      </div>
      <div className="sub-answers">
        {Object.entries(SUB_QUESTIONS).map(([id, info]) => {
          const a = item.answers[id]
          if (!a) return null
          return (
            <div key={id}>
              <h4>
                {info.title}
                <span className="muted small">（値 {(a.value ?? 0).toFixed(2)}）</span>
              </h4>
              <ProbabilityRows q={{ id, ...info }} a={a} />
            </div>
          )
        })}
      </div>
    </div>
  )
}

function QuickActions({
  item,
  meta,
  onDone,
  here,
}: {
  item: Item
  meta: Meta | null
  onDone: () => void
  here?: 'pii' | 'escalations'
}) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const run = (f: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    f()
      .then(onDone)
      .catch((e: unknown) => setError(errorMessage(e)))
      .finally(() => setBusy(false))
  }
  return (
    <div className="quick-actions">
      {item.status === 'pii_review' && here !== 'pii' && (
        <Link className="btn" to={`/ops/pii?id=${item.id}`}>
          個人情報を確認する
        </Link>
      )}
      {item.status === 'review' && meta && (
        <>
          <span className="muted small">分類を確定:</span>
          {Object.entries(meta.categories).map(([k, label]) => (
            <button key={k} type="button" className={k === item.category ? undefined : 'secondary'} disabled={busy} onClick={() => run(() => ops.decide(item.id, k))}>
              {label}
            </button>
          ))}
        </>
      )}
      {item.status === 'routed' && item.audit && item.audit_result === null && meta && (
        <>
          <span className="muted small">抜き取り確認（正しい分類は？）:</span>
          {Object.entries(meta.categories).map(([k, label]) => (
            <button key={k} type="button" className={k === item.category ? undefined : 'secondary'} disabled={busy} onClick={() => run(() => ops.decide(item.id, k))}>
              {label}
            </button>
          ))}
        </>
      )}
      {item.status === 'routed' && <RoutedAssignee item={item} busy={busy} run={run} />}
      {item.status === 'routed' && meta && <CloseRouted key={`${item.id}:${item.category ?? ''}`} item={item} meta={meta} busy={busy} run={run} />}
      {item.status === 'escalated' && here !== 'escalations' && (
        <Link className="btn" to={`/ops/escalations?id=${item.id}`}>
          エスカレーションで対応する
        </Link>
      )}
      {item.sent_text !== null && item.status !== 'queued' && item.status !== 'processing' && (
        // 問い合わせ（個人情報を伏せて Jev に送った本文）を入れて、返信前チェックを開く
        <Link className="btn secondary" to={`/tools/reply?item=${encodeURIComponent(item.id)}`}>
          返信内容の検討
        </Link>
      )}
      {canMail(item) && (
        // jevlab はメールを送らない。お使いのメールソフトを、宛先と件名を入れた状態で開く
        <a className="btn secondary" href={replyHref(item)} target="_blank" rel="noopener noreferrer">
          メールで返信
        </a>
      )}
      {item.status === 'error' && (
        <button type="button" disabled={busy} onClick={() => run(() => ops.retry(item.id))}>
          再実行
        </button>
      )}
      {error && <span className="error small">{error}</span>}
    </div>
  )
}

/**
 * 振り分け済みの件の対応完了。最終の分類は、いまの分類を最初から選んでおく。
 * 触らずに完了すれば「分類は合っていた」、切り替えれば「修正した」として記録し、閾値の調整の正解に使う。
 */
/** 振り分け済みの件の担当（自動で割り当てた担当を人が変える）。 */
function RoutedAssignee({ item, busy, run }: { item: Item; busy: boolean; run: (f: () => Promise<unknown>) => void }) {
  const { settings } = useOps()
  return (
    <div className="row">
      <label className="muted small" htmlFor={`routed-assignee-${item.id}`}>
        担当者
      </label>
      <select id={`routed-assignee-${item.id}`} value={item.assignee ?? ''} disabled={busy} onChange={(e) => run(() => ops.assign(item.id, e.target.value))}>
        <option value="">未割り当て</option>
        {(settings?.staff ?? [])
          .filter((s) => s.active || s.id === item.assignee)
          .map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
      </select>
      {item.assign_provisional && item.assigned_by === 'auto' && <span className="muted small">仮で割り当て</span>}
    </div>
  )
}

function CloseRouted({ item, meta, busy, run }: { item: Item; meta: Meta; busy: boolean; run: (f: () => Promise<unknown>) => void }) {
  const [category, setCategory] = useState(item.category ?? '')
  return (
    <div className="row" data-testid="close-routed">
      <label className="muted small" htmlFor={`close-category-${item.id}`}>
        最終の分類
      </label>
      <select id={`close-category-${item.id}`} value={category} onChange={(e) => setCategory(e.target.value)}>
        {item.category === null && <option value="">（未分類）</option>}
        {Object.entries(meta.categories).map(([k, label]) => (
          <option key={k} value={k}>
            {label}
          </option>
        ))}
      </select>
      <button type="button" disabled={busy || !category} onClick={() => run(() => ops.close(item.id, category || null))}>
        対応完了にする
      </button>
    </div>
  )
}

// Jev に送った後の件。ガードレールの見逃しはここから報告する
const MISS_STATUSES: Status[] = ['review', 'escalated', 'routed', 'closed']

/** 検知漏れの報告。送った本文は取り消せないので、改善（閾値の調整）のための記録だけ残す。 */
function MissReport({ item, labels, onDone }: { item: Item; labels: Record<string, string>; onDone: () => void }) {
  const types = Object.keys(labels) as PiiType[]
  const [range, setRange] = useState<{ start: number; end: number } | null>(null)
  const [type, setType] = useState<PiiType | ''>('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const submit = () => {
    if (!range || !type) return
    setBusy(true)
    setError(null)
    setMsg(null)
    ops
      .reportMiss(item.id, type, range.start, range.end)
      .then(() => {
        setMsg('報告しました')
        setRange(null)
        window.getSelection()?.removeAllRanges()
        onDone()
      })
      .catch((e: unknown) => setError(errorMessage(e)))
      .finally(() => setBusy(false))
  }
  return (
    <details className="miss-report" data-testid="miss-report">
      <summary>検知漏れを報告</summary>
      <p className="note small">送信済みのため取り消せません。今後の改善に使います</p>
      <SelectableText
        text={item.text}
        spans={item.pii.filter((s) => s.confirmed)}
        labels={labels}
        onSelect={(r) => {
          setRange(r)
          setMsg(null)
        }}
        testId="miss-text"
      />
      <div className="row">
        {range ? (
          <span className="small">「{item.text.slice(range.start, range.end)}」</span>
        ) : (
          <span className="muted small">本文で見逃した箇所を選択</span>
        )}
        <select aria-label="種類" value={type} onChange={(e) => setType(e.target.value as PiiType | '')}>
          <option value="">種類を選ぶ</option>
          {types.map((t) => (
            <option key={t} value={t}>
              {labels[t]}
            </option>
          ))}
        </select>
        <button type="button" disabled={!range || !type || busy} onClick={submit}>
          報告
        </button>
        {msg && <span className="ok small">{msg}</span>}
        {error && <span className="error small">{error}</span>}
      </div>
    </details>
  )
}

export function ItemPanel({ id, onChanged, here }: { id: string; onChanged?: () => void; here?: 'pii' | 'escalations' }) {
  const { meta, settings, refresh } = useOps()
  const detail = usePolling(() => ops.item(id), POLL_MS)
  const [view, setView] = useState<'original' | 'sent'>('original')
  const { reload } = detail
  // 別の件に切り替えたら、次の定期取得を待たずに読み直す
  useEffect(() => reload(), [id, reload])
  if (detail.error && !detail.data) return <div className="error">{detail.error}</div>
  if (!detail.data) return <div className="muted">読み込み中…</div>
  // 別の件に切り替えた直後は、前の件を薄く表示したまま読み込む（画面の高さが跳ねないように）。操作はさせない
  const stale = detail.data.item.id !== id
  const { item, events } = detail.data
  const offline = detail.error && (
    <div className="error small" role="alert">
      最新の状態を取得できません（再接続中）: {detail.error}
    </div>
  )
  const labels: Record<string, string> = meta?.pii_types ?? {}
  const weights = settings?.priority_weights ?? {}
  return (
    <article className={`item-panel${stale ? ' stale' : ''}`} data-testid="item-panel" data-status={item.status} aria-busy={stale}>
      <header>
        <div className="row">
          <span className="muted">{item.id}</span>
          <StatusChip status={item.status} />
          <CategoryTag meta={meta} item={item} />
          <PriorityBadge item={item} weights={weights} />
          {item.backfill && <span className="audit-flag">試算用</span>}
          {item.auto_closed && <span className="audit-flag">自動で完了</span>}
          {item.audit && <span className="audit-flag">抜き取り{item.audit_result ? `（${item.audit_result === 'ok' ? '問題なし' : '修正'}）` : ''}</span>}
        </div>
        <h2>{titleOf(item)}</h2>
        <div className="muted small">
          {CHANNEL_SHORT[item.channel]} ／ {item.from_name || '差出人不明'}
          {item.from_address && ` <${item.from_address}>`} ／ {new Date(item.received_at).toLocaleString('ja-JP')}
          {item.cost_usd > 0 && ` ／ ${costText(item.cost_usd)}`}
        </div>
        {item.reason && <div className="reason">{item.reason}</div>}
        {item.error && <div className="error small">{item.error}</div>}
        {offline}
        {!stale && <QuickActions
          item={item}
          meta={meta}
          here={here}
          onDone={() => {
            detail.reload()
            refresh()
            onChanged?.()
          }}
        />}
      </header>

      <section>
        <div className="panel-head">
          <h3>本文</h3>
          {item.sent_text !== null && item.sent_text !== item.text && (
            <div className="seg" role="group" aria-label="本文の表示">
              <button type="button" className="seg-btn" aria-pressed={view === 'original'} onClick={() => setView('original')}>
                受信した本文
              </button>
              <button type="button" className="seg-btn" aria-pressed={view === 'sent'} onClick={() => setView('sent')}>
                Jev に送った本文
              </button>
            </div>
          )}
        </div>
        <div className="fulltext" data-testid="item-text">
          {view === 'sent' && item.sent_text !== null ? item.sent_text : <PiiText text={item.text} spans={item.pii} labels={labels} />}
        </div>
        {item.pii_decision === 'blocked' && <p className="note">この件は Jev に送っていません（ブロック）。</p>}
        {item.pii_leftover !== null && (
          <p className="muted small">候補以外に個人情報が残っている可能性: {pct(item.pii_leftover, 0)}</p>
        )}
        {!stale && MISS_STATUSES.includes(item.status) && item.sent_text !== null && (
          <MissReport key={item.id} item={item} labels={labels} onDone={reload} />
        )}
      </section>

      <section>
        <h3>判定</h3>
        <Judgement item={item} meta={meta} />
        {Object.keys(item.priority).length > 0 && (
          <p className="muted small">
            優先度の内訳: {Object.entries(item.priority).map(([k, v]) => `${PRIORITY_LABELS[k] ?? k} ${pct(v, 0)}`).join(' ／ ')}
          </p>
        )}
      </section>

      {(item.assignee || item.notes.length > 0) && (
        <section>
          <h3>対応</h3>
          {item.assignee && <p>担当: {item.assignee}</p>}
          {item.notes.map((n, i) => (
            <p key={i} className="note-line">
              {n}
            </p>
          ))}
        </section>
      )}

      <section>
        <h3>経過（監査ログ）</h3>
        <Timeline events={events} />
      </section>

      {item.expected && meta && (
        <details className="expected">
          <summary>デモデータの想定ラベル</summary>
          <dl>
            <dt>分類</dt>
            <dd>{item.expected.category ? (meta.category_labels[item.expected.category] ?? item.expected.category) : '-'}</dd>
            <dt>不満度 / 緊急</dt>
            <dd>
              {item.expected.frustration ?? '-'} / {item.expected.urgent === undefined ? '-' : item.expected.urgent ? '緊急' : '通常'}
            </dd>
            <dt>注文番号</dt>
            <dd>{item.expected.order_id ?? '該当なし'}</dd>
            <dt>個人情報</dt>
            <dd>{item.expected.pii?.length ? item.expected.pii.map((p) => `${labels[p.type] ?? p.type}「${p.text}」`).join('、') : 'なし'}</dd>
          </dl>
        </details>
      )}
    </article>
  )
}
