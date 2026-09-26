import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'
import { api, errorMessage, type AppInfo, type EvalReport, type JudgeResult, type RunRecord, type Sample } from '../api'
import { CalibrationTables, CasesTable, MATCH_NOTE, MetricsTable, ProbabilityRows } from '../components/report'
import { answerConfidence, answerKey, localTime, optionColor, optionLabel, pct, usd } from '../format'
import { ModeBadge, Page, useShell, useTitle } from '../shell'
import { useAppData } from '../useAppData'

function AnswerCards({ info, result }: { info: AppInfo; result: JudgeResult }) {
  return (
    <div className="grid">
      {info.questions.map((q) => {
        const a = result.answers[q.id]
        if (!a) return null
        const key = answerKey(a)
        return (
          <div key={q.id} className="card" style={{ borderTop: `4px solid ${optionColor(q, key)}` }}>
            <div className="muted">
              {q.title}
              {q.id === info.primary && <span className="chip primary">主ラベル</span>}
            </div>
            <div className="big" style={{ color: optionColor(q, key) }}>
              {q.type === 'score' ? `${(a.value ?? 0).toFixed(2)}（${optionLabel(q, a.prediction)}）` : optionLabel(q, a.prediction)}
            </div>
            <div className="muted small">確信度 {pct(answerConfidence(a))}</div>
            <ProbabilityRows q={q} a={a} />
          </div>
        )
      })}
    </div>
  )
}

function JudgePanel({ name, info, samples }: { name: string; info: AppInfo; samples: Sample[] }) {
  const { refreshStatus, target } = useShell()
  const [sampleId, setSampleId] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<JudgeResult | null>(null)

  const judge = () => {
    const text = body.trim()
    if (!text) {
      setError('本文を入力してください')
      return
    }
    if (!target) {
      setError('接続先の状態を取得中です。少し待ってから実行してください')
      return
    }
    setError(null)
    setBusy(true)
    api
      .judge(name, target, text)
      .then(setResult)
      .catch((e: unknown) => setError(errorMessage(e)))
      .finally(() => {
        setBusy(false)
        refreshStatus()
      })
  }

  return (
    <section className="panel">
      <h2>単発判定</h2>
      <div className="row">
        <label htmlFor="sample">評価データから選ぶ:</label>
        <select
          id="sample"
          value={sampleId}
          onChange={(e) => {
            setSampleId(e.target.value)
            const s = samples.find((x) => x.id === e.target.value)
            if (s) setBody(s.body)
          }}
        >
          <option value="">（自由入力）</option>
          {samples.map((s) => (
            <option key={s.id} value={s.id}>
              {s.id}: {s.body.slice(0, 40)}…
            </option>
          ))}
        </select>
      </div>
      <textarea id="body" placeholder="本文を入力" value={body} onChange={(e) => setBody(e.target.value)} />
      <div className="row">
        <button onClick={judge} disabled={busy}>
          判定
        </button>
        <span className="muted">
          {busy
            ? '判定中…'
            : result &&
              `${result.model} ／ ${result.input_tokens} tokens ／ ${result.latency_ms.toFixed(0)} ms ／ ${usd(result.cost_usd)}`}
        </span>
      </div>
      {error && <div className="error">{error}</div>}
      {result && <AnswerCards info={info} result={result} />}
    </section>
  )
}

function HistoryTable({ info, runs }: { info: AppInfo; runs: RunRecord[] }) {
  if (!runs.length) return <div className="muted">まだ評価していません</div>
  return (
    <table>
      <thead>
        <tr>
          <th>日時</th>
          <th>接続先</th>
          <th>モデル</th>
          <th className="num">件数</th>
          <th className="num">平均一致率</th>
          {info.questions.map((q) => (
            <th key={q.id} className="num">
              {q.title}
            </th>
          ))}
          <th className="num">平均応答</th>
          <th className="num">コスト</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((r) => {
          const acc = new Map(r.questions.map((q) => [q.id, q.accuracy]))
          return (
            <tr key={r.at}>
              <td>{localTime(r.at)}</td>
              <td>
                <ModeBadge mode={r.mode} />
              </td>
              <td>{r.model}</td>
              <td className="num">
                {r.n}/{r.total}
              </td>
              <td className="num">
                <strong>{pct(r.mean_accuracy)}</strong>
              </td>
              {info.questions.map((q) => {
                const v = acc.get(q.id)
                return (
                  <td key={q.id} className="num">
                    {v === undefined ? '-' : pct(v)}
                  </td>
                )
              })}
              <td className="num">{r.mean_latency_ms.toFixed(0)} ms</td>
              <td className="num">{usd(r.total_cost_usd)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export function AppPage() {
  const name = useParams().name ?? ''
  const data = useAppData(name)
  const { refreshStatus, target } = useShell()
  const title = data.state === 'ready' ? data.info.title : '読み込み中…'
  useTitle(data.state === 'ready' ? data.info.title : 'Jev アプリ')

  const [runs, setRuns] = useState<RunRecord[]>([])
  const [historyError, setHistoryError] = useState<string | null>(null)
  const loadHistory = useCallback(() => {
    api
      .appRuns(name)
      .then((r) => {
        setRuns(r)
        setHistoryError(null)
      })
      .catch((e: unknown) => setHistoryError(`履歴の取得に失敗: ${errorMessage(e)}`))
  }, [name])
  useEffect(loadHistory, [loadHistory])

  const [report, setReport] = useState<EvalReport | null>(null)
  const [evalBusy, setEvalBusy] = useState(false)
  const [evalError, setEvalError] = useState<string | null>(null)
  const evaluate = () => {
    if (!target) {
      setEvalError('接続先の状態を取得中です。少し待ってから実行してください')
      return
    }
    setEvalError(null)
    setEvalBusy(true)
    api
      .evaluate(name, target)
      .then((rep) => {
        setReport(rep)
        loadHistory()
      })
      .catch((e: unknown) => setEvalError(errorMessage(e)))
      .finally(() => {
        setEvalBusy(false)
        refreshStatus()
      })
  }

  const runHref = `/eval/apps/${encodeURIComponent(name)}/run`
  return (
    <Page crumbs={[{ label: '評価ダッシュボード', to: '/eval' }, { label: title, to: runHref }, { label: '単発判定・履歴' }]}>
      <div className="panel-head">
        <h1>{title}：単発判定・履歴</h1>
        <Link className="btn" to={runHref}>
          ライブ評価に戻る
        </Link>
      </div>
      {data.state === 'error' && <div className="error">{data.message}</div>}
      {data.state === 'ready' && (
        <>
          <p className="muted">{data.info.description}</p>
          <JudgePanel name={name} info={data.info} samples={data.samples} />

          <section className="panel">
            <h2>評価履歴</h2>
            {historyError ? (
              <div className="error">{historyError}</div>
            ) : (
              <div className="scroll">
                <HistoryTable info={data.info} runs={runs} />
              </div>
            )}
          </section>

          <section className="panel">
            <h2>一括評価（結果のみ）</h2>
            <p className="muted small">
              全件の一致率と較正
            </p>
            <div className="row">
              <button onClick={evaluate} disabled={evalBusy}>
                評価データを実行
              </button>
              <span className="muted">
                {evalBusy
                  ? `${data.samples.length}件を実行中…`
                  : report &&
                    `${report.cases[0]?.result.model ?? '-'} ／ ${report.n}件 ／ 平均 ${report.mean_latency_ms.toFixed(0)} ms ／ 合計 ${usd(report.total_cost_usd)}`}
              </span>
            </div>
            {evalError && <div className="error">{evalError}</div>}
            {report && (
              <>
                <MetricsTable info={data.info} report={report} />
                <p className="note">{MATCH_NOTE}</p>
                <CalibrationTables info={data.info} report={report} />
                <h3>ケース別</h3>
                <CasesTable info={data.info} cases={report.cases} />
              </>
            )}
          </section>
        </>
      )}
    </Page>
  )
}
