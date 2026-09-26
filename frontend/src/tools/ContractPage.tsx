import { useEffect, useRef, useState } from 'react'
import { errorMessage } from '../api'
import { pct, TARGET_SHORT, usd } from '../format'
import { Page, useShell, useTitle } from '../shell'
import { tools, type ClauseLevel, type ClauseResult, type ContractMeta, type ContractResult, type Explanation } from './api'
import { useElapsed } from './common'
import { Sentences } from './Sentences'

// 評価アプリ「契約条項のリスク判定」の評価データから作った例（架空の SaaS 利用規約）
const SAMPLE = `第1条（目的）
本規約は、当社が提供する本サービスの利用条件を定めるものとする。
第2条（利用料金）
利用者は、本サービスの利用料金を毎月末日までに当社指定の方法で支払うものとする。
第3条（契約期間）
本契約の有効期間は契約締結日から1年間とする。期間満了の1か月前までにいずれの当事者からも書面による別段の申し出がないときは、同一条件でさらに1年間更新され、以後も同様とする。
第4条（中途解約）
利用者が契約期間の途中で本契約を解約する場合、利用者は残存期間に相当する利用料金の全額を違約金として当社に支払うものとする。
第5条（データの利用）
当社は、利用者が本サービスに登録したデータを、当社および当社の提携先におけるサービス改善、新サービスの開発、機械学習モデルの学習に利用できるものとし、利用者はこれに同意する。
第6条（責任の制限）
当社が利用者に対して負う損害賠償責任は、直接かつ現実に生じた通常の損害に限り、当該損害の発生した月に利用者が当社に支払った利用料金の額を上限とする。`

const LEVEL_LABELS: Record<ClauseLevel, string> = { high: '要確認', mid: '注意', low: '問題なし' }

function ClauseCard({ c, meta, explanation }: { c: ClauseResult; meta: ContractMeta; explanation?: Explanation }) {
  return (
    <li className={`clause lv-${c.level}`} data-testid="clause">
      <div className="clause-head">
        <span className={`clause-tag lv-${c.level}`}>{LEVEL_LABELS[c.level]}</span>
        {c.flags.map((f) => (
          <span key={f} className="flag-chip" title={`当てはまる確率 ${pct(c.flag_probs[f] ?? 0, 0)}`}>
            {meta.flags[f] ?? f}
          </span>
        ))}
        <span className="spacer" />
        <span className="muted small">不利さ {c.risk.toFixed(2)} / 2</span>
      </div>
      <div className="clause-text">{c.text}</div>
      {explanation && (
        <div className="clause-explain" data-testid="clause-explain">
          <p>
            <Sentences text={explanation.summary} />
          </p>
          {explanation.ask.length > 0 && (
            <ul className="small">
              {explanation.ask.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </li>
  )
}

export function ContractPage() {
  useTitle('契約・規約チェック')
  const { target } = useShell()
  const [meta, setMeta] = useState<ContractMeta | null>(null)
  const [metaError, setMetaError] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [judged, setJudged] = useState<{ text: string; result: ContractResult } | null>(null)
  const [exBusy, setExBusy] = useState(false)
  const [exError, setExError] = useState<string | null>(null)
  const [explained, setExplained] = useState<Record<number, Explanation>>({})
  const [exModel, setExModel] = useState<string | null>(null)
  const elapsed = useElapsed(exBusy)
  // 判定をやり直したら、それより前に頼んだ説明の応答は捨てる
  const generation = useRef(0)

  useEffect(() => {
    tools
      .contractMeta()
      .then(setMeta)
      .catch((e: unknown) => setMetaError(errorMessage(e)))
  }, [])

  const stale = judged !== null && text.trim() !== judged.text
  const check = () => {
    if (!target || !text.trim() || busy) return
    setBusy(true)
    setError(null)
    generation.current += 1
    tools
      .contract(target, text.trim())
      .then((result) => {
        setJudged({ text: text.trim(), result })
        setExplained({})
        setExError(null)
        setExModel(null)
      })
      .catch((e: unknown) => setError(errorMessage(e)))
      .finally(() => setBusy(false))
  }
  const risky = judged ? judged.result.clauses.filter((c) => c.level !== 'low' || c.flags.length > 0) : []
  const explain = () => {
    if (!risky.length || exBusy) return
    const gen = generation.current
    setExBusy(true)
    setExError(null)
    tools
      .explain(
        risky.map((c) => ({ index: c.index, text: c.text, level: c.level, flags: c.flags })),
        meta?.default_model ?? 'sonnet',
      )
      .then((r) => {
        if (gen !== generation.current) return
        setExplained(Object.fromEntries(r.items.map((x) => [x.index, x])))
        setExModel(`${r.model} ／ ${(r.latency_ms / 1000).toFixed(1)} 秒`)
      })
      .catch((e: unknown) => setExError(errorMessage(e)))
      .finally(() => setExBusy(false))
  }
  const r = judged?.result

  return (
    <Page wide crumbs={[{ label: 'ツール' }, { label: '契約・規約チェック' }]}>
      <div className="panel-head">
        <h1>契約・規約チェック</h1>
        <span className="muted small">{target ? TARGET_SHORT[target] : '接続先'}・文面は保存しません</span>
      </div>
      <div className="warn-box" data-testid="disclaimer">
        <Sentences text={meta?.disclaimer ?? '法的助言ではありません。法務に回す前の一次チェックの目安です。'} />
      </div>
      {metaError && <div className="error">{metaError}</div>}
      <div className="tone-layout">
        <section className="panel tone-input contract-input">
          <h2>契約書・規約の本文</h2>
          <div className="row">
            <button type="button" className="filter-chip" onClick={() => setText(SAMPLE)}>
              例: SaaS 利用規約
            </button>
          </div>
          <textarea
            aria-label="契約書・規約の本文"
            maxLength={meta?.max_chars ?? 20000}
            value={text}
            placeholder="第1条（目的）…"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault()
                check()
              }
            }}
          />
          <div className="row">
            <span className="muted small">第N条・番号・空行で条項に分けます（{meta?.max_clauses ?? 20} 条まで）</span>
          </div>
          <div className="row judge-row">
            <button type="button" className="judge-btn" title="Ctrl+Enter" disabled={busy || !text.trim() || !target} onClick={check}>
              {busy ? '判定中…' : '判定する'}
            </button>
          </div>
          {error && (
            <div className="error small" role="alert">
              {error}
            </div>
          )}
        </section>

        {r && meta ? (
          <section className="panel" data-testid="contract-result">
            <div className="panel-head">
              <h2 className="inline">条項ごとの判定</h2>
              <span className="muted small">
                {r.model} ／ {r.latency_ms.toFixed(0)} ms ／ {usd(r.cost_usd)}
              </span>
            </div>
            <p className="small">
              <span className="clause-tag lv-high">要確認 {r.counts.high}</span> <span className="clause-tag lv-mid">注意 {r.counts.mid}</span>{' '}
              <span className="clause-tag lv-low">問題なし {r.counts.low}</span>
              {r.truncated && <span className="warn-text"> 先頭の {r.clauses.length} 条だけ判定しました</span>}
            </p>
            {stale && <p className="warn-text small">本文が判定したときから変わっています</p>}
            <div className="row">
              <button type="button" className="rw-btn" disabled={exBusy || !risky.length || stale} onClick={explain}>
                {exBusy ? `説明を作成中…（${elapsed} 秒）` : `やさしく説明（Claude・${risky.length} 条）`}
              </button>
              {exModel && <span className="muted small">{exModel}</span>}
            </div>
            {exError && <div className="error small">{exError}</div>}
            <ol className="clause-list">
              {r.clauses.map((c) => (
                <ClauseCard key={c.index} c={c} meta={meta} explanation={explained[c.index]} />
              ))}
            </ol>
          </section>
        ) : (
          <section className="panel tone-empty">
            <p className="muted">本文を入れて「判定する」を押してください。</p>
          </section>
        )}
      </div>
    </Page>
  )
}
