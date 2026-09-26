// ライブ評価の実行状態。画面を移動しても実行を続け、結果を保持するためコンポーネントの外に持つ。
// 終わった結果は sessionStorage にも残し、再読み込み後に戻す（タブを閉じると消える）。
import { useSyncExternalStore } from 'react'
import { api, ApiError, errorMessage, type AppInfo, type CaseResult, type EvalReport, type Mode, type Sample } from './api'
import { primaryView, type Entry, type LogKind, type LogLine } from './components/live'
import { answerBrief, TARGET_SHORT, usd } from './format'

export const MIN_CONC = 1
export const MAX_CONC = 8
export const clampConc = (v: number) =>
  Math.min(MAX_CONC, Math.max(MIN_CONC, Number.isFinite(v) ? Math.trunc(v) : MIN_CONC))
// 比較では 2 系統ぶん流れるため、単独のときより多めに残す
const LOG_LIMIT = 600
const STORAGE_PREFIX = 'jevlab.run.'
const SAVE_VERSION = 1

/** 1 つの接続先での実行（比較では 2 本並ぶ）。 */
export interface Lane {
  target: Mode
  // 列見出し・ログの識別名。同じ接続先を 2 本並べたときは A/B を付ける
  label: string
  entries: Entry[]
  concurrency: number
  stopping: boolean
  finished: boolean
  endedAt: number | null
  report: EvalReport | null
  error: string | null
}

export interface Run {
  app: string
  samples: Sample[]
  lanes: Lane[]
  // Date.now() 基準（再読み込み後も経過時間を出せるように）
  startedAt: number
  lines: LogLine[]
  // 再読み込み前の結果を sessionStorage から戻したもの
  restored: boolean
}

export type Phase = 'running' | 'stopping' | 'finished'

export function phaseOf(run: Run): Phase {
  const open = run.lanes.filter((l) => !l.finished)
  if (!open.length) return 'finished'
  return open.every((l) => l.stopping) ? 'stopping' : 'running'
}

export const laneLabels = (targets: readonly Mode[]): string[] =>
  targets.map((t, i) => (targets.filter((x) => x === t).length > 1 ? `${TARGET_SHORT[t]}·${'AB'[i] ?? i}` : TARGET_SHORT[t]))

/** 実行前の表示用（全件を待機中にしたもの）。 */
export const idleLanes = (targets: readonly Mode[], n: number, concurrency: readonly number[]): Lane[] => {
  const labels = laneLabels(targets)
  return targets.map((target, i) => ({
    target,
    label: labels[i] ?? TARGET_SHORT[target],
    entries: Array.from({ length: n }, (): Entry => ({ state: 'idle' })),
    concurrency: clampConc(concurrency[i] ?? MIN_CONC),
    stopping: false,
    finished: false,
    endedAt: null,
    report: null,
    error: null,
  }))
}

// 実行中だけ必要な情報。非同期の完了通知から参照し、画面には出さない
interface LaneRuntime {
  queue: number[]
  inflight: number
  cases: Map<number, CaseResult>
}

interface Runtime {
  info: AppInfo
  lanes: LaneRuntime[]
  onSettled: () => void
}

const runs = new Map<string, Run>()
const runtimes = new Map<string, Runtime>()
// sessionStorage を見に行ったアプリ（毎回読み直さない）
const checked = new Set<string>()
const listeners = new Set<() => void>()
let lineSeq = 0

const subscribe = (listener: () => void) => {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
const notify = () => listeners.forEach((l) => l())

const alive = (app: string, rt: Runtime) => runtimes.get(app) === rt

function update(app: string, f: (run: Run) => Run) {
  const run = runs.get(app)
  if (!run) return
  runs.set(app, f(run))
  notify()
}

const updateLane = (app: string, lane: number, f: (l: Lane) => Lane) =>
  update(app, (run) => ({ ...run, lanes: run.lanes.map((l, i) => (i === lane ? f(l) : l)) }))

const setEntry = (app: string, lane: number, idx: number, entry: Entry) =>
  updateLane(app, lane, (l) => ({ ...l, entries: l.entries.map((e, i) => (i === idx ? entry : e)) }))

function log(app: string, kind: LogKind, text: string, lane?: string) {
  update(app, (run) => {
    lineSeq += 1
    const line: LogLine = { id: lineSeq, t: Date.now() - run.startedAt, kind, text, lane }
    return { ...run, lines: [...run.lines.slice(-(LOG_LIMIT - 1)), line] }
  })
}

// 比較のときだけログに接続先を付ける
const laneTag = (app: string, lane: number): string | undefined => {
  const run = runs.get(app)
  return run && run.lanes.length > 1 ? run.lanes[lane]?.label : undefined
}

// ---- 保存・復元 ----

interface Saved {
  v: typeof SAVE_VERSION
  run: Run
}

const isSaved = (x: unknown): x is Saved =>
  typeof x === 'object' && x !== null && 'v' in x && x.v === SAVE_VERSION && 'run' in x && typeof x.run === 'object'

function persist(app: string) {
  const run = runs.get(app)
  if (!run || phaseOf(run) !== 'finished') return
  const saved: Saved = { v: SAVE_VERSION, run: { ...run, restored: false } }
  try {
    sessionStorage.setItem(STORAGE_PREFIX + app, JSON.stringify(saved))
  } catch (e: unknown) {
    log(app, 'ERR', `結果をブラウザに保存できませんでした（再読み込みで消えます）: ${errorMessage(e)}`)
  }
}

function forget(app: string) {
  try {
    sessionStorage.removeItem(STORAGE_PREFIX + app)
  } catch (e: unknown) {
    console.error(`保存済みの結果を消せませんでした（${app}）`, e)
  }
}

function restore(app: string): Run | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_PREFIX + app)
    if (raw === null) return null
    const data: unknown = JSON.parse(raw)
    if (isSaved(data)) return { ...data.run, restored: true }
    console.error(`保存済みの結果の形式が違うため読み込みません（${app}）`)
  } catch (e: unknown) {
    console.error(`保存済みの結果を読み込めませんでした（${app}）`, e)
  }
  return null
}

function getRun(app: string): Run | null {
  if (!runs.has(app) && !checked.has(app)) {
    checked.add(app)
    const saved = restore(app)
    if (saved) runs.set(app, saved)
  }
  return runs.get(app) ?? null
}

// ---- 実行 ----

async function judge(app: string, rt: Runtime, lane: number, idx: number) {
  const run = runs.get(app)
  const s = run?.samples[idx]
  const l = run?.lanes[lane]
  if (!s || !l) return
  const tag = laneTag(app, lane)
  const { info } = rt
  setEntry(app, lane, idx, { state: 'running' })
  log(app, 'REQ', `#${s.id} POST judge {${info.questions.map((q) => q.id).join(', ')}} 本文 ${s.body.length}字`, tag)
  try {
    const c = await api.judgeSample(app, l.target, s.id)
    if (!alive(app, rt)) return
    rt.lanes[lane]?.cases.set(idx, c)
    setEntry(app, lane, idx, { state: 'done', result: c })
    const answers = info.questions
      .map((q) => {
        const a = c.result.answers[q.id]
        return a ? `${q.id}=${answerBrief(q, a)}` : null
      })
      .filter((x) => x !== null)
      .join(' ')
    const v = primaryView(info, c)
    log(
      app,
      'RES',
      `#${s.id} ${answers}${v ? ` conf=${v.confidence.toFixed(2)}` : ''} ${c.result.latency_ms.toFixed(0)}ms ${usd(c.result.cost_usd)}`,
      tag,
    )
  } catch (e: unknown) {
    if (!alive(app, rt)) return
    const message = errorMessage(e)
    setEntry(app, lane, idx, { state: 'error', message })
    log(app, 'ERR', `#${s.id} ${message}`, tag)
    // 予算上限に達したら残りを流しても全部失敗するので、その接続先だけ止める
    if (e instanceof ApiError && e.status === 409) stopLane(app, rt, lane, `予算上限のため中止しました: ${message}`)
  }
}

function pump(app: string, rt: Runtime, lane: number) {
  const l = runs.get(app)?.lanes[lane]
  const lr = rt.lanes[lane]
  if (!l || !lr || !alive(app, rt) || l.finished) return
  while (!l.stopping && lr.inflight < l.concurrency && lr.queue.length) {
    const idx = lr.queue.shift()
    if (idx === undefined) break
    lr.inflight += 1
    void judge(app, rt, lane, idx).finally(() => {
      lr.inflight -= 1
      pump(app, rt, lane)
    })
  }
  if (lr.inflight === 0 && (l.stopping || !lr.queue.length)) finishLane(app, rt, lane)
}

function finishLane(app: string, rt: Runtime, lane: number) {
  const run = runs.get(app)
  const l = run?.lanes[lane]
  const lr = rt.lanes[lane]
  if (!run || !l || !lr || l.finished) return
  const end = Date.now()
  updateLane(app, lane, (x) => ({ ...x, finished: true, endedAt: end }))
  const cases = [...lr.cases.entries()].sort(([a], [b]) => a - b).map(([, c]) => c)
  log(app, 'SYS', `完了: ${cases.length} / ${run.samples.length}件 ／ ${((end - run.startedAt) / 1000).toFixed(1)} 秒`, laneTag(app, lane))
  persist(app)
  if (!cases.length) {
    rt.onSettled()
    return
  }
  api
    .summary(app, l.target, cases)
    .then((report) => alive(app, rt) && updateLane(app, lane, (x) => ({ ...x, report })))
    .catch(
      (e: unknown) =>
        alive(app, rt) && updateLane(app, lane, (x) => ({ ...x, error: `集計・保存に失敗: ${errorMessage(e)}` })),
    )
    .finally(() => {
      if (alive(app, rt)) persist(app)
      rt.onSettled()
    })
}

function stopLane(app: string, rt: Runtime, lane: number, message: string) {
  const l = runs.get(app)?.lanes[lane]
  if (!l || l.finished || l.stopping) return
  updateLane(app, lane, (x) => ({ ...x, stopping: true, error: message }))
  log(app, 'SYS', message, laneTag(app, lane))
  pump(app, rt, lane)
}

export interface StartOptions {
  info: AppInfo
  samples: Sample[]
  targets: Mode[]
  concurrency: number[]
  // 1 本終わるごとに呼ぶ（使用量の表示を更新する）
  onSettled: () => void
}

export function startRun({ info, samples, targets, concurrency, onSettled }: StartOptions) {
  const app = info.name
  const rt: Runtime = {
    info,
    lanes: targets.map(() => ({ queue: samples.map((_, i) => i), inflight: 0, cases: new Map() })),
    onSettled,
  }
  const lanes = idleLanes(targets, samples.length, concurrency)
  runtimes.set(app, rt)
  runs.set(app, { app, samples, lanes, startedAt: Date.now(), lines: [], restored: false })
  forget(app)
  notify()
  const plan = lanes.map((l) => `${l.label}（並列 ${l.concurrency}）`).join(' ／ ')
  log(app, 'SYS', `開始: ${info.title} ${samples.length}件 ／ ${plan}`)
  targets.forEach((_, i) => pump(app, rt, i))
}

export function stopRun(app: string, message: string) {
  const rt = runtimes.get(app)
  const run = runs.get(app)
  if (!rt || !run) return
  run.lanes.forEach((_, i) => stopLane(app, rt, i, message))
}

export function setLaneConcurrency(app: string, lane: number, value: number) {
  const c = clampConc(value)
  const l = runs.get(app)?.lanes[lane]
  if (!l || l.concurrency === c) return
  updateLane(app, lane, (x) => ({ ...x, concurrency: c }))
  const rt = runtimes.get(app)
  if (rt && !l.finished) {
    log(app, 'SYS', `並列数を ${c} に変更`, laneTag(app, lane))
    pump(app, rt, lane)
  }
}

export function clearRun(app: string) {
  runtimes.delete(app)
  runs.delete(app)
  forget(app)
  notify()
}

// ---- React から使う ----

export const useRun = (app: string): Run | null => useSyncExternalStore(subscribe, () => getRun(app))

// 配列は毎回作り直すと同一判定できないため、改行区切りの文字列で比べる
const activeKey = () =>
  [...runs.values()]
    .filter((r) => phaseOf(r) !== 'finished')
    .map((r) => r.app)
    .join('\n')

/** 実行中のアプリ名。 */
export function useActiveApps(): string[] {
  const key = useSyncExternalStore(subscribe, activeKey)
  return key ? key.split('\n') : []
}
