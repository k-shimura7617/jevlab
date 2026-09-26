import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'
import { errorMessage } from '../api'
import { pct, TARGET_SHORT, usd } from '../format'
import { ops, type Item } from '../ops/api'
import { canMail, replyHref } from '../ops/mail'
import { Page, useShell, useTitle } from '../shell'
import { tools, type Level, type ReplyCheck, type ReplyMeta, type ReplyResult, type RewriteResult } from './api'
import { Hint } from '../components/hint'
import { useElapsed } from './common'
import { Sentences } from './Sentences'

const SAMPLE = {
  inquiry: '先週注文したマグカップが割れて届きました。明日のプレゼントに使いたかったのに残念です。交換はできますか？',
  draft: '承知しました。すぐに新しいものを明日までにお届けします。送料もこちらで負担します。',
}
const LEVEL_TAG: Record<Level, string> = { ok: '問題なし', warn: '気になる', bad: '要見直し' }
const VERDICT_LABELS = { ok: '送ってよさそう', review: '見直し推奨', caution: '要注意' } as const

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
  // 運用の件から開いたときの件（判定した下書きで、その件にメールで返信する）
  const [item, setItem] = useState<Item | null>(null)
  const [busy, setBusy] = useState(false)
  const [judged, setJudged] = useState<{ inquiry: string; draft: string; result: ReplyResult } | null>(null)
  const [rwBusy, setRwBusy] = useState(false)
  const [rwError, setRwError] = useState<string | null>(null)
  const [rewrite, setRewrite] = useState<RewriteResult | null>(null)
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
  // エスカレーションから開いたときは、その件の問い合わせ（個人情報を伏せて Jev に送った本文）を入れる
  useEffect(() => {
    if (!itemId) return
    ops
      .item(itemId)
      .then(({ item }) => {
        setItem(item)
        if (item.sent_text === null) setError(`${item.id} は個人情報のため Jev に送っていないので、ここでは使えません`)
        else setInquiry(item.sent_text)
      })
      .catch((e: unknown) => setError(errorMessage(e)))
  }, [itemId])

  const stale = judged !== null && (inquiry.trim() !== judged.inquiry || draft.trim() !== judged.draft)
  const check = () => {
    if (!target || !inquiry.trim() || !draft.trim() || busy) return
    setBusy(true)
    setError(null)
    generation.current += 1
    const input = { inquiry: inquiry.trim(), draft: draft.trim() }
    tools
      .reply(target, { ...input, policy: policyText })
      .then((result) => {
        setJudged({ ...input, result })
        setRewrite(null)
        setAfter(null)
        setRwError(null)
      })
      .catch((e: unknown) => setError(errorMessage(e)))
      .finally(() => setBusy(false))
  }
  // AI返信案: 下書きがあれば直した案（判定したばかりなら指摘を渡す）、なければ問い合わせから書いた案
  const makeRewrite = () => {
    if (!inquiry.trim() || rwBusy) return
    const gen = generation.current
    const model = meta?.default_model ?? 'sonnet'
    const fresh = judged !== null && !stale ? judged : null
    setRwBusy(true)
    setRwError(null)
    const request = draft.trim()
      ? tools.replyRewrite({
          inquiry: inquiry.trim(),
          draft: draft.trim(),
          policy: policyText,
          findings: fresh ? findingsOf(fresh.result) : [],
          model,
        })
      : tools.replyDraft({ inquiry: inquiry.trim(), policy: policyText, model })
    request
      .then((res) => {
        if (gen !== generation.current) return
        setRewrite(res)
        setFixed(res.rewritten)
        setAfter(null)
      })
      .catch((e: unknown) => setRwError(errorMessage(e)))
      .finally(() => setRwBusy(false))
  }
  const checkFixed = () => {
    if (!target || !judged || !fixed.trim() || afterBusy) return
    const gen = generation.current
    setAfterBusy(true)
    setRwError(null)
    tools
      .reply(target, { inquiry: judged.inquiry, draft: fixed.trim(), policy: policyText })
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
              <Link to={`/ops/items/${encodeURIComponent(itemId)}`}>{itemId}</Link> から ／{' '}
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
          <label className="small" htmlFor="reply-inquiry">
            お客様の問い合わせ
          </label>
          <textarea id="reply-inquiry" rows={5} maxLength={meta?.max_chars ?? 4000} value={inquiry} onChange={(e) => setInquiry(e.target.value)} />
          <label className="small" htmlFor="reply-draft">
            返信の下書き
          </label>
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
            {item && canMail(item) && (
              // 判定した下書きのまま（判定の後に書き換えていない）なら、その内容でメールソフトを開く
              <a
                className={`btn secondary${judged && !stale ? '' : ' disabled'}`}
                href={judged && !stale ? replyHref(item, judged.draft) : undefined}
                aria-disabled={!judged || stale}
                title={judged && !stale ? undefined : '判定してから使えます'}
                target="_blank"
                rel="noopener noreferrer"
              >
                この内容で返信
              </a>
            )}
            <button type="button" className="judge-btn" title="Ctrl+Enter" disabled={busy || !inquiry.trim() || !draft.trim() || !target} onClick={check}>
              {busy ? '判定中…' : '判定する'}
            </button>
          </div>
          {/* 方針は枠の下端に置く。開くと上の余白に広がり、下書き・判定するは動かない */}
          <div className="policy-area">
            {policyOpen && (
              <textarea id="reply-policy" className="policy-box" aria-label="方針" rows={4} maxLength={1000} value={policyText} onChange={(e) => setPolicy(e.target.value)} />
            )}
            <div className="row">
              <button type="button" className="secondary policy-toggle" aria-expanded={policyOpen} onClick={() => setPolicyOpen((v) => !v)}>
                方針 {policyOpen ? '▾' : '▸'}
              </button>
              <Hint text={'判定と AI返信案は、方針を超える約束をしていないかを見ます。\n開くと方針を直せます。'} />
            </div>
          </div>
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
        {rewrite && (
          <>
            <textarea aria-label="AI返信案" rows={7} value={fixed} onChange={(e) => setFixed(e.target.value)} />
            <div className="row">
              <button type="button" disabled={!fixed.trim()} onClick={() => setDraft(fixed.trim())}>
                採用
              </button>
              {r && (
                <button type="button" className="secondary" disabled={afterBusy || !fixed.trim() || !target} onClick={checkFixed}>
                  {afterBusy ? '判定中…' : 'この案を判定する'}
                </button>
              )}
              <span className="muted small">
                {rewrite.model} ／ {(rewrite.latency_ms / 1000).toFixed(1)} 秒
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
