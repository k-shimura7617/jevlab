import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { errorMessage } from '../../api'
import { pct } from '../../format'
import { Page, useTitle } from '../../shell'
import { ops, type Item, type PiiAction, type PiiType, type Span } from '../api'
import { BulkBar, CheckRow, Empty, ItemRow } from '../components'
import { ACTION_LABELS, PII_FLAG_LABELS, PII_FLAG_NOTES, PII_OUT_OF_SCOPE, PII_TYPE_EXAMPLES, safeToBulk } from '../format'
import { SelectableText } from '../SelectableText'
import { useOps, usePolling } from '../state'

const PII_ORDER: PiiType[] = ['person_name', 'phone', 'email', 'sns_account', 'postal_code', 'address', 'card', 'bank_account', 'birthday']

/** サーバの apply_mask と同じ置き換え（確認画面のプレビュー用）。 */
function maskPreview(text: string, spans: Span[], policy: Record<PiiType, PiiAction>, labels: Record<string, string>): string {
  const targets = spans.filter((s) => s.confirmed && policy[s.type] === 'mask').sort((a, b) => a.start - b.start)
  let out = ''
  let pos = 0
  for (const s of targets) {
    if (s.start < pos) continue
    out += `${text.slice(pos, s.start)}【${labels[s.type] ?? s.type}】`
    pos = s.end
  }
  return out + text.slice(pos)
}

type Enqueue = <T>(f: () => Promise<T>) => Promise<T>

function Editor({ item, onDone, enqueue }: { item: Item; onDone: (id: string) => void; enqueue: Enqueue }) {
  const { meta, settings } = useOps()
  // 編集はサーバに下書きとして保存する（画面を移っても残り、左のチェックでまとめて処理するときも使われる）
  const [spans, setSpansState] = useState<Span[]>(item.pii_draft ?? item.pii)
  const [draft, setDraft] = useState<'saving' | 'saved' | null>(item.pii_draft ? 'saved' : null)
  const setSpans = (f: (prev: Span[]) => Span[]) => {
    const next = f(spans)
    setSpansState(next)
    const same = JSON.stringify(next) === JSON.stringify(item.pii)
    setDraft('saving')
    setSaveError(null)
    // 保存は順番どおりに送る（速く続けて操作しても、古い内容が後から上書きしないように）
    enqueue(() => ops.savePiiDraft(item.id, same ? null : next))
      .then(() => setDraft(same ? null : 'saved'))
      .catch((e: unknown) => {
        setDraft(null)
        setSaveError(`編集を保存できません: ${errorMessage(e)}`)
      })
  }
  const [selection, setSelection] = useState<{ start: number; end: number } | null>(null)
  const [addType, setAddType] = useState<PiiType>('person_name')
  const [busy, setBusy] = useState(false)
  // 下書きの保存の失敗は上の知らせる欄に、送信の失敗は操作のボタンのすぐ下に出す
  const [saveError, setSaveError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const labels: Record<string, string> = meta?.pii_types ?? {}
  const policy = settings?.guard.policy
  const overlapsExisting = selection !== null && spans.some((s) => s.start < selection.end && selection.start < s.end)

  const add = () => {
    if (!selection || overlapsExisting) return
    const span: Span = { ...selection, type: addType, text: item.text.slice(selection.start, selection.end), source: 'human', score: null, confirmed: true }
    setSpans((prev) => [...prev, span])
    setSelection(null)
    window.getSelection()?.removeAllRanges()
  }
  const submit = (action: 'continue' | 'block') => {
    setBusy(true)
    setSubmitError(null)
    // 成功したら一覧から外れるまで押せないままにする（二重送信を防ぐ）
    ops
      .submitPii(item.id, spans, action)
      .then(() => onDone(item.id))
      .catch((e: unknown) => {
        setSubmitError(errorMessage(e))
        setBusy(false)
      })
  }
  const confirmed = spans.filter((s) => s.confirmed)
  const blocked = policy ? [...new Set(confirmed.filter((s) => policy[s.type] === 'block').map((s) => labels[s.type] ?? s.type))] : []
  const masking = policy ? confirmed.filter((s) => policy[s.type] === 'mask').length : 0
  return (
    <article className="pii-editor" data-testid="pii-editor">
      <header>
        <div className="row">
          <span className="muted">{item.id}</span>
          <strong>{item.subject || '（件名なし）'}</strong>
          <span className="muted small">
            {item.from_name} {item.from_address && `<${item.from_address}>`}
          </span>
          <Link className="small" to={`/ops/inbox?id=${item.id}`}>
            受付箱で見る
          </Link>
        </div>
      </header>
      <p className="muted small">
        色付き＝個人情報（クリックで外す）。漏れは選択して追加
      </p>
      {/* 知らせる欄は高さを固定し、中でスクロールさせる（チェックの切り替えや件の移動で画面の位置がずれないように） */}
      <div className="pii-messages" aria-live="polite" data-testid="pii-messages">
        {item.pii_leftover !== null && item.pii_leftover >= (settings?.guard.leftover_threshold ?? 0.5) && (
          <div className="warn-box">
            候補外に残っているかも（{pct(item.pii_leftover, 0)}）
          </div>
        )}
        {draft && (
          <div className="draft-bar small" data-testid="pii-draft">
            <span>{draft === 'saving' ? '編集を保存中…' : '編集を保存しました'}</span>
            <button type="button" className="link-btn" disabled={draft === 'saving' || busy} onClick={() => setSpans(() => item.pii)}>
              編集を取り消す
            </button>
          </div>
        )}
        {saveError && (
          <div className="error small" role="alert">
            {saveError}
          </div>
        )}
      </div>
      <SelectableText
        text={item.text}
        spans={spans}
        labels={labels}
        onToggle={(i) => setSpans((prev) => prev.map((s, j) => (j === i ? { ...s, confirmed: !s.confirmed } : s)))}
        onSelect={setSelection}
      />
      <div className={`add-bar${selection ? ' on' : ''}`} data-testid="pii-add">
        {selection ? (
          <>
            <span>
              選択中: <strong>「{item.text.slice(selection.start, selection.end)}」</strong>
            </span>
            <select aria-label="個人情報の種類" value={addType} onChange={(e) => setAddType(e.target.value as PiiType)}>
              {PII_ORDER.map((t) => (
                <option key={t} value={t}>
                  {labels[t] ?? t}
                </option>
              ))}
            </select>
            <button type="button" onClick={add} disabled={overlapsExisting}>
              個人情報として追加
            </button>
            {overlapsExisting && <span className="error small">既存の箇所と重なっています</span>}
          </>
        ) : (
          <span className="muted small">本文を選択して追加</span>
        )}
      </div>

      <div className="pii-table scroll">
        <table>
          <thead>
            <tr>
              <th>箇所</th>
              <th>種類</th>
              <th className="num">判定</th>
              <th>方針</th>
              <th>扱い</th>
            </tr>
          </thead>
          <tbody>
            {spans.map((s, i) => (
              <tr key={`${s.start}-${s.end}`} className={s.confirmed ? undefined : 'off'}>
                <td className="pii-where">「{s.text}」</td>
                <td>
                  <select
                    aria-label={`「${s.text}」の種類`}
                    value={s.type}
                    onChange={(e) => setSpans((prev) => prev.map((x, j) => (j === i ? { ...x, type: e.target.value as PiiType } : x)))}
                  >
                    {PII_ORDER.map((t) => (
                      <option key={t} value={t}>
                        {labels[t] ?? t}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="num nowrap">{s.score !== null ? s.score.toFixed(2) : s.source === 'human' ? '人が追加' : '規則で確定'}</td>
                <td>{policy ? <span className={`act act-${policy[s.type]}`}>{ACTION_LABELS[policy[s.type]]}</span> : '-'}</td>
                <td className="nowrap">
                  <label className="small">
                    <input
                      type="checkbox"
                      checked={s.confirmed}
                      onChange={() => setSpans((prev) => prev.map((x, j) => (j === i ? { ...x, confirmed: !x.confirmed } : x)))}
                    />{' '}
                    個人情報
                  </label>
                  {s.source === 'human' && (
                    <button type="button" className="link-btn" onClick={() => setSpans((prev) => prev.filter((_, j) => j !== i))}>
                      削除
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {spans.length === 0 && (
              <tr>
                <td colSpan={5} className="muted">
                  規則で拾った候補はありません
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {policy && (
        <>
          <h3>Jev に送る本文</h3>
          {/* プレビューも高さを固定して中でスクロールさせる */}
          <div className="pii-preview-box">
            {blocked.length > 0 ? (
              <div className="warn-box">
                {blocked.join('・')}はブロックの方針のため、この件は Jev に送りません（
                {settings?.guard.blocked_route === 'kev' ? 'Kev だけで仕分けます' : '人に回します'}）。
              </div>
            ) : (
              <div className="fulltext preview" data-testid="pii-preview">
                {maskPreview(item.text, spans, policy, labels)}
              </div>
            )}
          </div>
        </>
      )}
      <div className="row sticky-actions">
        {blocked.length > 0 ? (
          <button type="button" className="danger" disabled={busy || draft === 'saving'} onClick={() => submit('continue')}>
            ブロック（Jev に送らない）
          </button>
        ) : (
          <>
            <button type="button" disabled={busy || draft === 'saving'} onClick={() => submit('continue')}>
              {masking ? `マスクして仕分け（${masking} 箇所）` : 'そのまま仕分け'}
            </button>
            <button type="button" className="danger" disabled={busy || draft === 'saving'} onClick={() => submit('block')}>
              ブロック（Jev に送らない）
            </button>
          </>
        )}
        <Link className="small" to="/ops/settings#guard">
          方針を変える
        </Link>
      </div>
      {submitError && (
        <div className="error small" role="alert" data-testid="pii-submit-error">
          {submitError}
        </div>
      )}
      <PiiScope labels={labels} policy={policy} />
    </article>
  )
}

/** 何を個人情報として扱うか（種類・方針・例）と、隠さないものの例。 */
function PiiScope({ labels, policy }: { labels: Record<string, string>; policy: Record<PiiType, PiiAction> | undefined }) {
  return (
    <details className="pii-scope" open data-testid="pii-scope">
      <summary>確認の対象（何を個人情報として扱うか）</summary>
      <div className="pii-scope-body">
        <div>
          <h4>対象（隠す・止める）</h4>
          <ul>
            {(Object.keys(labels) as PiiType[]).map((t) => (
              <li key={t}>
                <strong>{labels[t]}</strong>
                {policy && <span className={`act act-${policy[t]}`}>{ACTION_LABELS[policy[t]]}</span>}
                {PII_TYPE_EXAMPLES[t] && <span className="muted small">例: {PII_TYPE_EXAMPLES[t]}</span>}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h4>対象外（隠さない）</h4>
          <ul>
            {PII_OUT_OF_SCOPE.map((x) => (
              <li key={x.label}>
                <strong>{x.label}</strong>
                <span className="muted small">例: {x.example}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <p className="note small">迷ったら個人情報として扱う</p>
    </details>
  )
}

/** まとめて処理するときに使われる個人情報の扱い（編集があればその内容、なければ候補をすべて個人情報とみなす）。 */
const effectiveSpans = (i: Item): Span[] => i.pii_draft ?? i.pii.map((s) => ({ ...s, confirmed: true }))

function FlagTags({ item }: { item: Item }) {
  return (
    <>
      {item.pii_draft && (
        <span className="pii-flag-tag pf-draft" title="編集した内容で処理します">
          編集あり
        </span>
      )}
      {item.pii_flags.map((f) => (
        <span key={f} className={`pii-flag-tag pf-${f}`} title={PII_FLAG_NOTES[f]}>
          {PII_FLAG_LABELS[f]}
        </span>
      ))}
    </>
  )
}

/** 一括で流す件の選択。新しく届いた件のうち、迷いのない件（検出済みだけ）は最初から選んでおく。 */
function useChecked(items: Item[]): [Set<string>, (id: string, on: boolean) => void, (ids: string[]) => void] {
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const seen = useRef<Set<string>>(new Set())
  useEffect(() => {
    const fresh = items.filter((i) => !seen.current.has(i.id))
    if (!fresh.length) return
    fresh.forEach((i) => seen.current.add(i.id))
    const auto = fresh.filter(safeToBulk).map((i) => i.id)
    if (auto.length) setChecked((prev) => new Set([...prev, ...auto]))
  }, [items])
  const toggle = (id: string, on: boolean) =>
    setChecked((prev) => {
      const next = new Set(prev)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })
  return [checked, toggle, (ids) => setChecked(new Set(ids))]
}

export function PiiReview() {
  useTitle('個人情報の確認')
  const { meta, settings, refresh } = useOps()
  const [params, setParams] = useSearchParams()
  const list = usePolling(() => ops.items(['pii_review']))
  const items = useMemo(() => [...(list.data ?? [])].sort((a, b) => a.seq - b.seq), [list.data])
  const wanted = params.get('id')
  const current = items.find((i) => i.id === wanted) ?? items[0]
  const select = (id: string) => setParams({ id }, { replace: true })
  const { reload } = list
  const [checked, toggle, setAll] = useChecked(items)
  const selected = items.filter((i) => checked.has(i.id))
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkMsg, setBulkMsg] = useState<string | null>(null)
  const [bulkError, setBulkError] = useState<string | null>(null)
  // 下書きの保存を 1 本の列にする（順番を守り、まとめて処理の前に保存の完了を待てるように）
  const saves = useRef<Promise<unknown>>(Promise.resolve())
  const enqueue = useCallback<Enqueue>((f) => {
    const p = saves.current.then(f, f)
    saves.current = p.catch(() => undefined)
    return p
  }, [])
  useEffect(() => {
    if (current && current.id !== wanted) setParams({ id: current.id }, { replace: true })
  }, [current, wanted, setParams])
  useEffect(() => {
    if (!bulkMsg) return
    const timer = setTimeout(() => setBulkMsg(null), 6000)
    return () => clearTimeout(timer)
  }, [bulkMsg])
  const policy = settings?.guard.policy
  const willBlock = (i: Item) => (policy ? effectiveSpans(i).some((s) => s.confirmed && policy[s.type] === 'block') : false)
  const blockSelected = selected.filter(willBlock).length
  const runBulk = () => {
    const targets = selected
    // 人が編集した件はその判断で処理するので、迷いのある件として数えない
    // 見落としがあるかもしれない件（人が編集していないもの）。未確定の候補はまとめて処理でもマスクするので数えない
    const missed = targets.filter((i) => i.pii_draft === null && i.pii_flags.includes('possible_missed')).length
    const blocking = new Set(targets.filter(willBlock).map((i) => i.id))
    const ask =
      (missed ? `うち ${missed} 件は見落としがあるかもしれません。\n` : '') +
      `選んだ ${targets.length} 件を送りますか？` +
      (blocking.size ? `（うち ${blocking.size} 件はブロックして送りません）` : '')
    if (!window.confirm(ask)) return
    setBulkBusy(true)
    setBulkError(null)
    // 右の画面での編集（下書き）の保存が終わってから処理する
    saves.current
      .then(() => ops.bulkPii(targets.map((i) => i.id)))
      .then((r) => {
        const blocked = r.done.filter((id) => blocking.has(id)).length
        setBulkMsg(
          blocked
            ? `${r.done.length} 件を処理しました（マスクして仕分け ${r.done.length - blocked} 件・ブロック ${blocked} 件）`
            : `${r.done.length} 件をマスクして仕分けしました`,
        )
        const failed = Object.entries(r.errors)
        if (failed.length) setBulkError(failed.map(([id, m]) => `${id}: ${m}`).join(' ／ '))
        // 失敗した件は選んだままにする
        setAll(Object.keys(r.errors))
      })
      .catch((e: unknown) => setBulkError(errorMessage(e)))
      .finally(() => {
        setBulkBusy(false)
        reload()
        refresh()
      })
  }
  return (
    <Page wide crumbs={[{ label: '運用', to: '/ops' }, { label: '個人情報の確認' }]}>
      <div className="panel-head">
        <h1>個人情報の確認</h1>
        <span className="muted small">確認してから Jev に送る</span>
      </div>
      <div className="queue-layout">
        <section className="panel list-pane">
          <h2>
            個人情報の確認待ち <span className="n">{items.length}</span>
          </h2>
          <p className="muted small">
            「検出済み」は最初から選択。ブロック対象は送らない
            <br />
            「編集あり」は編集した内容で処理
          </p>
          <BulkBar count={selected.length} total={items.length} onAll={() => setAll(items.map((i) => i.id))} onNone={() => setAll([])}>
            <button type="button" disabled={bulkBusy || !selected.length} onClick={runBulk}>
              {blockSelected
                ? `選んだ ${selected.length} 件を処理（マスクして仕分け ${selected.length - blockSelected}・ブロック ${blockSelected}）`
                : `選んだ ${selected.length} 件をマスクして仕分け`}
            </button>
          </BulkBar>
          {bulkMsg && <div className="done-box small">{bulkMsg}</div>}
          {bulkError && (
            <div className="error small" role="alert">
              {bulkError}
            </div>
          )}
          {list.error && <div className="error small">{list.error}</div>}
          <div className="item-list" data-testid="pii-list">
            {items.map((i) => (
              <CheckRow key={i.id} checked={checked.has(i.id)} onCheck={(v) => toggle(i.id, v)} label={`${i.id}「${i.subject || i.body.slice(0, 20)}」を一括の対象にする`}>
                <button type="button" className="row-button" aria-current={i.id === current?.id ? 'true' : undefined} onClick={() => select(i.id)}>
                  <ItemRow item={i} meta={meta} active={i.id === current?.id} plain extra={<FlagTags item={i} />} />
                </button>
              </CheckRow>
            ))}
            {list.data && items.length === 0 && <Empty>確認待ちの件はありません</Empty>}
          </div>
        </section>
        <section className="panel detail-pane">
          {!list.data ? (
            <Empty>読み込み中…</Empty>
          ) : current ? (
            <Editor
              key={current.id}
              item={current}
              enqueue={enqueue}
              onDone={(id) => {
                const next = items[items.findIndex((i) => i.id === id) + 1]
                if (next) select(next.id)
                toggle(id, false)
                reload()
                refresh()
              }}
            />
          ) : (
            <Empty>確認待ちはありません</Empty>
          )}
        </section>
      </div>
    </Page>
  )
}
