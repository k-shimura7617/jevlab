import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../../api'
import { Hint } from '../../components/hint'
import { pct } from '../../format'
import { Page, useTitle } from '../../shell'
import { ops, type Curve, type Settings } from '../api'
import { useOps, usePolling } from '../state'

const W = 520
const H = 220
const PAD = { l: 40, r: 12, t: 12, b: 28 }
const x = (t: number) => PAD.l + ((t - 0.5) / 0.49) * (W - PAD.l - PAD.r)
const y = (v: number) => PAD.t + (1 - v) * (H - PAD.t - PAD.b)

/** 閾値ごとの自動処理率と誤り率の曲線。 */
function CurveChart({ curve, targetError, current }: { curve: Curve; targetError: number; current: number }) {
  const line = (pick: (p: Curve['points'][number]) => number | null) =>
    curve.points
      .flatMap((p) => {
        const v = pick(p)
        return v === null ? [] : [`${x(p.threshold).toFixed(1)},${y(Math.min(v, 1)).toFixed(1)}`]
      })
      .join(' ')
  return (
    <svg className="curve" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="閾値ごとの自動処理率と誤り率">
      {[0, 0.25, 0.5, 0.75, 1].map((v) => (
        <g key={v}>
          <line x1={PAD.l} x2={W - PAD.r} y1={y(v)} y2={y(v)} className="grid-line" />
          <text x={PAD.l - 6} y={y(v) + 4} className="axis" textAnchor="end">
            {pct(v, 0)}
          </text>
        </g>
      ))}
      {[0.5, 0.6, 0.7, 0.8, 0.9, 0.99].map((t) => (
        <text key={t} x={x(t)} y={H - 8} className="axis" textAnchor="middle">
          {t.toFixed(2)}
        </text>
      ))}
      <line x1={PAD.l} x2={W - PAD.r} y1={y(targetError)} y2={y(targetError)} className="target-line" />
      <line x1={x(current)} x2={x(current)} y1={PAD.t} y2={H - PAD.b} className="current-line" />
      {curve.recommended !== null && (
        <line x1={x(curve.recommended)} x2={x(curve.recommended)} y1={PAD.t} y2={H - PAD.b} className="rec-line" />
      )}
      <polyline points={line((p) => p.auto_rate)} className="auto-line" />
      <polyline points={line((p) => p.error_rate)} className="err-line" />
    </svg>
  )
}

/** 検知漏れの報告の集計と、「候補外に残っている可能性」の閾値の目安。 */
function MissSection({ labels }: { labels: Record<string, string> }) {
  const [catchRate, setCatchRate] = useState(0.8)
  const summary = usePolling(() => ops.misses(catchRate), 5000)
  const { reload } = summary
  useEffect(() => reload(), [catchRate, reload])
  const m = summary.data && summary.data.catch === catchRate ? summary.data : null
  const n = m ? m.leftovers.length : 0
  return (
    <section className="panel" data-testid="misses">
      <div className="panel-head">
        <h2>検知漏れ</h2>
        <span className="muted small">件の詳細から報告</span>
      </div>
      {summary.error && <div className="error small">{summary.error}</div>}
      {!m && !summary.error && <p className="muted">集計中…</p>}
      {m && m.reports === 0 && <p className="muted small">報告はまだありません</p>}
      {m && m.reports > 0 && (
        <>
          <p className="small">
            報告 {m.reports} 件:{' '}
            {Object.entries(m.by_type)
              .map(([t, c]) => `${labels[t] ?? t} ${c}`)
              .join(' ／ ')}
          </p>
          <p className="small">
            見逃した件の「候補外に残っている可能性」: {m.leftovers.map((v) => v.toFixed(2)).join('、') || 'なし'}
            {m.unscored > 0 && <span className="muted">（判定なし {m.unscored} 件）</span>}
          </p>
          {n > 0 && (
            <div className="row">
              <label className="muted small" htmlFor="miss-catch">
                確認に回したい割合
              </label>
              <select id="miss-catch" value={catchRate} onChange={(e) => setCatchRate(Number(e.target.value))}>
                {[0.5, 0.8, 0.9, 1].map((v) => (
                  <option key={v} value={v}>
                    {pct(v, 0)}
                  </option>
                ))}
              </select>
            </div>
          )}
          {n > 0 && m.suggested !== null && (
            <p className="small" data-testid="miss-suggestion">
              <Link to="/ops/settings#guard">取りこぼしを疑う確率</Link> {m.threshold.toFixed(2)}: 見逃し {n} 件中 {m.caught_now} 件を確認に回せた。
              {m.suggested < m.threshold ? (
                <>
                  {' '}
                  <strong>{m.suggested.toFixed(2)}</strong> にすると {m.caught_suggested} 件。確認に回る件は最大 {m.review_now} → {m.review_suggested} 件（+
                  {m.review_suggested - m.review_now}、判定のある全 {m.scored_items} 件中）
                </>
              ) : (
                ' 変更は不要です'
              )}
            </p>
          )}
        </>
      )}
    </section>
  )
}

export function Tuning() {
  useTitle('閾値の調整')
  const { settings, meta, saveSettings } = useOps()
  const [source, setSource] = useState<'human' | 'expected'>('human')
  const [target, setTarget] = useState(0.05)
  // 変更した直後のカード（その中に「変更しました」を出す）と、失敗したカードのエラー
  const [done, setDone] = useState<string | null>(null)
  const [failed, setFailed] = useState<{ key: string; message: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const report = usePolling(() => ops.tuning(source, target), 5000)
  const { reload } = report
  useEffect(() => reload(), [source, target, reload])
  const r = report.data && report.data.source === source && report.data.target_error === target ? report.data : null
  const current = settings?.classify.auto_threshold ?? 0.9
  const review = settings?.classify.review_threshold ?? 0.5
  const change = (key: string, update: (c: Settings['classify']) => Settings['classify']) => {
    if (!settings) return
    setBusy(key)
    setDone(null)
    setFailed(null)
    saveSettings({ ...settings, classify: update(settings.classify) })
      .then(() => setDone(key))
      .catch((e: unknown) => setFailed({ key, message: errorMessage(e) }))
      .finally(() => setBusy(null))
  }
  // 提案があるカードに付ける「いま → 提案 に変更」。変更すると、いまの値（縦線）もその場で変わる
  const changeButton = (key: string, now: number, rec: number | null, update: Parameters<typeof change>[1], blocked?: string) =>
    rec === null ? null : (
      <div className="row">
        {rec.toFixed(2) === now.toFixed(2) ? (
          <span className="muted small">提案どおり</span>
        ) : blocked ? (
          <span className="error small">{blocked}</span>
        ) : (
          <button type="button" disabled={busy !== null} onClick={() => change(key, update)} data-testid={`apply-${key}`}>
            {now.toFixed(2)} → {rec.toFixed(2)} に変更
          </button>
        )}
        {done === key && <span className="ok small">変更しました</span>}
        {failed?.key === key && <span className="error small">{failed.message}</span>}
      </div>
    )
  return (
    <Page wide crumbs={[{ label: '運用', to: '/ops' }, { label: '閾値の調整' }]}>
      <div className="panel-head">
        <h1>閾値の調整</h1>
        <span className="muted small">誤り率の目標を満たす閾値を提案</span>
      </div>
      <section className="panel">
        <div className="row">
          <span className="muted small">正解に使う件</span>
          <div className="seg" role="group" aria-label="正解に使う件">
            <button type="button" className="seg-btn" aria-pressed={source === 'human'} onClick={() => setSource('human')}>
              人が確認した件
            </button>
            <button type="button" className="seg-btn" aria-pressed={source === 'expected'} onClick={() => setSource('expected')}>
              デモの想定ラベル
            </button>
          </div>
          <label className="muted small" htmlFor="target-error">
            目標の誤り率
          </label>
          <select id="target-error" value={target} onChange={(e) => setTarget(Number(e.target.value))}>
            {[0.01, 0.02, 0.05, 0.1, 0.2].map((v) => (
              <option key={v} value={v}>
                {pct(v, 0)} 以下
              </option>
            ))}
          </select>
          <span className="muted small">いまの共通の閾値 {current.toFixed(2)}</span>
        </div>
        <p className="note">
          {source === 'human' ? (
            <>
              人が確定・確認した件が正解
              <Hint text="確認待ちで確定・抜き取り確認・エスカレーションで完了した件。自動で振り分けて誰も見ていない件は含めない" />
            </>
          ) : (
            'デモの想定ラベルが正解（検証用）'
          )}
        </p>
        {report.error && <div className="error small">{report.error}</div>}
        {!r && <p className="muted">集計中…</p>}
        {r && (
          <>
            <div className="legend small">
              <span className="lg auto">自動で振り分ける割合</span>
              <span className="lg err">自動分の誤り率</span>
              <span className="lg target">目標の誤り率</span>
              <span className="lg current">いまの閾値</span>
              <span className="lg rec">提案</span>
            </div>
            <div className="curves">
              <div className="curve-card" data-testid="curve-overall">
                <h3>全体（{r.overall.n} 件）</h3>
                <CurveChart curve={r.overall} targetError={target} current={current} />
                <p className="small">{r.overall.note}</p>
                {changeButton(
                  'overall',
                  current,
                  r.overall.recommended,
                  (c) => ({ ...c, auto_threshold: r.overall.recommended ?? c.auto_threshold }),
                  r.overall.recommended !== null && r.overall.recommended < review
                    ? `確認待ちの閾値 ${review.toFixed(2)} を下回るため変更できません`
                    : undefined,
                )}
              </div>
              {r.by_label.map((c) => {
                const label = c.label ?? ''
                const now = settings?.classify.label_thresholds[label] ?? current
                return (
                  <div key={label} className="curve-card" data-testid={`curve-${label}`}>
                    <h3>
                      {meta?.categories[label] ?? label}（{c.n} 件）
                    </h3>
                    <CurveChart curve={c} targetError={target} current={now} />
                    <p className="small">{c.note}</p>
                    {changeButton(label, now, c.recommended, (cl) => ({
                      ...cl,
                      label_thresholds: { ...cl.label_thresholds, [label]: c.recommended ?? now },
                    }))}
                  </div>
                )
              })}
            </div>
          </>
        )}
      </section>
      <MissSection labels={meta?.pii_types ?? {}} />
    </Page>
  )
}
