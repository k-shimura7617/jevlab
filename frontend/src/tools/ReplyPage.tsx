import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { errorMessage } from '../api'
import { pct, TARGET_SHORT, usd } from '../format'
import { ops, type Item } from '../ops/api'
import { canMail, replyHref } from '../ops/mail'
import { Page, useShell, useTitle } from '../shell'
import { tools, type Level, type ReplyCheck, type ReplyMeta, type ReplyResult, suggestReplyStream, type SuggestDone } from './api'
import { PiiCheckButton } from './PiiCheck'
import { Hint } from '../components/hint'
import { useElapsed } from './common'
import { Sentences } from './Sentences'

const SAMPLE = {
  inquiry: '先週注文したマグカップが割れて届きました。明日のプレゼントに使いたかったのに残念です。交換はできますか？',
  draft: '承知しました。すぐに新しいものを明日までにお届けします。送料もこちらで負担します。',
}
const LEVEL_TAG: Record<Level, string> = { ok: '問題なし', warn: '注意', bad: '要見直し' }
const VERDICT_LABELS = { ok: 'OK', review: '見直し推奨', caution: '要注意' } as const

function CheckCards({ result }: { result: ReplyResult }) {
  return (
    <>
      <div className="aspects">
        {result.checks.map((c) => (
          <div key={c.id} className={`aspect lv-${c.level}`} data-testid="reply-check">
            <div className="aspect-head">
              <span>{c.title}</span>
              <span className={`lv-tag lv-${c.level}`}>{c.id === 'apology' ? c.note : LEVEL_TAG[c.level]}</span>
            </div>
            <div className="meter">
              <span style={{ width: `${Math.round(c.badness * 100)}%` }} />
            </div>
            <div className="muted small">{c.polarity === 'good' && c.id !== 'apology' ? `できていない確率 ${pct(c.badness, 0)}` : c.id === 'apology' ? `適切でない確率 ${pct(c.badness, 0)}` : `当てはまる確率 ${pct(c.value, 0)}`}</div>
          </div>
        ))}
      </div>
      <h3>足りない情報</h3>
      {result.missing.length ? (
        <div className="row" data-testid="reply-missing">
          {result.missing.map((m) => (
            <span key={m.key} className="flag-chip" title={`確率 ${pct(m.probability, 0)}`}>
              {m.label}
            </span>
          ))}
        </div>
      ) : (
        <p className="muted small">なし</p>
      )}
    </>
  )
}

/** 指摘を Claude に渡す形にする。 */
const findingsOf = (r: ReplyResult) => [
  ...r.checks.filter((c) => c.level !== 'ok').map((c) => ({ title: c.title, detail: c.note })),
  ...r.missing.map((m) => ({ title: '足りない情報', detail: m.label })),
]

function Compare({ before, after }: { before: ReplyResult; after: ReplyResult }) {
  return (
    <table className="compare" data-testid="reply-compare">
      <thead>
        <tr>
          <th>観点</th>
          <th>下書き</th>
          <th>AI返信案</th>
        </tr>
      </thead>
      <tbody>
        {before.checks.map((b) => {
          const a: ReplyCheck | undefined = after.checks.find((x) => x.id === b.id)
          return (
            <tr key={b.id}>
              <td>{b.title}</td>
              <td>
                <span className={`lv-tag lv-${b.level}`}>{b.id === 'apology' ? b.note : LEVEL_TAG[b.level]}</span>
              </td>
              <td>{a && <span className={`lv-tag lv-${a.level}`}>{a.id === 'apology' ? a.note : LEVEL_TAG[a.level]}</span>}</td>
            </tr>
          )
        })}
        <tr>
          <td>足りない情報</td>
          <td>{before.missing.map((m) => m.label).join('・') || 'なし'}</td>
          <td>{after.missing.map((m) => m.label).join('・') || 'なし'}</td>
        </tr>
      </tbody>
    </table>
  )
}

export function ReplyPage() {
  useTitle('返信前チェック')
  const { target } = useShell()
  const [params] = useSearchParams()
  const itemId = params.get('item')
  const [meta, setMeta] = useState<ReplyMeta | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [inquiry, setInquiry] = useState('')
  const [draft, setDraft] = useState('')
  const [policy, setPolicy] = useState<string | null>(null)
  const [policyOpen, setPolicyOpen] = useState(false)
  // 「この内容で返信」を、左の「判定する」と同じ高さに置くための位置（枠の上端から）
  const judgeRef = useRef<HTMLButtonElement>(null)
  const [sendTop, setSendTop] = useState<number | null>(null)
  // 運用の件から開いたときの件（判定した下書きで、その件にメールで返信する）
  const [item, setItem] = useState<Item | null>(null)
  const [busy, setBusy] = useState(false)
  const [judged, setJudged] = useState<{ inquiry: string; draft: string; result: ReplyResult } | null>(null)
  const [rwBusy, setRwBusy] = useState(false)
  const [rwError, setRwError] = useState<string | null>(null)
  // AI返信案の書き終わり（モデル・所要時間）。書いている途中は null
  const [suggested, setSuggested] = useState<SuggestDone | null>(null)
  const [fixed, setFixed] = useState('')
  const [after, setAfter] = useState<{ draft: string; result: ReplyResult } | null>(null)
  const [afterBusy, setAfterBusy] = useState(false)
  const elapsed = useElapsed(rwBusy)
  // 判定をやり直したら、それより前に頼んだ AI返信案・再判定の応答は捨てる
  const generation = useRef(0)
  const policyText = policy ?? meta?.default_policy ?? ''

  useEffect(() => {
    tools
      .replyMeta()
      .then(setMeta)
      .catch((e: unknown) => setError(errorMessage(e)))
  }, [])
  // 運用の件から開いたときは、その件の問い合わせを入れる。
  // 伏せ字はサーバが受付の確認どおりに作る（判定・AI返信案も item_id でサーバ側の本文を使う）
  useEffect(() => {
    if (!itemId) {
      setItem(null)
      return
    }
    Promise.all([ops.item(itemId), tools.replyItem(itemId)])
      .then(([{ item }, masked]) => {
        setItem(item)
        setInquiry(masked.inquiry)
      })
      .catch((e: unknown) => setError(errorMessage(e)))
  }, [itemId])

  useLayoutEffect(() => {
    const measure = () => {
      const btn = judgeRef.current
      const panel = btn?.closest('.panel')
      setSendTop(btn && panel ? btn.getBoundingClientRect().top - panel.getBoundingClientRect().top : null)
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [judged])
  // 方針のモーダルは Esc でも閉じる
  useEffect(() => {
    if (!policyOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPolicyOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [policyOpen])
  const stale = judged !== null && (inquiry.trim() !== judged.inquiry || draft.trim() !== judged.draft)
  const check = () => {
    if (!target || !inquiry.trim() || !draft.trim() || busy) return
    setBusy(true)
    setError(null)
    generation.current += 1
    const input = { inquiry: inquiry.trim(), draft: draft.trim() }
    tools
      .reply(target, { ...input, item_id: itemId, policy: policyText })
      .then((result) => {
        setJudged({ ...input, result })
        setSuggested(null)
        setFixed('')
        setAfter(null)
        setRwError(null)
      })
      .catch((e: unknown) => setError(errorMessage(e)))
      .finally(() => setBusy(false))
  }
  // AI返信案: 下書きがあれば直した案（判定したばかりなら指摘を渡す）、なければ問い合わせから書いた案
  // AI返信案は書いた分から順に出す（ストリーミング）
  const makeRewrite = () => {
    if (!inquiry.trim() || rwBusy) return
    const gen = generation.current + 1
    generation.current = gen
    const fresh = judged !== null && !stale ? judged : null
    setRwBusy(true)
    setRwError(null)
    setSuggested(null)
    setFixed('')
    setAfter(null)
    suggestReplyStream(
      {
        inquiry: inquiry.trim(),
        item_id: itemId,
        draft: draft.trim(),
        policy: policyText,
        findings: fresh ? findingsOf(fresh.result) : [],
        model: meta?.default_model ?? 'sonnet',
      },
      (text) => {
        if (gen === generation.current) setFixed((prev) => prev + text)
      },
    )
      .then((done) => {
        if (gen === generation.current) setSuggested(done)
      })
      .catch((e: unknown) => {
        if (gen === generation.current) setRwError(errorMessage(e))
      })
      .finally(() => setRwBusy(false))
  }
  const checkFixed = () => {
    if (!target || !judged || !fixed.trim() || afterBusy) return
    const gen = generation.current
    setAfterBusy(true)
    setRwError(null)
    tools
      .reply(target, { inquiry: judged.inquiry, item_id: itemId, draft: fixed.trim(), policy: policyText })
      .then((result) => {
        if (gen === generation.current) setAfter({ draft: fixed.trim(), result })
      })
      .catch((e: unknown) => setRwError(errorMessage(e)))
      .finally(() => setAfterBusy(false))
  }
  const r = judged?.result

  return (
    <Page wide crumbs={[{ label: 'ツール' }, { label: '返信前チェック' }]}>
      <div className="panel-head">
        <h1>返信前チェック</h1>
        <span className="muted small">
          {itemId && (
            <>
              <Link to={`/ops/inbox?id=${encodeURIComponent(itemId)}`}>{itemId}</Link> から ／{' '}
            </>
          )}
          {target ? TARGET_SHORT[target] : '接続先'}・個人情報の候補は伏せて送ります
        </span>
      </div>
      {error && <div className="error">{error}</div>}
      <div className="tone-layout">
        <section className="panel tone-input reply-input">
          <div className="row">
            <h2 className="inline">問い合わせと返信の下書き</h2>
            <span className="spacer" />
            <button
              type="button"
              className="filter-chip"
              onClick={() => {
                setInquiry(SAMPLE.inquiry)
                setDraft(SAMPLE.draft)
              }}
            >
              例
            </button>
          </div>
          <div className="row label-row">
            <label className="small" htmlFor="reply-inquiry">
              お客様の問い合わせ
            </label>
            <span className="spacer" />
            {itemId ? (
              <span className="masked-badge small" data-testid="masked-badge">
                伏せ字済み（受付の確認どおり）
              </span>
            ) : (
              <PiiCheckButton label="問い合わせ" text={inquiry} onMask={setInquiry} />
            )}
          </div>
          <textarea
            id="reply-inquiry"
            rows={5}
            maxLength={meta?.max_chars ?? 4000}
            value={inquiry}
            readOnly={itemId !== null}
            onChange={(e) => setInquiry(e.target.value)}
          />
          <div className="row label-row">
            <label className="small" htmlFor="reply-draft">
              返信の下書き
            </label>
            <span className="spacer" />
            <PiiCheckButton label="下書き" text={draft} onMask={setDraft} />
          </div>
          <textarea
            id="reply-draft"
            rows={6}
            maxLength={meta?.max_chars ?? 4000}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault()
                check()
              }
            }}
          />
          <div className="row judge-row">
            <button ref={judgeRef} type="button" className="judge-btn" title="Ctrl+Enter" disabled={busy || !inquiry.trim() || !draft.trim() || !target} onClick={check}>
              {busy ? '判定中…' : '判定する'}
            </button>
          </div>
          {/* 方針はモーダルで開く（入力欄の大きさ・位置を変えない） */}
          <div className="policy-area row">
            <button type="button" className="secondary policy-toggle" onClick={() => setPolicyOpen(true)}>
              方針
            </button>
            <Hint text={'判定と AI返信案は、方針を超える約束をしていないかを見ます。\n押すと方針を直せます。'} />
          </div>
          {policyOpen && (
            <>
              <div className="sheet-backdrop" onClick={() => setPolicyOpen(false)} />
              <div className="modal" role="dialog" aria-modal="true" aria-label="方針">
                <h2>方針</h2>
                <textarea id="reply-policy" className="policy-box" aria-label="方針" maxLength={1000} value={policyText} onChange={(e) => setPolicy(e.target.value)} />
                <div className="row judge-row">
                  <button type="button" onClick={() => setPolicyOpen(false)}>
                    閉じる
                  </button>
                </div>
              </div>
            </>
          )}
        </section>

        {r ? (
          <section className={`panel tone-result verdict-${r.verdict}`} data-testid="reply-result">
            <div className="verdict-banner">
              <span className="verdict-label">{VERDICT_LABELS[r.verdict]}</span>
              <span>
                <Sentences text={r.verdict_note} />
              </span>
            </div>
            <div className="muted small">
              {r.model} ／ {r.latency_ms.toFixed(0)} ms ／ {usd(r.cost_usd)}
            </div>
            {stale && <p className="warn-text small">入力が判定したときから変わっています</p>}
            <CheckCards result={r} />
            {item && canMail(item) && (
              // 判定した下書きのまま（判定の後に書き換えていない）なら、その内容でメールソフトを開く。
              // 広い画面では、左の「判定する」と同じ高さに置く
              <div className="reply-send" style={sendTop === null ? undefined : { top: sendTop }}>
                <a
                  className={`btn judge-btn${stale ? ' disabled' : ''}`}
                  href={stale ? undefined : replyHref(item, judged?.draft ?? '')}
                  aria-disabled={stale}
                  title={stale ? '判定し直してから使えます' : undefined}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  この内容で返信
                </a>
              </div>
            )}
          </section>
        ) : (
          <section className="panel tone-empty">
            <p className="muted">
              <Sentences text="問い合わせと下書きを入れて「判定する」を押してください。下書きがなければ、下の「AI返信案の作成」で作れます。" />
            </p>
          </section>
        )}
      </div>

      <section className="panel rewrite" data-testid="reply-rewrite">
        <div className="panel-head">
          <h2>AI返信案（Claude）</h2>
          <button type="button" className="rw-btn" disabled={rwBusy || busy || !inquiry.trim()} onClick={makeRewrite}>
            {rwBusy ? `作成中…（${elapsed} 秒）` : 'AI返信案の作成'}
          </button>
        </div>
        {rwError && <div className="error small">{rwError}</div>}
        {(rwBusy || fixed !== '') && (
          <>
            <textarea aria-label="AI返信案" rows={7} value={fixed} readOnly={rwBusy} onChange={(e) => setFixed(e.target.value)} />
            <div className="row">
              <button type="button" disabled={rwBusy || !fixed.trim()} onClick={() => setDraft(fixed.trim())}>
                採用
              </button>
              {r && (
                <button type="button" className="secondary" disabled={rwBusy || afterBusy || !fixed.trim() || !target} onClick={checkFixed}>
                  {afterBusy ? '判定中…' : 'この案を判定する'}
                </button>
              )}
              <span className="muted small">
                {suggested ? `${suggested.model} ／ ${(suggested.latency_ms / 1000).toFixed(1)} 秒` : rwBusy ? '作成中…' : ''}
              </span>
            </div>
            {r && after && (
              <>
                {after.draft !== fixed.trim() && <p className="warn-text small">編集前の案の比較</p>}
                <Compare before={r} after={after.result} />
              </>
            )}
          </>
        )}
      </section>
    </Page>
  )
}
