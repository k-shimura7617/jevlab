// 運用画面の表示用の名前・色・計算
import type { Actor, Channel, Decider, Item, PiiAction, PiiFlag, StaffMember, Status } from './api'

export const STATUS_LABELS: Record<Status, string> = {
  queued: '処理待ち',
  processing: '処理中',
  pii_review: '個人情報の確認待ち',
  review: '分類の確認待ち',
  escalated: 'エスカレーション',
  routed: '振り分け済み',
  closed: '対応完了',
  error: 'エラー',
}

export const STATUS_SHORT: Record<Status, string> = {
  queued: '待機',
  processing: '処理中',
  pii_review: '個人情報の確認',
  review: '分類の確認',
  escalated: 'エスカレ',
  routed: '振り分け済',
  closed: '完了',
  error: 'エラー',
}

export const CHANNEL_SHORT: Record<Channel, string> = { mail: 'メール', chat: 'チャット', csv: 'CSV', api: 'API', slack: 'Slack' }
export const CHANNEL_ICON: Record<Channel, string> = { mail: '✉', chat: '#', csv: '⇪', api: '⚙', slack: '◆' }

export const ACTOR_LABELS: Record<Actor, string> = {
  system: 'システム',
  kev: 'Kev',
  jev: 'Jev',
  mock: 'MOCK',
  human: '担当者',
  connector: 'コネクタ',
}

export const DECIDER_LABELS: Record<Decider, string> = { jev: 'Jev', kev: 'Kev', mock: 'MOCK', human: '担当者' }

export const ACTION_LABELS: Record<PiiAction, string> = { allow: '検出のみ', mask: 'マスク', block: 'ブロック' }
export const ACTION_NOTES: Record<PiiAction, string> = {
  allow: '記録だけ残し、そのまま Jev に送る',
  mask: '【種類】に置き換えてから送る',
  block: 'Jev には送らない',
}

export const PRIORITY_LABELS: Record<string, string> = {
  frustration: '不満度',
  urgent: '緊急度',
  refund: '返金・補償',
  publicity: '公になる恐れ',
}

// 既存のメール仕分けの選択肢の並び（format.ts の optionColor と同じ色の割り当て）
const CATEGORY_ORDER = ['inquiry', 'complaint', 'thanks']
export const categoryColor = (key: string | null): string => {
  if (key === null) return 'var(--line)'
  if (key === 'other') return 'var(--c-other)'
  const i = CATEGORY_ORDER.indexOf(key)
  return i < 0 ? 'var(--c-other)' : `var(--c${i})`
}

/** 優先度（観点ごとの値の重み付き平均、0〜1）。サーバの priority_score と同じ式。 */
export function priorityOf(item: Item, weights: Record<string, number>): number {
  const entries = Object.entries(weights).filter(([, w]) => w > 0)
  const total = entries.reduce((s, [, w]) => s + w, 0)
  if (total <= 0) return 0
  return entries.reduce((s, [k, w]) => s + (item.priority[k] ?? 0) * w, 0) / total
}

export const priorityLevel = (p: number): 'high' | 'mid' | 'low' => (p >= 0.6 ? 'high' : p >= 0.35 ? 'mid' : 'low')
export const PRIORITY_LEVEL_LABELS = { high: '高', mid: '中', low: '低' } as const

export function relativeTime(iso: string, now: number = Date.now()): string {
  const sec = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000))
  if (sec < 60) return `${sec}秒前`
  if (sec < 3600) return `${Math.floor(sec / 60)}分前`
  if (sec < 86400) return `${Math.floor(sec / 3600)}時間前`
  return new Date(iso).toLocaleDateString('ja-JP', { month: 'numeric', day: 'numeric' })
}

export const clockTime = (iso: string) =>
  new Date(iso).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit', second: '2-digit' })

/** 受信からの経過（エスカレーションの対応目安に使う）。 */
export function elapsedLabel(fromIso: string, now: number = Date.now()): string {
  const min = Math.max(0, Math.floor((now - new Date(fromIso).getTime()) / 60000))
  if (min < 60) return `${min}分`
  return `${Math.floor(min / 60)}時間${min % 60}分`
}

// Jev の確信度は最大確率そのものではなく、選択肢の数で補正した値（(最大確率 − 1/N) ÷ (1 − 1/N)）
export const CONFIDENCE_NOTE = '確信度＝最大確率を選択肢の数で補正した値（最大確率より低く出る）'

export const PII_FLAG_LABELS: Record<PiiFlag, string> = {
  detected: '検出済み',
  // API の値は possible_name のままだが、実際は種類を問わず「確定できなかった候補がある」の意味
  possible_name: '未確定の候補',
  possible_missed: '見落としの可能性',
}
export const PII_FLAG_NOTES: Record<PiiFlag, string> = {
  detected: 'すべて確定済み。マスクすれば送れる',
  possible_name: '確定できなかった候補がある。本文を見て判断',
  possible_missed: '候補外に残っているかも。本文を確認',
}
/** 一括で流してよい件（見つかった個人情報がすべて確定済みで、迷いがない）。 */
export const safeToBulk = (item: Item) => item.pii_flags.length > 0 && item.pii_flags.every((f) => f === 'detected')

export const staffName = (staff: StaffMember[] | undefined, id: string | null) =>
  id === null ? '未割り当て' : (staff?.find((s) => s.id === id)?.name ?? id)

export const titleOf = (item: Item) => item.subject || item.body.replace(/\s+/g, ' ').slice(0, 40)
