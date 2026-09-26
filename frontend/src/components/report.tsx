import type { AnswerView, AppInfo, CaseResult, EvalReport, QuestionInfo, QuestionMetrics } from '../api'
import { answerConfidence, answerKey, answerProbabilities, optionColor, optionLabel, pct } from '../format'

// 「一致」は想定ラベル（作成したダミー）と同じだったという意味で、モデルの正誤そのものではない
export const MATCH_NOTE =
  '一致率＝想定ラベル（ダミー）と同じ割合。不一致＝誤りとは限らない'

export function subMetric(m: QuestionMetrics): string {
  const f = (x: number | null) => (x === null ? '-' : x.toFixed(3))
  switch (m.type) {
    case 'choice':
      return `ECE ${f(m.ece)}（0に近いほど確信度が正直）`
    case 'score':
      return `MAE ${f(m.mae)} ／ ECE ${f(m.ece)}`
    case 'noul':
      return `Brier ${f(m.brier)}`
  }
}

const byId = (info: AppInfo) => new Map(info.questions.map((q) => [q.id, q]))

export function MetricsTable({ info, report }: { info: AppInfo; report: EvalReport }) {
  const qs = byId(info)
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>項目</th>
            <th className="num">一致率</th>
            <th>補助指標</th>
          </tr>
        </thead>
        <tbody>
          {report.questions.map((m) => (
            <tr key={m.id}>
              <td>
                {qs.get(m.id)?.title ?? m.id}
                {m.id === info.primary && <span className="chip primary">主ラベル</span>}
              </td>
              <td className="num">{pct(m.accuracy)}</td>
              <td>{subMetric(m)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function CalibrationTables({ info, report }: { info: AppInfo; report: EvalReport }) {
  const qs = byId(info)
  return report.questions
    .filter((m) => m.reliability.length > 0)
    .map((m) => (
      <div key={m.id}>
        <h3>{qs.get(m.id)?.title ?? m.id}の較正（確信度の区間ごとの実際の一致率）</h3>
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>確信度の区間</th>
                <th className="num">件数</th>
                <th className="num">平均確信度</th>
                <th className="num">実際の一致率</th>
              </tr>
            </thead>
            <tbody>
              {m.reliability.map((b) => (
                <tr key={b.lower}>
                  <td>
                    {b.lower.toFixed(1)}〜{b.upper.toFixed(1)}
                  </td>
                  <td className="num">{b.count}</td>
                  <td className="num">{b.count ? pct(b.mean_confidence) : '-'}</td>
                  <td className="num">{b.count ? pct(b.accuracy) : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    ))
}

function predictedText(q: QuestionInfo, a: AnswerView): string {
  switch (a.type) {
    case 'choice':
      return `${optionLabel(q, a.prediction)}（${pct(answerConfidence(a))}）`
    case 'score':
    case 'noul':
      return `${optionLabel(q, a.prediction)}（${(a.value ?? 0).toFixed(2)}）`
  }
}

export function CasesTable({ info, cases }: { info: AppInfo; cases: CaseResult[] }) {
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>本文</th>
            {info.questions.map((q) => (
              <th key={q.id}>{q.title}（想定→予測）</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cases.map((c) => (
            <tr key={c.sample.id}>
              <td>{c.sample.id}</td>
              <td className="body">{c.sample.body}</td>
              {info.questions.map((q) => {
                const a = c.result.answers[q.id]
                const expected = c.sample.labels[q.id]
                if (!a || expected === undefined) return <td key={q.id} className="muted">-</td>
                const same = c.ok[q.id]
                return (
                  <td key={q.id} className={same ? undefined : 'ng'} style={same ? undefined : { fontWeight: 400 }}>
                    {optionLabel(q, expected)} → {predictedText(q, a)}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** 主ラベルの混同行列（行: 想定ラベル、列: 予測）。 */
export function ConfusionMatrix({ q, cases }: { q: QuestionInfo; cases: CaseResult[] }) {
  const keys = Object.keys(q.options)
  const count = (exp: string, got: string) =>
    cases.filter((c) => {
      const a = c.result.answers[q.id]
      return a !== undefined && String(c.sample.labels[q.id]) === exp && answerKey(a) === got
    }).length
  const max = Math.max(1, ...keys.flatMap((e) => keys.map((g) => count(e, g))))
  return (
    <div className="scroll">
      <table className="confusion" data-testid="confusion">
        <thead>
          <tr>
            <th className="axis">想定 ＼ 予測</th>
            {keys.map((k) => (
              <th key={k} style={{ color: optionColor(q, k) }}>
                {optionLabel(q, k)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {keys.map((e) => (
            <tr key={e}>
              <th style={{ color: optionColor(q, e) }}>{optionLabel(q, e)}</th>
              {keys.map((g) => {
                const n = count(e, g)
                const tint = e === g ? 'var(--ok)' : 'var(--ng)'
                return (
                  <td
                    key={g}
                    className="cell"
                    style={{
                      background: n ? `color-mix(in srgb, ${tint} ${Math.round((n / max) * 45) + 8}%, transparent)` : undefined,
                    }}
                  >
                    {n || <span className="muted">·</span>}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** 1 問ぶんの予測を色つきの確率バーで表示する（単発判定・詳細シート共通）。 */
export function ProbabilityRows({ q, a }: { q: QuestionInfo; a: AnswerView }) {
  const probs = answerProbabilities(a)
  const picked = answerKey(a)
  return Object.keys(q.options)
    .map((k) => [k, probs[k] ?? 0] as const)
    .sort((x, y) => y[1] - x[1])
    .map(([k, p]) => (
      <div key={k} className={k === picked ? 'prob-row picked' : 'prob-row'}>
        <span style={{ color: optionColor(q, k) }}>{q.type === 'score' ? `${k}: ${optionLabel(q, k)}` : optionLabel(q, k)}</span>
        <span className="track">
          <span style={{ width: pct(p), background: optionColor(q, k) }} />
        </span>
        <span className="num">{pct(p)}</span>
      </div>
    ))
}
