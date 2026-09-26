import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { errorMessage } from '../api'
import { pct, TARGET_SHORT, usd } from '../format'
import { Page, useShell, useTitle } from '../shell'
import {
  tools,
  type AspectResult,
  type ClaudeModel,
  type Group,
  type Level,
  type Medium,
  type Purpose,
  type Recipient,
  type RewriteResult,
  type ToneMeta,
  type ToneResult,
} from './api'

const SAMPLES: { label: string; text: string; recipient: Recipient; medium: Medium }[] = [
  { label: '催促', text: '例の件、まだですか？なんで昨日までに終わってないんですか。至急お願いします。', recipient: 'colleague', medium: 'chat' },
  {
    label: '言い訳の入った謝罪',
    text: '先日の納品遅れの件ですが、運送会社の都合で遅れたもので弊社としてはどうしようもありませんでした。今後気をつけます。',
    recipient: 'client',
    medium: 'mail',
  },
  { label: '素っ気ない返事', text: '了解です。資料見ときます。', recipient: 'boss', medium: 'chat' },
  { label: '皮肉', text: 'さすがですね、今回も締め切りギリギリで。いつも本当に助かります。', recipient: 'colleague', medium: 'chat' },
  { label: '曖昧な依頼', text: 'あれの件、いい感じに進めといてもらえますか。よろしくお願いします。', recipient: 'client', medium: 'mail' },
  {
    label: '丁寧なお礼',
    text: '本日はお忙しいところお時間をいただき、ありがとうございました。いただいたご意見をもとに、来週金曜までに修正案をお送りします。',
    recipient: 'client',
    medium: 'mail',
  },
]
const HISTORY_KEY = 'jevlab.tone.history'
const MAX_HISTORY = 10

interface HistoryEntry {
  text: string
  recipient: Recipient
  medium: Medium
  verdict: ToneResult['verdict']
  at: number
}

const isHistoryEntry = (v: unknown): v is HistoryEntry =>
  typeof v === 'object' &&
  v !== null &&
  typeof (v as Record<string, unknown>).text === 'string' &&
  typeof (v as Record<string, unknown>).verdict === 'string' &&
  typeof (v as Record<string, unknown>).at === 'number'

// 入力した文面は保存しない方針のため、履歴はこのタブの中（sessionStorage）だけに置く
function loadHistory(): HistoryEntry[] {
  try {
    const raw = sessionStorage.getItem(HISTORY_KEY)
    const data: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(data) ? data.filter(isHistoryEntry) : []
  } catch (e: unknown) {
    console.error('言い方チェックの履歴を読み込めませんでした', e)
    return []
  }
}

function saveHistory(entries: HistoryEntry[]) {
  try {
    sessionStorage.setItem(HISTORY_KEY, JSON.stringify(entries))
  } catch (e: unknown) {
    console.error('言い方チェックの履歴を保存できませんでした', e)
  }
}

const VERDICT_LABELS: Record<ToneResult['verdict'], string> = { ok: '送って大丈夫', review: '見直し推奨', caution: '要注意' }
const LEVEL_LABELS: Record<Level, string> = { ok: '問題なし', warn: '気になる', bad: '要見直し' }

/** 観点の「悪さ」（0〜1）。サーバが計算した値を使う（丁寧さは「適切」以外の確率）。 */
const badness = (a: AspectResult): number => a.badness

/** 画面で選んでいる目的での総合判定。 */
const verdictFor = (result: ToneResult, purpose: Purpose) => result.verdicts[purpose]

function shownGroups(result: ToneResult, purpose: Purpose): Group[] {
  return result.groups_for_purpose[purpose]
}

/** 目的に合う観点だけ（グループの中でも目的に合わない観点は除く）。 */
function shownAspects(result: ToneResult, purpose: Purpose): AspectResult[] {
  const ids = result.aspects_for_purpose[purpose]
  return result.aspects.filter((a) => ids.includes(a.id))
}

function findingsOf(result: ToneResult, purpose: Purpose) {
  return shownAspects(result, purpose)
    .filter((a) => a.level !== 'ok')
    .map((a) => ({ title: a.title, detail: `${a.note}（${LEVEL_LABELS[a.level]}・${a.value.toFixed(2)}）` }))
}

function AspectMeter({ a }: { a: AspectResult }) {
  const b = badness(a)
  return (
    <div className={`aspect lv-${a.level}`} data-testid={`aspect-${a.id}`}>
      <div className="aspect-head">
        <span>{a.title}</span>
        <span className={`lv-tag lv-${a.level}`}>{a.id === 'politeness' ? a.note : LEVEL_LABELS[a.level]}</span>
      </div>
      {/* 丁寧さは期待値だと両端に割れたときに真ん中に見えるため、他の観点と同じく「悪さ」で表す */}
      <div className="meter" role="meter" aria-label={a.title} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(b * 100)} aria-valuetext={`問題の度合い ${pct(b, 0)}`}>
        <span style={{ width: pct(b) }} />
      </div>
      <div className="muted small">
        {a.polarity === 'good' && a.id !== 'politeness' ? `できている確率 ${pct(a.value, 0)}` : a.id === 'politeness' ? `適切でない確率 ${pct(a.badness, 0)}` : `当てはまる確率 ${pct(a.value, 0)}`}
      </div>
    </div>
  )
}

function ResultView({
  result,
  meta,
  purpose,
  onPurpose,
  title,
}: {
  result: ToneResult
  meta: ToneMeta
  purpose: Purpose
  onPurpose?: (p: Purpose) => void
  title: string
}) {
  const groups = shownGroups(result, purpose)
  const v = verdictFor(result, purpose)
  return (
    <section className={`panel tone-result verdict-${v.verdict}`} data-testid="tone-result">
      <div className="verdict-banner">
        <span className="verdict-label">{VERDICT_LABELS[v.verdict]}</span>
        <span>{v.note}</span>
      </div>
      <div className="row">
        <h2 className="inline">{title}</h2>
        <span className="muted small">
          {result.model} ／ {result.latency_ms.toFixed(0)} ms ／ {usd(result.cost_usd)}
        </span>
      </div>
      <div className="tone-meta">
        <label className="small">
          目的
          {onPurpose ? (
            <select aria-label="文面の目的" value={purpose} onChange={(e) => onPurpose(e.target.value as Purpose)}>
              {(Object.keys(meta.purposes) as Purpose[]).map((p) => (
                <option key={p} value={p}>
                  {meta.purposes[p]}
                  {p === result.purpose ? `（推定 ${pct(result.purpose_confidence, 0)}）` : ''}
                </option>
              ))}
            </select>
          ) : (
            <strong> {meta.purposes[purpose]}</strong>
          )}
        </label>
        <span className="small">
          相手が受け取る印象: <strong className={`imp imp-${result.impression}`}>{meta.impressions[result.impression] ?? result.impression}</strong>
          <span className="muted">（{pct(result.impression_probabilities[result.impression] ?? 0, 0)}）</span>
        </span>
      </div>
      {groups.map((g) => (
        <div key={g} className="aspect-group">
          <h3>{meta.groups[g]}</h3>
          <div className="aspects">
            {shownAspects(result, purpose)
              .filter((a) => a.group === g)
              .map((a) => (
                <AspectMeter key={a.id} a={a} />
              ))}
          </div>
        </div>
      ))}
      <h3>きつく見える文</h3>
      <p className="muted small legend-line">濃いほどきつく見える</p>
      <p className="sentences" data-testid="tone-sentences">
        {result.sentences.map((s, i) => (
          <span
            key={i}
            className={s.flagged ? 'sent flagged' : 'sent'}
            title={`きつい・冷たいと感じる確率 ${pct(s.score, 0)}`}
            style={{ '--heat': Math.min(1, s.score).toFixed(2) } as CSSProperties}
          >
            {s.text}
          </span>
        ))}
      </p>
      {result.truncated && <p className="muted small">先頭の {meta.max_sentences} 文だけ判定しました。</p>}
    </section>
  )
}

function CompareTable({ before, after, meta, purpose }: { before: ToneResult; after: ToneResult; meta: ToneMeta; purpose: Purpose }) {
  const rows = shownAspects(before, purpose)
  return (
    <div className="scroll">
      <table className="compare" data-testid="tone-compare">
        <thead>
          <tr>
            <th>観点</th>
            <th>元の文面</th>
            <th>書き換え案</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>総合</td>
            <td>
              <span className={`vd vd-${verdictFor(before, purpose).verdict}`}>{VERDICT_LABELS[verdictFor(before, purpose).verdict]}</span>
            </td>
            <td>
              <span className={`vd vd-${verdictFor(after, purpose).verdict}`}>{VERDICT_LABELS[verdictFor(after, purpose).verdict]}</span>
            </td>
          </tr>
          <tr>
            <td>受け取る印象</td>
            <td>{meta.impressions[before.impression]}</td>
            <td>{meta.impressions[after.impression]}</td>
          </tr>
          {rows.map((a) => {
            const b = after.aspects.find((x) => x.id === a.id)
            const better = b ? badness(b) < badness(a) - 0.05 : false
            const worse = b ? badness(b) > badness(a) + 0.05 : false
            return (
              <tr key={a.id}>
                <td>{a.title}</td>
                <td>
                  <span className={`lv-tag lv-${a.level}`}>{a.id === 'politeness' ? a.note : LEVEL_LABELS[a.level]}</span>
                </td>
                <td>
                  {b && <span className={`lv-tag lv-${b.level}`}>{b.id === 'politeness' ? b.note : LEVEL_LABELS[b.level]}</span>}
                  {better && <span className="delta up"> 改善</span>}
                  {worse && <span className="delta down"> 悪化</span>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function useElapsed(running: boolean): number {
  const [sec, setSec] = useState(0)
  const started = useRef(0)
  useEffect(() => {
    if (!running) return
    started.current = Date.now()
    const timer = setInterval(() => setSec(Math.floor((Date.now() - started.current) / 1000)), 500)
    return () => {
      clearInterval(timer)
      setSec(0)
    }
  }, [running])
  return sec
}

// 6 文面での実測（2026-09）に基づく目安。
// sonnet が最速で質も十分、opus は皮肉・断りなど微妙な言い回しの直しがやや上
const MODEL_HINTS: Record<ClaudeModel, string> = {
  sonnet: '速い・質も十分',
  opus: '微妙なニュアンス向き',
}

/** 「指定なし」を先頭に並べる。 */
const noneFirst = <T extends string>(keys: T[]): T[] => [...keys.filter((k) => k === 'none'), ...keys.filter((k) => k !== 'none')]

export function TonePage() {
  useTitle('言い方チェック')
  const { target } = useShell()
  const [meta, setMeta] = useState<ToneMeta | null>(null)
  const [metaError, setMetaError] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [recipient, setRecipient] = useState<Recipient>('none')
  const [medium, setMedium] = useState<Medium>('none')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [judged, setJudged] = useState<{ text: string; recipient: Recipient; medium: Medium; result: ToneResult } | null>(null)
  const [purposeOverride, setPurposeOverride] = useState<Purpose | null>(null)
  const [model, setModel] = useState<ClaudeModel | null>(null)
  const [rwBusy, setRwBusy] = useState(false)
  const [rwError, setRwError] = useState<string | null>(null)
  const [rewrite, setRewrite] = useState<RewriteResult | null>(null)
  const [draft, setDraft] = useState('')
  // 書き換え案の判定結果と、判定したときの案（編集したら比較は古くなる）
  const [after, setAfter] = useState<{ draft: string; result: ToneResult } | null>(null)
  const [afterBusy, setAfterBusy] = useState(false)
  const [afterError, setAfterError] = useState<string | null>(null)
  const [history, setHistory] = useState<HistoryEntry[]>(loadHistory)
  const [copyMsg, setCopyMsg] = useState<string | null>(null)
  const elapsed = useElapsed(rwBusy)
  // 判定をやり直したら、それより前に出した書き換え・再判定の応答は捨てる
  const generation = useRef(0)
  const draftEdited = rewrite !== null && draft !== rewrite.rewritten
  // 入力欄・相手・場面が判定したときから変わっているか（書き換えは判定した文面に対して作るため）
  const stale = judged !== null && (text.trim() !== judged.text || recipient !== judged.recipient || medium !== judged.medium)

  useEffect(() => {
    tools
      .toneMeta()
      .then(setMeta)
      .catch((e: unknown) => setMetaError(`言い方チェックの定義の取得に失敗: ${errorMessage(e)}`))
  }, [])

  const purpose = purposeOverride ?? judged?.result.purpose ?? 'request'
  const chosenModel = model ?? meta?.default_model ?? 'sonnet'

  const check = () => {
    if (busy || !text.trim() || !target) return
    if (draftEdited && !window.confirm('編集した書き換え案は、判定し直すと消えます。続けますか？')) return
    const gen = ++generation.current
    setBusy(true)
    setError(null)
    const input = { text: text.trim(), recipient, medium }
    tools
      .tone(target, input)
      .then((result) => {
        if (gen !== generation.current) return
        setJudged({ ...input, result })
        setPurposeOverride(null)
        setRewrite(null)
        setDraft('')
        setAfter(null)
        const next = [{ ...input, verdict: result.verdict, at: Date.now() }, ...history.filter((h) => h.text !== input.text)].slice(0, MAX_HISTORY)
        setHistory(next)
        saveHistory(next)
      })
      .catch((e: unknown) => {
        if (gen === generation.current) setError(errorMessage(e))
      })
      .finally(() => setBusy(false))
  }

  const makeRewrite = () => {
    if (!judged || rwBusy) return
    if (draftEdited && !window.confirm('編集した書き換え案は、作り直すと消えます。続けますか？')) return
    const gen = generation.current
    setRwBusy(true)
    setRwError(null)
    setAfter(null)
    tools
      .rewrite({
        text: judged.text,
        recipient: judged.recipient,
        medium: judged.medium,
        purpose,
        findings: findingsOf(judged.result, purpose),
        flagged_sentences: judged.result.sentences.filter((s) => s.flagged).map((s) => s.text),
        model: chosenModel,
      })
      .then((r) => {
        if (gen !== generation.current) return
        setRewrite(r)
        setDraft(r.rewritten)
      })
      .catch((e: unknown) => {
        if (gen === generation.current) setRwError(errorMessage(e))
      })
      .finally(() => setRwBusy(false))
  }

  const checkDraft = () => {
    if (!judged || !draft.trim() || !target || afterBusy) return
    const gen = generation.current
    const judgedDraft = draft
    setAfterBusy(true)
    setAfterError(null)
    tools
      .tone(target, { text: draft.trim(), recipient: judged.recipient, medium: judged.medium })
      .then((r) => {
        if (gen === generation.current) setAfter({ draft: judgedDraft, result: r })
      })
      .catch((e: unknown) => {
        if (gen === generation.current) setAfterError(errorMessage(e))
      })
      .finally(() => setAfterBusy(false))
  }

  const copy = () => {
    setCopyMsg(null)
    // http で LAN の IP から開いた場合など、安全な接続でないとクリップボードは使えない
    if (!navigator.clipboard) {
      setCopyMsg('この接続ではコピーできません。文面を選んでコピーしてください')
      return
    }
    navigator.clipboard
      .writeText(draft)
      .then(() => setCopyMsg('コピーしました'))
      .catch((e: unknown) => setCopyMsg(`コピーできませんでした: ${errorMessage(e)}`))
  }

  return (
    <Page wide crumbs={[{ label: 'ツール' }, { label: '言い方チェック' }]}>
      <div className="panel-head">
        <h1>言い方チェック</h1>
        <span className="muted small">
          {target ? TARGET_SHORT[target] : '接続先'}・文面は保存しません
        </span>
      </div>
      {metaError && <div className="error">{metaError}</div>}
      <div className="tone-layout">
        <section className="panel tone-input">
          <h2>送る前の文面</h2>
          <div className="row">
            <span className="muted small">例文:</span>
            {SAMPLES.map((s) => (
              <button
                key={s.label}
                type="button"
                className="filter-chip"
                onClick={() => {
                  setText(s.text)
                  setRecipient(s.recipient)
                  setMedium(s.medium)
                }}
              >
                {s.label}
              </button>
            ))}
          </div>
          <textarea
            aria-label="送る前の文面"
            rows={7}
            maxLength={2000}
            value={text}
            placeholder="例: 例の件、まだですか？至急お願いします。"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault()
                check()
              }
            }}
          />
          <div className="row">
            <label className="small" htmlFor="tone-recipient">
              相手
            </label>
            <select id="tone-recipient" value={recipient} onChange={(e) => setRecipient(e.target.value as Recipient)}>
              {meta &&
                noneFirst(Object.keys(meta.recipients) as Recipient[]).map((r) => (
                  <option key={r} value={r}>
                    {meta.recipients[r]}
                  </option>
                ))}
            </select>
            <div className="seg" role="group" aria-label="場面">
              {meta &&
                noneFirst(Object.keys(meta.mediums) as Medium[]).map((m) => (
                  <button key={m} type="button" className="seg-btn" aria-pressed={medium === m} onClick={() => setMedium(m)}>
                    {meta.mediums[m]}
                  </button>
                ))}
            </div>
            <span className="spacer" />
            <span className="muted small">{text.length} / 2000</span>
            <button type="button" disabled={busy || !text.trim() || !target} onClick={check}>
              {busy ? '判定中…' : '判定する（Ctrl+Enter）'}
            </button>
          </div>
          {error && (
            <div className="error small" role="alert">
              {error}
            </div>
          )}
          {history.length > 0 && (
            <details className="tone-history">
              <summary className="small">このタブの履歴（{history.length}）</summary>
              <ul>
                {history.map((h) => (
                  <li key={h.at}>
                    <button
                      type="button"
                      className="link-btn"
                      onClick={() => {
                        setText(h.text)
                        setRecipient(h.recipient)
                        setMedium(h.medium)
                      }}
                    >
                      {h.text.slice(0, 40)}
                    </button>
                    <span className={`vd vd-${h.verdict}`}>{VERDICT_LABELS[h.verdict]}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </section>

        {judged && meta ? (
          <ResultView result={judged.result} meta={meta} purpose={purpose} onPurpose={setPurposeOverride} title="判定結果" />
        ) : (
          <section className="panel tone-empty">
            <p className="muted">文面を入れて「判定する」を押してください。</p>
          </section>
        )}
      </div>

      {judged && meta && (
        <section className="panel rewrite" data-testid="rewrite">
          <div className="panel-head">
            <h2>書き換え案（Claude）</h2>
            <div className="row tight">
              <label className="small" htmlFor="rw-model">
                モデル
              </label>
              <select id="rw-model" value={chosenModel} onChange={(e) => setModel(e.target.value as ClaudeModel)}>
                {meta.models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                    {m === meta.default_model ? '（おすすめ）' : ''}
                    {MODEL_HINTS[m] ? ` ${MODEL_HINTS[m]}` : ''}
                  </option>
                ))}
              </select>
              <button type="button" className="rw-btn" disabled={rwBusy || busy || stale} onClick={makeRewrite}>
                {rwBusy ? `作成中… ${elapsed} 秒` : rewrite ? '作り直す' : '書き換え案を作る'}
              </button>
            </div>
          </div>
          <p className="muted small">
            指摘 {findingsOf(judged.result, purpose).length} 件を Claude に渡す。足りない情報は【空欄】
          </p>
          {stale && <p className="warn-text small">入力が変わっています。判定し直してください。</p>}
          {rwError && (
            <div className="error small" role="alert">
              {rwError}
            </div>
          )}
          {rewrite && (
            <>
              <textarea aria-label="書き換え案" rows={9} value={draft} onChange={(e) => setDraft(e.target.value)} data-testid="rewrite-draft" />
              <div className="row">
                <span className="muted small">
                  {rewrite.model} ／ {(rewrite.latency_ms / 1000).toFixed(1)} 秒
                  {rewrite.reported_cost_usd !== null && ` ／ 定価換算 ${usd(rewrite.reported_cost_usd)}（サブスク枠）`}
                </span>
                <span className="spacer" />
                {copyMsg && (
                  <span className="muted small" role="status">
                    {copyMsg}
                  </span>
                )}
                <button type="button" className="secondary" onClick={copy}>
                  コピー
                </button>
                <button type="button" disabled={afterBusy || !draft.trim() || !target} onClick={checkDraft}>
                  {afterBusy ? '判定中…' : 'この案を判定する'}
                </button>
              </div>
              <div className="rewrite-notes">
                {rewrite.changes.length > 0 && (
                  <div>
                    <h3>直したところ</h3>
                    <ul>
                      {rewrite.changes.map((c, i) => (
                        <li key={i}>{c}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {rewrite.placeholders.length > 0 && (
                  <div>
                    <h3>書き手が埋める空欄</h3>
                    <ul>
                      {rewrite.placeholders.map((c, i) => (
                        <li key={i}>{c}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
              {afterError && (
                <div className="error small" role="alert">
                  {afterError}
                </div>
              )}
              {after && (
                <>
                  <h3>元の文面との比較</h3>
                  {after.draft !== draft && <p className="warn-text small">編集前の案の比較です。</p>}
                  <CompareTable before={judged.result} after={after.result} meta={meta} purpose={purpose} />
                </>
              )}
            </>
          )}
        </section>
      )}
    </Page>
  )
}
