import { useEffect, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import type { AppInfo, EvalReport, Mode, Sample, Status } from '../api'
import {
  agreement,
  Bins,
  DetailSheet,
  flagKey,
  InboxCard,
  JevConsole,
  matches,
  primaryView,
  sameFilter,
  StatsCards,
  TriageBar,
  type Entry,
  type Filter,
} from '../components/live'
import { Hint } from '../components/hint'
import { CasesTable, ConfusionMatrix, MATCH_NOTE, MetricsTable } from '../components/report'
import { MODE_LABELS, optionLabel, pct, SWITCH_ORDER, TARGET_SHORT, TRIAGE_LABELS, TRIAGE_ORDER, TRIAGE_RANGES, usd } from '../format'
import {
  clampConc,
  clearRun,
  idleLanes,
  MAX_CONC,
  MIN_CONC,
  phaseOf,
  setLaneConcurrency,
  startRun,
  stopRun,
  useRun,
  type Lane,
} from '../runStore'
import { ModeBadge, Page, targetStatus, useShell, useTitle } from '../shell'
import { useAppData } from '../useAppData'

// ---- 実行設定（ブラウザに保存し、次に開いたときも同じ設定にする） ----

const SETTINGS_KEY = 'jevlab.runSettings'

interface Settings {
  compare: boolean
  pair: [Mode, Mode]
  concurrency: Record<Mode, number>
}

// Kev は CPU で動かすため 1 並列。Jev と MOCK は並列にしても 1 件あたりのコストは同じ
const DEFAULT_SETTINGS: Settings = { compare: false, pair: ['jev', 'custom'], concurrency: { jev: 4, custom: 1, mock: 4 } }

const isMode = (v: unknown): v is Mode => typeof v === 'string' && (SWITCH_ORDER as readonly string[]).includes(v)

function parseSettings(raw: unknown): Settings {
  if (typeof raw !== 'object' || raw === null) return DEFAULT_SETTINGS
  const r = raw as Record<string, unknown>
  const p = r.pair
  const pair: [Mode, Mode] = Array.isArray(p) && p.length === 2 && isMode(p[0]) && isMode(p[1]) ? [p[0], p[1]] : DEFAULT_SETTINGS.pair
  const conc = typeof r.concurrency === 'object' && r.concurrency !== null ? (r.concurrency as Record<string, unknown>) : {}
  const concOf = (t: Mode) => {
    const v = conc[t]
    return typeof v === 'number' ? clampConc(v) : DEFAULT_SETTINGS.concurrency[t]
  }
  return { compare: r.compare === true, pair, concurrency: { jev: concOf('jev'), custom: concOf('custom'), mock: concOf('mock') } }
}

function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    return raw === null ? DEFAULT_SETTINGS : parseSettings(JSON.parse(raw))
  } catch (e: unknown) {
    console.error('ライブ評価の設定を読み込めませんでした（既定値を使います）', e)
    return DEFAULT_SETTINGS
  }
}

function useSettings(): [Settings, (f: (s: Settings) => Settings) => void] {
  const [settings, setSettings] = useState(loadSettings)
  const update = (f: (s: Settings) => Settings) =>
    setSettings((prev) => {
      const next = f(prev)
      try {
        localStorage.setItem(SETTINGS_KEY, JSON.stringify(next))
      } catch (e: unknown) {
        console.error('ライブ評価の設定を保存できませんでした', e)
      }
      return next
    })
  return [settings, update]
}

/** 実行できない接続先の理由（使えるなら null）。 */
function unavailableReason(status: Status | null, t: Mode): string | null {
  if (!status) return '接続先の状態を取得中'
  const ts = targetStatus(status, t)
  if (!ts) return `${TARGET_SHORT[t]} の状態が不明`
  return ts.available ? null : `${TARGET_SHORT[t]} は使えません: ${ts.reason ?? '理由不明'}`
}

// ---- 部品 ----

function ConcStepper({ value, onChange, label }: { value: number; onChange: (v: number) => void; label: string }) {
  return (
    <span className="stepper">
      <button type="button" aria-label={`${label}を減らす`} disabled={value <= MIN_CONC} onClick={() => onChange(value - 1)}>
        −
      </button>
      <input
        type="number"
        inputMode="numeric"
        min={MIN_CONC}
        max={MAX_CONC}
        value={value}
        aria-label={label}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <button type="button" aria-label={`${label}を増やす`} disabled={value >= MAX_CONC} onClick={() => onChange(value + 1)}>
        ＋
      </button>
    </span>
  )
}

function laneStats(lane: Lane, elapsedSec: number, billed: boolean) {
  const results = lane.entries.flatMap((e) => (e.state === 'done' ? [e.result] : []))
  return {
    done: lane.entries.filter((e) => e.state === 'done' || e.state === 'error').length,
    total: lane.entries.length,
    running: lane.entries.filter((e) => e.state === 'running').length,
    errors: lane.entries.filter((e) => e.state === 'error').length,
    elapsedSec,
    latencies: results.map((c) => c.result.latency_ms),
    cost: results.reduce((a, c) => a + c.result.cost_usd, 0),
    billed,
  }
}

function LaneHead({ lane, children }: { lane: Lane; children?: ReactNode }) {
  return (
    <div className="lane-title">
      <ModeBadge mode={lane.target} />
      {lane.label !== TARGET_SHORT[lane.target] && <strong>{lane.label}</strong>}
      {children}
    </div>
  )
}

function TriageRates({ info, report }: { info: AppInfo; report: EvalReport }) {
  const rows = TRIAGE_ORDER.map((t) => {
    const cs = report.cases.filter((c) => primaryView(info, c)?.triage === t)
    const same = cs.filter((c) => c.ok[info.primary]).length
    return { t, n: cs.length, rate: cs.length ? same / cs.length : null }
  })
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>区分</th>
            <th className="num">件数</th>
            <th className="num">一致率</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ t, n, rate }) => (
            <tr key={t}>
              <td>
                <span className={`dot triage-${t}`} />
                {TRIAGE_LABELS[t]}（{TRIAGE_RANGES[t]}）
              </td>
              <td className="num">{n}</td>
              <td className="num">{rate === null ? '-' : pct(rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Summary({ info, report, total }: { info: AppInfo; report: EvalReport; total: number }) {
  const q = info.questions.find((x) => x.id === info.primary)
  return (
    <section className="panel" data-testid="summary">
      <h2>集計</h2>
      <p className="muted">
        {report.n} / {total}件を集計 ／ 平均 {report.mean_latency_ms.toFixed(0)} ms ／ 合計 {usd(report.total_cost_usd)}
      </p>
      <div className="summary-grid">
        <div>
          <h3>項目ごとの一致率</h3>
          <MetricsTable info={info} report={report} />
          <h3>確信度の区分ごとの一致率（{q?.title ?? info.primary}）</h3>
          <TriageRates info={info} report={report} />
        </div>
        {q && (
          <div>
            <h3>{q.title}の対応表（行: 想定ラベル ／ 列: 予測）</h3>
            <ConfusionMatrix q={q} cases={report.cases} />
          </div>
        )}
      </div>
      <p className="note">{MATCH_NOTE}</p>
      <details>
        <summary>ケース別（想定ラベル→予測）</summary>
        <CasesTable info={info} cases={report.cases} />
      </details>
    </section>
  )
}

function CompareSummary({ info, lanes, total }: { info: AppInfo; lanes: Lane[]; total: number }) {
  const q = info.questions.find((x) => x.id === info.primary)
  const rows = (lanes[0]?.entries ?? []).map((_, i) => lanes.map((l): Entry => l.entries[i] ?? { state: 'idle' }))
  const judged = rows.map((r) => agreement(info, r)).filter((a) => a !== null)
  const agreed = judged.filter((a) => a).length
  const primaryRate = (r: EvalReport) => r.questions.find((m) => m.id === info.primary)?.accuracy ?? null
  return (
    <section className="panel" data-testid="compare-summary">
      <h2>比較の集計</h2>
      <div className="scroll">
        <table>
          <thead>
            <tr>
              <th>接続先</th>
              <th className="num">件数</th>
              <th className="num">{q?.title ?? info.primary}の一致率</th>
              <th className="num">平均応答</th>
              <th className="num">合計コスト</th>
            </tr>
          </thead>
          <tbody>
            {lanes.map((l) => {
              const rate = l.report ? primaryRate(l.report) : null
              return (
                <tr key={l.label}>
                  <td>
                    <LaneHead lane={l} />
                  </td>
                  {l.report ? (
                    <>
                      <td className="num">
                        {l.report.n} / {total}
                      </td>
                      <td className="num">
                        <strong>{rate === null ? '-' : pct(rate)}</strong>
                      </td>
                      <td className="num">{l.report.mean_latency_ms.toFixed(0)} ms</td>
                      <td className="num">{usd(l.report.total_cost_usd)}</td>
                    </>
                  ) : (
                    <td colSpan={4} className={l.error ? 'error' : 'muted'}>
                      {l.error ?? '集計中…'}
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="agree-line" data-testid="agreement">
        {lanes.map((l) => l.label).join(' と ')} の{q?.title ?? '主ラベル'}の一致率: <strong>{judged.length ? pct(agreed / judged.length) : '-'}</strong>
        <span className="muted small">
          （{agreed} / {judged.length} 件）
        </span>
      </p>
      <div className="lanes">
        {lanes.map((l) => (
          <div key={l.label} className="lane">
            <LaneHead lane={l} />
            {l.report ? (
              <>
                <h3>項目ごとの一致率</h3>
                <MetricsTable info={info} report={l.report} />
                {q && (
                  <>
                    <h3>{q.title}の対応表（行: 想定ラベル ／ 列: 予測）</h3>
                    <ConfusionMatrix q={q} cases={l.report.cases} />
                  </>
                )}
              </>
            ) : (
              <p className={l.error ? 'error' : 'muted'}>{l.error ?? '集計中…'}</p>
            )}
          </div>
        ))}
      </div>
      <p className="note">{MATCH_NOTE}</p>
    </section>
  )
}

// ---- 本体 ----

function LiveRun({ name, info, samples }: { name: string; info: AppInfo; samples: Sample[] }) {
  const { status, target, refreshStatus } = useShell()
  const run = useRun(name)
  const [settings, updateSettings] = useSettings()
  const [count, setCount] = useState<number | null>(null)
  const [filter, setFilter] = useState<Filter>({ kind: 'all' })
  const [selected, setSelected] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const phase = run ? phaseOf(run) : 'idle'
  const running = phase === 'running' || phase === 'stopping'

  // 次に実行する接続先（単独はヘッダーで選んだもの、比較は画面で選んだ 2 つ）
  const setup: Mode[] = settings.compare ? settings.pair : target ? [target] : []
  const blockers = setup.map((t) => unavailableReason(status, t)).filter((r) => r !== null)
  // Jev は課金されるため、件数を選ぶまでは少なめにしておく
  const defaultCount = setup.includes('jev') ? Math.min(10, samples.length) : samples.length
  const n = count ?? defaultCount

  // 実行したことがあればその結果を、なければこれから実行する内容を待機中として出す
  const shownSamples = run ? run.samples : samples.slice(0, n)
  const lanes = run ? run.lanes : idleLanes(setup, shownSamples.length, setup.map((t) => settings.concurrency[t]))
  const compare = lanes.length > 1

  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(Date.now()), 250)
    return () => clearInterval(timer)
  }, [running])

  const start = () => {
    if (blockers.length || !setup.length) return
    setFilter({ kind: 'all' })
    setSelected(null)
    setNow(Date.now())
    startRun({
      info,
      samples: samples.slice(0, n),
      targets: setup,
      concurrency: setup.map((t) => settings.concurrency[t]),
      onSettled: refreshStatus,
    })
  }

  const setConc = (laneIdx: number, t: Mode, v: number) => {
    updateSettings((s) => ({ ...s, concurrency: { ...s.concurrency, [t]: clampConc(v) } }))
    if (running) setLaneConcurrency(name, laneIdx, v)
  }

  const concOf = (l: Lane) => (running ? l.concurrency : settings.concurrency[l.target])
  const elapsedOf = (l: Lane) => (run ? (Math.max(l.endedAt ?? now, run.startedAt) - run.startedAt) / 1000 : 0)
  const billedOf = (t: Mode) => targetStatus(status, t)?.billed ?? t !== 'custom'

  const rows = shownSamples.map((s, i) => ({
    s,
    i,
    lanes: lanes.map((l) => ({ label: l.label, entry: l.entries[i] ?? ({ state: 'idle' } as const) })),
  }))
  const countOf = (f: Filter) => rows.filter((r) => matches(info, f, r.lanes.map((x) => x.entry))).length
  const visible = rows.filter((r) => matches(info, filter, r.lanes.map((x) => x.entry)))
  const errors = countOf({ kind: 'error' })
  const selectedRow = selected === null ? undefined : rows[selected]
  const primaryQ = info.questions.find((q) => q.id === info.primary)
  const flags = info.questions
    .filter((q) => q.id !== info.primary)
    .flatMap((q) => {
      const key = flagKey(q)
      return key === null ? [] : [{ q, key }]
    })
  const chip = (f: Filter, label: string) => (
    <button
      key={label}
      type="button"
      className={sameFilter(filter, f) ? 'filter-chip active' : 'filter-chip'}
      onClick={() => setFilter(sameFilter(filter, f) && f.kind !== 'all' ? { kind: 'all' } : f)}
    >
      {label}
      <span className="n">{countOf(f)}</span>
    </button>
  )
  const firstDone = lanes.flatMap((l) => l.entries).find((e) => e.state === 'done')
  const laneFilter = 'lane' in filter ? filter.lane : undefined
  const pairSelect = (i: 0 | 1) => (
    <select
      aria-label={`比較する接続先 ${i + 1}`}
      value={settings.pair[i]}
      disabled={running}
      onChange={(e) => {
        const v = e.target.value
        if (!isMode(v)) return
        updateSettings((s) => ({ ...s, pair: i === 0 ? [v, s.pair[1]] : [s.pair[0], v] }))
      }}
    >
      {SWITCH_ORDER.map((t) => (
        <option key={t} value={t}>
          {MODE_LABELS[t]}
          {unavailableReason(status, t) ? '（使えません）' : ''}
        </option>
      ))}
    </select>
  )

  return (
    <>
      <section className="panel">
        <div className="row">
          <div className="seg" role="group" aria-label="実行の種類">
            {([false, true] as const).map((c) => (
              <button
                key={String(c)}
                type="button"
                className="seg-btn"
                aria-pressed={settings.compare === c}
                disabled={running}
                onClick={() => updateSettings((s) => ({ ...s, compare: c }))}
              >
                {c ? '比較' : '単独'}
              </button>
            ))}
          </div>
          {settings.compare ? (
            <>
              {pairSelect(0)}
              <span className="muted">と</span>
              {pairSelect(1)}
            </>
          ) : (
            <span className="muted small">接続先はヘッダーの切替で選ぶ{target ? `（いま: ${TARGET_SHORT[target]}）` : ''}</span>
          )}
        </div>
        <div className="row">
          {!running && (
            <button onClick={start} disabled={blockers.length > 0 || !setup.length}>
              {run ? 'もう一度実行' : '評価を開始'}
            </button>
          )}
          {running && (
            <button
              className="danger"
              disabled={phase === 'stopping'}
              onClick={() => stopRun(name, '中止しました（そこまでで集計）')}
            >
              {phase === 'stopping' ? '実行中の分を待っています…' : '中止'}
            </button>
          )}
          {run && !running && (
            <button
              className="secondary"
              onClick={() => {
                setFilter({ kind: 'all' })
                setSelected(null)
                clearRun(name)
              }}
            >
              結果を消す
            </button>
          )}
          <label className="muted" htmlFor="count">
            件数
          </label>
          <select id="count" value={n} disabled={running} onChange={(e) => setCount(Number(e.target.value))}>
            {[...new Set([10, 20, 50, samples.length])]
              .filter((v) => v <= samples.length)
              .map((v) => (
                <option key={v} value={v}>
                  {v === samples.length ? `全件（${v}）` : `${v}件`}
                </option>
              ))}
          </select>
          {!compare && lanes[0] && (
            <>
              <span className="muted">並列数</span>
              <ConcStepper label="並列数" value={concOf(lanes[0])} onChange={(v) => lanes[0] && setConc(0, lanes[0].target, v)} />
            </>
          )}
          <span className="muted small">
            Kev は 1 推奨
            <Hint text="並列数は 1〜8、実行中も変更できる。Jev は並列でもコストは同じ" />
          </span>
        </div>
        {!running &&
          blockers.map((b) => (
            <div key={b} className="error small" data-testid="blocker">
              {b}
            </div>
          ))}
        {run?.restored && <p className="note">再読み込み前の結果です</p>}
        <div className={compare ? 'lanes' : undefined}>
          {lanes.map((l, i) => {
            const s = laneStats(l, elapsedOf(l), billedOf(l.target))
            return (
              <div key={l.label} className={compare ? 'lane' : undefined} data-testid={`lane-${i}`}>
                {compare && (
                  <LaneHead lane={l}>
                    <span className="muted small">並列</span>
                    <ConcStepper label={`${l.label} の並列数`} value={concOf(l)} onChange={(v) => setConc(i, l.target, v)} />
                  </LaneHead>
                )}
                <div className="progress">
                  <span style={{ width: s.total ? pct(s.done / s.total) : '0%' }} />
                </div>
                <StatsCards s={s} />
                {l.error && <div className="error">{l.error}</div>}
              </div>
            )
          })}
        </div>
      </section>

      <div className="live-layout">
        <section className="panel">
          <h2>
            仕分け結果<span className="muted small">（{primaryQ?.title ?? info.primary}）</span>
          </h2>
          <div className={compare ? 'lanes' : undefined}>
            {lanes.map((l, i) => (
              <div key={l.label}>
                {compare && <LaneHead lane={l} />}
                <Bins info={info} entries={l.entries} lane={compare ? i : undefined} filter={filter} onFilter={setFilter} />
                <TriageBar info={info} entries={l.entries} lane={compare ? i : undefined} filter={filter} onFilter={setFilter} />
              </div>
            ))}
          </div>
          <div className="filters" role="toolbar" aria-label="表示の絞り込み">
            <span className="muted small">表示:</span>
            {chip({ kind: 'all' }, 'すべて')}
            {compare ? (
              <>
                {chip({ kind: 'diff' }, '不一致')}
                {chip({ kind: 'agree' }, '一致')}
              </>
            ) : (
              <>
                {primaryQ && Object.keys(primaryQ.options).map((k) => chip({ kind: 'label', key: k }, optionLabel(primaryQ, k)))}
                {flags.map(({ q, key }) => chip({ kind: 'flag', qid: q.id }, optionLabel(q, key)))}
              </>
            )}
            {errors > 0 && chip({ kind: 'error' }, 'エラー')}
            {compare && laneFilter !== undefined && (
              <span className="muted small">（{lanes[laneFilter]?.label} の結果で絞り込み中）</span>
            )}
          </div>
          <div className="inbox" data-testid="inbox">
            {visible.map((r) => (
              <InboxCard key={r.s.id} info={info} sample={r.s} lanes={r.lanes} onOpen={() => setSelected(r.i)} />
            ))}
            {visible.length === 0 && <div className="muted">該当する件はありません</div>}
          </div>
        </section>
        <JevConsole lines={run?.lines ?? []} live={running} model={firstDone?.state === 'done' ? firstDone.result.result.model : null} />
      </div>

      {run && phase === 'finished' && compare && <CompareSummary info={info} lanes={lanes} total={samples.length} />}
      {run && phase === 'finished' && !compare && lanes[0]?.report && (
        <Summary info={info} report={lanes[0].report} total={samples.length} />
      )}

      {selectedRow && <DetailSheet info={info} sample={selectedRow.s} lanes={selectedRow.lanes} onClose={() => setSelected(null)} />}
    </>
  )
}

export function RunPage() {
  const name = useParams().name ?? ''
  const data = useAppData(name)
  const title = data.state === 'ready' ? data.info.title : '…'
  useTitle(`${data.state === 'ready' ? data.info.title : 'Jev アプリ'} - ライブ評価`)
  return (
    <Page wide crumbs={[{ label: '評価ダッシュボード', to: '/eval' }, { label: title }]}>
      <div className="panel-head">
        <h1>{data.state === 'ready' ? `${data.info.title}：ライブ評価` : '読み込み中…'}</h1>
        <Link className="btn secondary" to={`/eval/apps/${encodeURIComponent(name)}`}>
          単発判定・履歴
        </Link>
      </div>
      {data.state === 'error' && <div className="error">{data.message}</div>}
      {data.state === 'ready' && <LiveRun key={name} name={name} info={data.info} samples={data.samples} />}
    </Page>
  )
}
