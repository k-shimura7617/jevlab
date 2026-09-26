import { useEffect, useRef, type CSSProperties } from 'react'
import type { AppInfo, CaseResult, QuestionInfo, Sample } from '../api'
import {
  answerConfidence,
  answerKey,
  answerProbabilities,
  ms,
  optionColor,
  optionLabel,
  pct,
  splitSubject,
  TRIAGE_LABELS,
  TRIAGE_ORDER,
  TRIAGE_RANGES,
  triageOf,
  usd,
  type Triage,
} from '../format'
import { ProbabilityRows } from './report'

export type Entry =
  | { state: 'idle' }
  | { state: 'running' }
  | { state: 'done'; result: CaseResult }
  | { state: 'error'; message: string }

// lane は比較のときにどちらの接続先の結果で絞るか（単独では省略）
export type Filter =
  | { kind: 'all' }
  | { kind: 'label'; key: string; lane?: number }
  | { kind: 'triage'; level: Triage; lane?: number }
  | { kind: 'flag'; qid: string; lane?: number }
  | { kind: 'error' }
  // 比較: 両方判定済みで主ラベルが食い違う／一致する
  | { kind: 'diff' }
  | { kind: 'agree' }

export const sameFilter = (a: Filter, b: Filter) => JSON.stringify(a) === JSON.stringify(b)

/** 主ラベル以外で「目立たせたい」値。noul は true、score は最上位の段階。choice は対象外。 */
export function flagKey(q: QuestionInfo): string | null {
  switch (q.type) {
    case 'noul':
      return 'true'
    case 'score':
      return Object.keys(q.options).at(-1) ?? null
    case 'choice':
      return null
  }
}

/** 完了した 1 件の主ラベルの見立て。 */
export function primaryView(info: AppInfo, c: CaseResult) {
  const q = info.questions.find((x) => x.id === info.primary)
  const a = c.result.answers[info.primary]
  if (!q || !a) return null
  const confidence = answerConfidence(a)
  return { q, a, key: answerKey(a), confidence, triage: triageOf(confidence) }
}

/** 両方の接続先が判定済みのときの主ラベルの一致（どちらかが未完了なら null）。 */
export function agreement(info: AppInfo, entries: readonly Entry[]): boolean | null {
  const keys = entries.map((e) => (e.state === 'done' ? (primaryView(info, e.result)?.key ?? null) : null))
  if (keys.length < 2 || keys.some((k) => k === null)) return null
  return keys.every((k) => k === keys[0])
}

/** 1 件ぶん（接続先ごとの状態の並び）が絞り込みに当たるか。 */
export function matches(info: AppInfo, f: Filter, entries: readonly Entry[]): boolean {
  switch (f.kind) {
    case 'all':
      return true
    case 'error':
      return entries.some((e) => e.state === 'error')
    case 'diff':
      return agreement(info, entries) === false
    case 'agree':
      return agreement(info, entries) === true
    case 'label': {
      const e = entries[f.lane ?? 0]
      return e?.state === 'done' && primaryView(info, e.result)?.key === f.key
    }
    case 'triage': {
      const e = entries[f.lane ?? 0]
      return e?.state === 'done' && primaryView(info, e.result)?.triage === f.level
    }
    case 'flag': {
      const e = entries[f.lane ?? 0]
      if (e?.state !== 'done') return false
      const q = info.questions.find((x) => x.id === f.qid)
      const a = e.result.result.answers[f.qid]
      return q !== undefined && a !== undefined && answerKey(a) === flagKey(q)
    }
  }
}

function StackBar({ q, probs }: { q: QuestionInfo; probs: Record<string, number> }) {
  return (
    <div className="stack" aria-hidden>
      {Object.keys(q.options).map((k) => (
        <span key={k} style={{ width: pct(probs[k] ?? 0), background: optionColor(q, k) }} />
      ))}
    </div>
  )
}

const TRIAGE_SHORT: Record<Triage, string> = { auto: '自動', check: '要確認', escalate: 'エスカレ' }

/** カードに並べる 1 接続先ぶんの状態。 */
export interface LaneEntry {
  label: string
  entry: Entry
}

// 比較のカード全体の状態（どちらかが動いていれば判定中として見せる）
function combinedState(lanes: readonly LaneEntry[]): Entry['state'] {
  const states = lanes.map((l) => l.entry.state)
  if (states.includes('running')) return 'running'
  if (states.includes('error')) return 'error'
  if (states.every((s) => s === 'done')) return 'done'
  return 'idle'
}

function VerdictText({ info, entry }: { info: AppInfo; entry: Entry }) {
  const view = entry.state === 'done' ? primaryView(info, entry.result) : null
  return (
    <>
      {entry.state === 'idle' && <span className="muted">待機中</span>}
      {entry.state === 'running' && (
        <span style={{ color: 'var(--accent)' }}>
          <span className="spinner" /> 判定中…
        </span>
      )}
      {entry.state === 'error' && <span style={{ color: 'var(--warn)' }}>エラー</span>}
      {view && (
        <>
          {optionLabel(view.q, view.a.prediction)}
          <span className="conf">{pct(view.confidence, 0)}</span>
        </>
      )}
    </>
  )
}

export function InboxCard({
  info,
  sample,
  lanes,
  onOpen,
}: {
  info: AppInfo
  sample: Sample
  lanes: readonly LaneEntry[]
  onOpen: () => void
}) {
  const { subject, text } = splitSubject(sample.body)
  const compare = lanes.length > 1
  const entry = lanes[0]?.entry ?? { state: 'idle' }
  const view = entry.state === 'done' ? primaryView(info, entry.result) : null
  const cat = view ? optionColor(view.q, view.key) : undefined
  const secondary = info.questions.filter((q) => q.id !== info.primary)
  const agree = compare ? agreement(info, lanes.map((l) => l.entry)) : null
  const errors = lanes.flatMap((l) => (l.entry.state === 'error' ? [`${compare ? `${l.label}: ` : ''}${l.entry.message}`] : []))
  return (
    <button
      type="button"
      className="mail"
      data-state={compare ? combinedState(lanes) : entry.state}
      data-id={sample.id}
      data-diff={agree === false ? 'true' : undefined}
      style={cat ? ({ '--cat': cat } as CSSProperties) : undefined}
      onClick={onOpen}
    >
      <div className="head">
        <span className="id">#{sample.id}</span>
        {agree === false && <span className="tag diff-tag">不一致</span>}
        {!compare && view && view.triage !== 'auto' && (
          <span className={`tag triage-tag triage-${view.triage}`} title={`確信度 ${TRIAGE_RANGES[view.triage]}`}>
            {TRIAGE_SHORT[view.triage]}
          </span>
        )}
      </div>
      {compare ? (
        <div className="lane-verdicts">
          {lanes.map((l) => {
            const v = l.entry.state === 'done' ? primaryView(info, l.entry.result) : null
            return (
              <div key={l.label} className="lane-verdict" style={v ? ({ '--cat': optionColor(v.q, v.key) } as CSSProperties) : undefined}>
                <span className="lane-name">{l.label}</span>
                <span className="lane-value">
                  <VerdictText info={info} entry={l.entry} />
                </span>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="verdict">
          <VerdictText info={info} entry={entry} />
        </div>
      )}
      {subject && <div className="subject">{subject}</div>}
      <div className="snippet">{text}</div>
      {!compare && view && <StackBar q={view.q} probs={answerProbabilities(view.a)} />}
      {errors.map((m) => (
        <div key={m} className="error small">
          {m}
        </div>
      ))}
      {!compare && entry.state === 'done' && (
        <div className="foot">
          {secondary.map((q) => {
            const a = entry.result.result.answers[q.id]
            if (!a) return null
            const key = answerKey(a)
            if (q.type === 'noul' && key !== 'true') return null
            if (q.type === 'score' && key === Object.keys(q.options)[0]) return null
            return (
              <span key={q.id} className={key === flagKey(q) ? 'tag flag' : 'tag'}>
                {optionLabel(q, a.prediction)}
              </span>
            )
          })}
          <span className="latency">{ms(entry.result.result.latency_ms)}</span>
        </div>
      )}
    </button>
  )
}

export function Bins({
  info,
  entries,
  lane,
  filter,
  onFilter,
}: {
  info: AppInfo
  entries: Entry[]
  // 比較のときの列番号（絞り込みに付ける）
  lane?: number
  filter: Filter
  onFilter: (f: Filter) => void
}) {
  const q = info.questions.find((x) => x.id === info.primary)
  if (!q) return null
  const views = entries.flatMap((e) => (e.state === 'done' ? [primaryView(info, e.result)] : [])).filter((v) => v !== null)
  return (
    <div className="bins" data-testid="bins">
      {Object.keys(q.options).map((k) => {
        const hit = views.filter((v) => v.key === k)
        const f: Filter = { kind: 'label', key: k, lane }
        const active = sameFilter(filter, f)
        const avg = hit.length ? hit.reduce((s, v) => s + v.confidence, 0) / hit.length : null
        return (
          <button
            key={k}
            type="button"
            className={active ? 'bin active' : 'bin'}
            style={{ '--cat': optionColor(q, k) } as CSSProperties}
            onClick={() => onFilter(active ? { kind: 'all' } : f)}
            aria-pressed={active}
          >
            <div className="name">{optionLabel(q, k)}</div>
            <div className="count" key={hit.length} style={{ animation: hit.length ? 'bump .35s ease-out' : undefined }}>
              {hit.length}
            </div>
            <div className="meta">
              {views.length ? pct(hit.length / views.length, 0) : '-'} ／ 平均確信度 {avg === null ? '-' : pct(avg, 0)}
            </div>
            <div className="share">
              <span style={{ width: views.length ? pct(hit.length / views.length) : '0%' }} />
            </div>
          </button>
        )
      })}
    </div>
  )
}

export function TriageBar({
  info,
  entries,
  lane,
  filter,
  onFilter,
}: {
  info: AppInfo
  entries: Entry[]
  // 比較のときの列番号（絞り込みに付ける）
  lane?: number
  filter: Filter
  onFilter: (f: Filter) => void
}) {
  const levels = entries.flatMap((e) => (e.state === 'done' ? [primaryView(info, e.result)?.triage] : []))
  const total = levels.length
  const count = (t: Triage) => levels.filter((x) => x === t).length
  const toggle = (t: Triage) => {
    const f: Filter = { kind: 'triage', level: t, lane }
    onFilter(sameFilter(filter, f) ? { kind: 'all' } : f)
  }
  return (
    <div className="triage" data-testid="triage">
      <div className="small muted">確信度による振り分け</div>
      <div className="triage-bar">
        {TRIAGE_ORDER.map((t) => (
          <button
            key={t}
            type="button"
            className={`triage-${t}`}
            style={{ flexGrow: count(t), flexBasis: 0 }}
            onClick={() => toggle(t)}
            title={`${TRIAGE_LABELS[t]}: ${count(t)}件`}
          >
            {count(t) && total ? pct(count(t) / total, 0) : ''}
          </button>
        ))}
      </div>
      <div className="triage-legend">
        {TRIAGE_ORDER.map((t) => (
          <button
            key={t}
            type="button"
            className={sameFilter(filter, { kind: 'triage', level: t, lane }) ? 'active' : undefined}
            onClick={() => toggle(t)}
          >
            <span className={`dot triage-${t}`} />
            {TRIAGE_LABELS[t]}（{TRIAGE_RANGES[t]}） <strong>{count(t)}</strong>件
          </button>
        ))}
      </div>
    </div>
  )
}

export interface LiveStats {
  done: number
  total: number
  running: number
  errors: number
  elapsedSec: number
  latencies: number[]
  cost: number
  billed: boolean
}

export function StatsCards({ s }: { s: LiveStats }) {
  const mean = s.latencies.length ? s.latencies.reduce((a, b) => a + b, 0) / s.latencies.length : null
  const perMin = s.elapsedSec > 0 ? (s.done / s.elapsedSec) * 60 : 0
  const remain = perMin > 0 && s.done < s.total ? ((s.total - s.done) / perMin) * 60 : null
  const cards: [string, string, string?][] = [
    ['処理済み', `${s.done} / ${s.total}`, `実行中 ${s.running} ／ エラー ${s.errors}`],
    ['経過', `${s.elapsedSec.toFixed(1)} 秒`],
    ['1件あたり', mean === null ? '-' : ms(mean), '応答時間の平均'],
    [
      '最速 / 最遅',
      s.latencies.length ? `${Math.min(...s.latencies).toFixed(0)} / ${Math.max(...s.latencies).toFixed(0)}` : '-',
      'ms',
    ],
    ['スループット', perMin ? `${perMin.toFixed(1)} 件/分` : '-'],
    ['残り推定', remain === null ? '-' : `${remain.toFixed(0)} 秒`],
    ['コスト', usd(s.cost), s.billed ? '今回の実行分' : '課金なし'],
  ]
  return (
    <div className="live-stats" data-testid="live-stats">
      {cards.map(([k, v, sub]) => (
        <div key={k} className="stat">
          <div className="muted small">{k}</div>
          <div className="value">{v}</div>
          {sub && <div className="sub">{sub}</div>}
        </div>
      ))}
    </div>
  )
}

export type LogKind = 'REQ' | 'RES' | 'ERR' | 'SYS'
export interface LogLine {
  id: number
  t: number
  kind: LogKind
  text: string
  // 比較のときの接続先名
  lane?: string
}

export function JevConsole({ lines, live, model }: { lines: LogLine[]; live: boolean; model: string | null }) {
  const box = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  useEffect(() => {
    const el = box.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [lines])
  return (
    <aside className="console" data-testid="console">
      <header>
        <span className="title">jev-console</span>
        <span className={live ? 'live' : 'muted'}>{live ? 'streaming' : (model ?? 'idle')}</span>
      </header>
      <div
        className="lines"
        ref={box}
        onScroll={(e) => {
          const el = e.currentTarget
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
        }}
      >
        {lines.length === 0 && <div className="empty">評価の通信がここに流れます</div>}
        {lines.map((l) => (
          <div key={l.id} className="line">
            <span className="t">+{(l.t / 1000).toFixed(2)}s</span>
            <span className={`k k-${l.kind}`}>{l.kind}</span>
            {l.lane && <span className="lane">{l.lane}</span>}
            {l.text}
          </div>
        ))}
      </div>
    </aside>
  )
}

export function DetailSheet({
  info,
  sample,
  lanes,
  onClose,
}: {
  info: AppInfo
  sample: Sample
  lanes: readonly LaneEntry[]
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const { subject, text } = splitSubject(sample.body)
  const compare = lanes.length > 1
  const agree = compare ? agreement(info, lanes.map((l) => l.entry)) : null
  return (
    <>
      <div className="sheet-backdrop" onClick={onClose} />
      <aside className={compare ? "sheet wide" : "sheet"} role="dialog" aria-label={`#${sample.id} の詳細`}>
        <button type="button" className="close" onClick={onClose} aria-label="閉じる">
          ×
        </button>
        <div className="muted small">
          #{sample.id}
          {agree === false && <span className="tag diff-tag">主ラベルが不一致</span>}
        </div>
        <h2>{subject ?? '本文'}</h2>
        <div className="fulltext">{text}</div>
        <div className={compare ? 'sheet-lanes' : undefined}>
          {lanes.map(({ label, entry }) => {
            const r = entry.state === 'done' ? entry.result.result : null
            return (
              <section key={label}>
                {compare && <h3 className="lane-head">{label}</h3>}
                {entry.state === 'error' && <p className="error">{entry.message}</p>}
                {entry.state !== 'done' && entry.state !== 'error' && <p className="muted">まだ判定していません</p>}
                {r &&
                  info.questions.map((q) => {
                    const a = r.answers[q.id]
                    if (!a) return null
                    return (
                      <div key={q.id}>
                        <h3>
                          {q.title}
                          {q.id === info.primary && <span className="chip primary">主ラベル</span>}
                          {a.type !== 'choice' && <span className="muted small">（値 {(a.value ?? 0).toFixed(2)}）</span>}
                        </h3>
                        <ProbabilityRows q={q} a={a} />
                      </div>
                    )
                  })}
                {r && (
                  <>
                    <h3>メタ情報</h3>
                    <dl>
                      <dt>モデル</dt>
                      <dd>{r.model}</dd>
                      <dt>入力トークン</dt>
                      <dd>{r.input_tokens}</dd>
                      <dt>応答時間</dt>
                      <dd>{ms(r.latency_ms)}</dd>
                      <dt>コスト</dt>
                      <dd>{usd(r.cost_usd)}</dd>
                    </dl>
                  </>
                )}
              </section>
            )
          })}
        </div>
        <h3>評価データの想定ラベル</h3>
        <dl>
          {info.questions.map((q) => {
            const v = sample.labels[q.id]
            return (
              <div key={q.id} style={{ display: 'contents' }}>
                <dt>{q.title}</dt>
                <dd>{v === undefined ? '-' : optionLabel(q, v)}</dd>
              </div>
            )
          })}
        </dl>
        <p className="note">想定ラベルはダミー。不一致＝誤りとは限らない</p>
      </aside>
    </>
  )
}
