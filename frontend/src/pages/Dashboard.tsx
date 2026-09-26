import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, errorMessage, type AppInfo, type RunRecord } from '../api'
import { localTime, MODE_ORDER, pct } from '../format'
import { ModeBadge, Page, useShell, useTitle } from '../shell'

function RunsMini({ runs }: { runs: RunRecord[] }) {
  if (!runs.length) return <div className="muted small">まだ評価していません</div>
  const sorted = [...runs].sort((a, b) => MODE_ORDER.indexOf(a.mode) - MODE_ORDER.indexOf(b.mode))
  return (
    <>
      <div className="muted small">前回の評価（質問平均の一致率）</div>
      <table className="runs-mini">
        <tbody>
          {sorted.map((r) => (
            <tr key={`${r.mode}-${r.at}`}>
              <td>
                <ModeBadge mode={r.mode} />
              </td>
              <td className="num">
                <strong>{pct(r.mean_accuracy)}</strong>
              </td>
              <td className="num">
                {r.n}/{r.total}件
              </td>
              <td className="num">{r.mean_latency_ms.toFixed(0)} ms</td>
              <td className="muted">{localTime(r.at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

function AppCard({ app, runs }: { app: AppInfo; runs: RunRecord[] }) {
  const href = `/eval/apps/${encodeURIComponent(app.name)}`
  return (
    <section className="panel app-card" data-testid={`app-${app.name}`}>
      <h2>
        <Link to={`${href}/run`}>{app.title}</Link>
      </h2>
      <p className="muted small">{app.description}</p>
      <div>
        {app.questions.map((q) => (
          <span key={q.id} className={q.id === app.primary ? 'chip primary' : 'chip'} title={q.id === app.primary ? '主ラベル' : undefined}>
            {q.title}
          </span>
        ))}
      </div>
      <RunsMini runs={runs} />
      <div className="actions">
        <Link className="btn" to={`${href}/run`}>
          ライブ評価
        </Link>
        <Link className="btn secondary" to={href}>
          単発判定・履歴
        </Link>
        <span className="muted small" style={{ alignSelf: 'center' }}>
          評価データ {app.sample_count}件
        </span>
      </div>
    </section>
  )
}

export function Dashboard() {
  useTitle('Jev アプリ一覧')
  const { apps } = useShell()
  const [latest, setLatest] = useState<RunRecord[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .latestRuns()
      .then(setLatest)
      .catch((e: unknown) => setError(`評価結果の取得に失敗: ${errorMessage(e)}`))
  }, [])

  const evaluated = new Set(latest.map((r) => r.app)).size
  const samples = apps.reduce((n, a) => n + a.sample_count, 0)
  const stats: [string, string][] = [
    ['アプリ', `${apps.length}`],
    ['評価データ（合計）', `${samples}件`],
    ['評価済みアプリ', `${evaluated} / ${apps.length}`],
  ]

  return (
    <Page crumbs={[{ label: '評価ダッシュボード' }]}>
      <h1>Jev アプリ一覧</h1>
      {error && <div className="error">{error}</div>}
      <div className="stats">
        {stats.map(([k, v]) => (
          <div key={k} className="stat">
            <div className="muted small">{k}</div>
            <div className="value">{v}</div>
          </div>
        ))}
      </div>
      <div className="app-grid">
        {apps.map((a) => (
          <AppCard key={a.name} app={a} runs={latest.filter((r) => r.app === a.name)} />
        ))}
      </div>
    </Page>
  )
}
