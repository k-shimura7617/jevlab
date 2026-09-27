import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../../api'
import { Hint } from '../../components/hint'
import { pct } from '../../format'
import { Page, useTitle } from '../../shell'
import { ops, type Curve, type PiiMatrix, type Settings } from '../api'
import { CONFIDENCE_NOTE, staffName } from '../format'
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

/** 2 × 2 の混同行列（行: モデル、列: 人）。 */
function PiiMatrixTable({ title, m, testId }: { title: string; m: PiiMatrix; testId: string }) {
  const cell = (n: number, ok: boolean) => (
    <td className="cell" style={n ? { background: `color-mix(in srgb, var(--${ok ? 'ok' : 'ng'}) 18%, transparent)` } : undefined}>
      {n || <span className="muted">·</span>}
    </td>
  )
  return (
    <table className="confusion" data-testid={testId}>
      <caption className="small">{title}</caption>
      <thead>
        <tr>
          <th className="axis">モデル ＼ 人</th>
          <th>個人情報</th>
          <th>でない</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <th>個人情報</th>
          {cell(m.tp, true)}
          {cell(m.fp, false)}
        </tr>
        <tr>
          <th>でない</th>
          {cell(m.fn, false)}
          {cell(m.tn, true)}
        </tr>
      </tbody>
    </table>
  )
}

/** 個人情報の判定（Kev）と、人の確認の突き合わせ。 */
function PiiEvalSection() {
  const res = usePolling(ops.piiEval, 5000)
  const e = res.data
  return (
    <section className="panel" data-testid="pii-eval">
      <div className="panel-head">
        <h2>個人情報の判定（Kev）</h2>
        {e && <span className="muted small">人が確認した {e.items} 件</span>}
      </div>
      {res.error && <div className="error small">{res.error}</div>}
      {e && e.items === 0 && <p className="muted small">まだありません</p>}
      {e && e.items > 0 && (
        <div className="summary-grid">
          <PiiMatrixTable title="候補（氏名など）" m={e.candidates} testId="pii-eval-candidates" />
          <PiiMatrixTable title={`候補外の残り（${e.leftover_threshold.toFixed(2)} 以上で疑う）`} m={e.leftover} testId="pii-eval-leftover" />
        </div>
      )}
    </section>
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
              <Link to="/admin/settings#guard">取りこぼしを疑う確率</Link> {m.threshold.toFixed(2)}: 見逃し {n} 件中 {m.caught_now} 件を確認に回せた。
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

/** 分類の混同行列（行: Jev の予測、列: 人が確かめた分類）。 */
function ConfusionTable({ confusion, labels }: { confusion: Record<string, Record<string, number>>; labels: Record<string, string> }) {
  const keys = Object.keys(labels)
  if (!Object.keys(confusion).length) return <p className="muted small">人が確かめた件がまだありません。</p>
  return (
    <div className="scroll">
      <table className="confusion" data-testid="confusion">
        <thead>
          <tr>
            <th className="axis">Jev ＼ 人</th>
            {keys.map((k) => (
              <th key={k}>{labels[k]}</th>
            ))}
            <th>正解率</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((p) => {
            const row = confusion[p] ?? {}
            const n = Object.values(row).reduce((s, v) => s + v, 0)
            return (
              <tr key={p}>
                <th>{labels[p]}</th>
                {keys.map((t) => {
                  const v = row[t] ?? 0
                  const ok = p === t
                  return (
                    <td key={t} className="cell" style={v ? { background: `color-mix(in srgb, var(--${ok ? 'ok' : 'ng'}) 18%, transparent)` } : undefined}>
                      {v || <span className="muted">·</span>}
                    </td>
                  )
                })}
                <td className="num">{n ? pct((row[p] ?? 0) / n, 0) : '-'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// 正解は人が確かめた件。目標の誤り率は 5%（細かい条件は選ばせない）
const TARGET_ERROR = 0.05

/** 成績: 分類と担当の当たり具合を見て、ボタン 1 つで閾値を合わせる。 */
export function Tuning() {
  useTitle('成績')
  const { settings, meta, saveSettings } = useOps()
  // 変更した直後のカード（その中に「変更しました」を出す）と、失敗したカードのエラー
  const [done, setDone] = useState<string | null>(null)
  const [failed, setFailed] = useState<{ key: string; message: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const report = usePolling(() => ops.tuning('human', TARGET_ERROR, false), 5000)
  const stats = usePolling(ops.assignment, 5000)
  const st = stats.data
  const staff = settings?.staff ?? []
  const r = report.data
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
  // 提案があるカードに付ける「いま → おすすめ に合わせる」。変更すると、いまの値（縦線）もその場で変わる
  const changeButton = (key: string, now: number, rec: number | null, update: Parameters<typeof change>[1], blocked?: string) =>
    rec === null ? null : (
      <div className="row">
        {rec.toFixed(2) === now.toFixed(2) ? (
          <span className="muted small">おすすめどおり</span>
        ) : blocked ? (
          <span className="error small">{blocked}</span>
        ) : (
          <button type="button" disabled={busy !== null} onClick={() => change(key, update)} data-testid={`apply-${key}`}>
            おすすめに合わせる（{now.toFixed(2)} → {rec.toFixed(2)}）
          </button>
        )}
        {done === key && <span className="ok small">変更しました</span>}
        {failed?.key === key && <span className="error small">{failed.message}</span>}
      </div>
    )
  return (
    <Page wide crumbs={[{ label: '運用', to: '/ops' }, { label: '成績' }]}>
      <div className="panel-head">
        <h1>成績</h1>
        <span className="muted small">人が確かめた件を正解にしています。</span>
      </div>
      <section className="panel">
        <h2>分類</h2>
        {report.error && <div className="error small">{report.error}</div>}
        {!r && <p className="muted">集計中…</p>}
        {r && <ConfusionTable confusion={r.confusion} labels={meta?.categories ?? {}} />}
      </section>
      {r && (
        <section className="panel">
          <h2>自動で振り分ける確信度</h2>
          <p className="muted small">
            誤りが {pct(TARGET_ERROR, 0)} 以下になる、いちばん低い値をおすすめします。
          </p>
          <div className="legend small">
            <span className="lg auto">自動で振り分ける割合</span>
            <span className="lg err">自動分の誤り率</span>
            <span className="lg current">いまの値</span>
            <span className="lg rec">おすすめ</span>
            <Hint text={CONFIDENCE_NOTE} />
          </div>
          <div className="curves">
            <div className="curve-card" data-testid="curve-overall">
              <h3>全体（{r.overall.n} 件）</h3>
              <CurveChart curve={r.overall} targetError={TARGET_ERROR} current={current} />
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
                    {meta?.category_labels[label] ?? label}（{c.n} 件）
                  </h3>
                  <CurveChart curve={c} targetError={TARGET_ERROR} current={now} />
                  <p className="small">{c.note}</p>
                  {changeButton(label, now, c.recommended, (cl) => ({
                    ...cl,
                    label_thresholds: { ...cl.label_thresholds, [label]: c.recommended ?? now },
                  }))}
                </div>
              )
            })}
          </div>
        </section>
      )}
      <section className="panel" data-testid="assign-stats">
        <h2>担当の推定</h2>
        {stats.error && <div className="error small">{stats.error}</div>}
        {!st ? (
          <p className="muted">集計中…</p>
        ) : st.with_suggestion === 0 ? (
          <p className="muted">まだ完了した件がありません。</p>
        ) : (
          <>
            <div className="kpis">
              <div className="kpi">
                <div className="muted small">推定と実際の担当の一致</div>
                <div className="kpi-value">{pct(st.matched / st.with_suggestion, 0)}</div>
                <div className="kpi-sub">
                  人が担当を決めて完了した {st.with_suggestion} 件のうち {st.matched} 件
                  {st.with_suggestion < 10 && '（件数が少ないため参考値）'}
                </div>
              </div>
              <div className="kpi">
                <div className="muted small">自動で割り当てた件</div>
                <div className="kpi-value">{st.auto_assigned}</div>
                <div className="kpi-sub">うち人が変えた {st.auto_changed} 件</div>
              </div>
            </div>
            {st.pairs.length > 0 && (
              <>
                <h3>取り違えの多い組</h3>
                <p className="muted small">
                  左が推定、右が実際の担当です。
                  <br />
                  担当範囲を見直すときに使います。
                </p>
                <ul className="pairs">
                  {st.pairs.map((p) => (
                    <li key={`${p.suggested}-${p.actual}`}>
                      {p.suggested ? staffName(staff, p.suggested) : '推定なし'} → <strong>{staffName(staff, p.actual)}</strong>：{p.count} 件
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </section>
    </Page>
  )
}

/** 管理（開発側）: 個人情報の判定の成績と検知漏れ。 */
export function AdminPii() {
  useTitle('個人情報の判定')
  const { meta } = useOps()
  return (
    <Page wide crumbs={[{ label: '管理', to: '/admin' }, { label: '個人情報の判定' }]}>
      <div className="panel-head">
        <h1>個人情報の判定</h1>
      </div>
      <PiiEvalSection />
      <MissSection labels={meta?.pii_types ?? {}} />
    </Page>
  )
}
