// 運用画面で共通に使う部品
import type { CSSProperties, ReactNode } from 'react'
import { Link } from 'react-router'
import { pct, usd } from '../format'
import type { Item, Meta, OpsEvent, Span, Status } from './api'
import {
  ACTOR_LABELS,
  categoryColor,
  CHANNEL_ICON,
  CHANNEL_SHORT,
  DECIDER_LABELS,
  PRIORITY_LABELS,
  PRIORITY_LEVEL_LABELS,
  priorityLevel,
  priorityOf,
  clockTime,
  relativeTime,
  STATUS_LABELS,
  STATUS_SHORT,
  titleOf,
} from './format'

export const StatusChip = ({ status, short }: { status: Status; short?: boolean }) => (
  <span className={`status-chip st-${status}`} title={STATUS_LABELS[status]}>
    {short ? STATUS_SHORT[status] : STATUS_LABELS[status]}
  </span>
)

export function CategoryTag({ meta, item, showConfidence = true }: { meta: Meta | null; item: Item; showConfidence?: boolean }) {
  if (item.category === null)
    return <span className="muted small">{item.pii_decision === 'blocked' ? '個人情報のため Jev 未送信' : '未分類'}</span>
  return (
    <span className="cat-tag" style={{ '--cat': categoryColor(item.category) } as CSSProperties}>
      {meta?.categories[item.category] ?? item.category}
      {showConfidence && item.confidence !== null && item.decided_by !== 'human' && (
        <span className="conf">{pct(item.confidence, 0)}</span>
      )}
      {item.decided_by && <span className="by">{DECIDER_LABELS[item.decided_by]}</span>}
    </span>
  )
}

export function PriorityBadge({ item, weights }: { item: Item; weights: Record<string, number> }) {
  if (!Object.keys(item.priority).length) return null
  const p = priorityOf(item, weights)
  const level = priorityLevel(p)
  const detail = Object.entries(item.priority)
    .map(([k, v]) => `${PRIORITY_LABELS[k] ?? k} ${pct(v, 0)}`)
    .join(' ／ ')
  return (
    <span className={`prio prio-${level}`} title={`優先度 ${pct(p, 0)}（${detail}）`}>
      優先度 {PRIORITY_LEVEL_LABELS[level]}
      <span className="prio-bar" aria-hidden>
        <span style={{ width: pct(p) }} />
      </span>
    </span>
  )
}

/** 受付箱の 1 行。 */
export function ItemRow({
  item,
  meta,
  weights,
  to,
  extra,
  active,
  plain,
}: {
  item: Item
  meta: Meta | null
  weights?: Record<string, number>
  to?: string
  extra?: ReactNode
  active?: boolean
  // 状態と「個人情報あり」を出さない（個人情報の確認の画面では自明なため）
  plain?: boolean
}) {
  const body = (
    <>
      <span className="ch" title={CHANNEL_SHORT[item.channel]}>
        {CHANNEL_ICON[item.channel]}
      </span>
      <span className="who">
        <strong>{item.from_name || '差出人不明'}</strong>
        <span className="muted small">{item.id}</span>
      </span>
      <span className="what">
        <span className="subject">{titleOf(item)}</span>
        <span className="snippet muted small">{item.subject ? item.body.replace(/\s+/g, ' ') : ''}</span>
      </span>
      <span className="tags">
        {!plain && <StatusChip status={item.status} short />}
        {item.category !== null ? (
          <CategoryTag meta={meta} item={item} />
        ) : (
          item.pii_decision === 'blocked' && <span className="muted small">個人情報のため Jev 未送信</span>
        )}
        {!plain && item.pii.some((s) => s.confirmed) && (
          <span className="pii-flag" title="個人情報を含む">
            個人情報あり
          </span>
        )}
        {weights && <PriorityBadge item={item} weights={weights} />}
        {extra}
      </span>
      <span className="when muted small" title={new Date(item.received_at).toLocaleString('ja-JP')}>
        {relativeTime(item.received_at)}
      </span>
    </>
  )
  const cls = `item-row${active ? ' active' : ''}`
  return to ? (
    <Link className={cls} to={to} data-id={item.id} data-status={item.status}>
      {body}
    </Link>
  ) : (
    <div className={cls} data-id={item.id} data-status={item.status}>
      {body}
    </div>
  )
}

/** 個人情報の位置をハイライトした本文。 */
export function PiiText({
  text,
  spans,
  labels,
  onToggle,
}: {
  text: string
  spans: Span[]
  labels: Record<string, string>
  onToggle?: (index: number) => void
}) {
  const sorted = spans.map((s, i) => ({ s, i })).sort((a, b) => a.s.start - b.s.start)
  const parts: ReactNode[] = []
  let pos = 0
  for (const { s, i } of sorted) {
    if (s.start < pos) continue
    parts.push(text.slice(pos, s.start))
    const cls = `pii-mark${s.confirmed ? '' : ' off'}${s.source === 'human' ? ' human' : ''}`
    const title = `${labels[s.type] ?? s.type}${s.score !== null ? `（判定 ${s.score.toFixed(2)}）` : s.source === 'human' ? '（人が追加）' : '（規則で確定）'}${s.confirmed ? '' : ' ／ 個人情報ではないと判定'}`
    parts.push(
      onToggle ? (
        <button key={`${s.start}-${s.end}`} type="button" className={cls} title={`${title}。クリックで切り替え`} onClick={() => onToggle(i)}>
          {text.slice(s.start, s.end)}
          <span className="pii-type">{labels[s.type] ?? s.type}</span>
        </button>
      ) : (
        <mark key={`${s.start}-${s.end}`} className={cls} title={title}>
          {text.slice(s.start, s.end)}
          <span className="pii-type">{labels[s.type] ?? s.type}</span>
        </mark>
      ),
    )
    pos = s.end
  }
  parts.push(text.slice(pos))
  return <>{parts}</>
}

export function Timeline({ events }: { events: OpsEvent[] }) {
  return (
    <ol className="timeline">
      {events.map((e) => (
        <li key={e.id} className={`ev ev-${e.kind} actor-${e.actor}`}>
          <span className="dot" aria-hidden />
          <div className="ev-head">
            <span className="actor">{ACTOR_LABELS[e.actor]}</span>
            <span className="muted small">{clockTime(e.at)}</span>
            {typeof e.data.latency_ms === 'number' && <span className="muted small">{e.data.latency_ms.toFixed(0)} ms</span>}
            {typeof e.data.model === 'string' && <span className="muted small">{e.data.model}</span>}
          </div>
          <div className="ev-msg">{e.message}</div>
        </li>
      ))}
    </ol>
  )
}

export function FieldsList({ fields, meta }: { fields: Record<string, string | null>; meta: Meta | null }) {
  const entries = Object.entries(fields)
  if (!entries.length) return <span className="muted small">抽出なし</span>
  return (
    <dl className="fields">
      {entries.map(([k, v]) => (
        <div key={k} className="field">
          <dt>{meta?.fields[k] ?? k}</dt>
          <dd className={v === null ? 'muted' : undefined}>{v ?? '該当なし'}</dd>
        </div>
      ))}
    </dl>
  )
}

export function WeightSliders({
  weights,
  onChange,
}: {
  weights: Record<string, number>
  onChange: (next: Record<string, number>) => void
}) {
  return (
    <div className="weights" role="group" aria-label="優先度の重み">
      {Object.entries(weights).map(([k, v]) => (
        <label key={k} className="weight">
          <span>{PRIORITY_LABELS[k] ?? k}</span>
          <input
            type="range"
            min={0}
            max={3}
            step={0.1}
            value={v}
            aria-label={`${PRIORITY_LABELS[k] ?? k}の重み`}
            onChange={(e) => onChange({ ...weights, [k]: Number(e.target.value) })}
          />
          <span className="num">{v.toFixed(1)}</span>
        </label>
      ))}
    </div>
  )
}

export function Kpi({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: 'ok' | 'warn' | 'ng' }) {
  return (
    <div className={`kpi${tone ? ` kpi-${tone}` : ''}`}>
      <div className="muted small">{label}</div>
      <div className="kpi-value">{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  )
}

export const costText = (usdValue: number) => (usdValue > 0 ? usd(usdValue) : '$0')

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty-state">{children}</div>
}

/** 一覧の 1 行の左にチェックボックスを付ける（行そのものは選択用のボタンのまま）。 */
export function CheckRow({
  checked,
  onCheck,
  label,
  children,
}: {
  checked: boolean
  onCheck: (v: boolean) => void
  label: string
  children: ReactNode
}) {
  return (
    <div className={`check-row${checked ? ' checked' : ''}`}>
      <input type="checkbox" checked={checked} aria-label={label} onChange={(e) => onCheck(e.target.checked)} />
      {children}
    </div>
  )
}

/** 一括操作の帯（選んだ件数と操作ボタン）。 */
export function BulkBar({
  count,
  total,
  onAll,
  onNone,
  children,
}: {
  count: number
  total: number
  onAll: () => void
  onNone: () => void
  children: ReactNode
}) {
  return (
    <div className={`bulk-bar${count ? ' on' : ''}`} data-testid="bulk-bar">
      <span className="small">
        <strong>{count}</strong> / {total} 件を選択
      </span>
      <button type="button" className="link-btn" onClick={onAll} disabled={!total}>
        表示中をすべて選ぶ
      </button>
      <button type="button" className="link-btn" onClick={onNone} disabled={!count}>
        選択を外す
      </button>
      <span className="spacer" />
      {children}
    </div>
  )
}

/** 自動保存の状態（保存できないときは理由）。 */
export function SaveState({ status, error }: { status: string | null; error: string | null }) {
  if (error)
    return (
      <span className="error small" role="alert" data-testid="save-state">
        保存できません: {error}
      </span>
    )
  return (
    <span className="muted small" data-testid="save-state">
      {status ?? '自動保存'}
    </span>
  )
}
