// ツール（言い方チェック）の API（src/jevlab/tools/api.py）
import { ApiError, detailOf, type Mode } from '../api'

export type Recipient = 'boss' | 'colleague' | 'subordinate' | 'client' | 'customer' | 'friend' | 'family'
export type Medium = 'chat' | 'mail'
export type Purpose = 'request' | 'apology' | 'thanks' | 'report' | 'decline' | 'casual'
export type Group = 'tone' | 'politeness' | 'clarity' | 'apology'
export type Level = 'ok' | 'warn' | 'bad'
export type Verdict = 'ok' | 'review' | 'caution'
export type ClaudeModel = 'sonnet' | 'opus'

export interface ToneMeta {
  recipients: Record<Recipient, string>
  mediums: Record<Medium, string>
  purposes: Record<Purpose, string>
  groups: Record<Group, string>
  impressions: Record<string, string>
  models: ClaudeModel[]
  default_model: ClaudeModel
  max_sentences: number
}

export interface AspectResult {
  id: string
  group: Group
  title: string
  polarity: 'good' | 'bad'
  value: number
  // 悪さ（0〜1）。良い観点は満たしていない確率、丁寧さは「適切」以外の確率
  badness: number
  level: Level
  note: string
}

export interface ToneResult {
  verdict: Verdict
  verdict_note: string
  // 目的ごとの総合判定（画面で目的を直したときに使う）
  verdicts: Record<Purpose, { verdict: Verdict; note: string }>
  purpose: Purpose
  purpose_confidence: number
  purpose_probabilities: Record<string, number>
  impression: string
  impression_probabilities: Record<string, number>
  aspects: AspectResult[]
  groups_for_purpose: Record<Purpose, Group[]>
  // 目的ごとに見る観点の ID（報告・お断りでは依頼の明確さを問わない、など）
  aspects_for_purpose: Record<Purpose, string[]>
  sentences: { text: string; score: number; flagged: boolean }[]
  truncated: boolean
  model: string
  latency_ms: number
  cost_usd: number
}

export interface RewriteResult {
  rewritten: string
  changes: string[]
  placeholders: string[]
  model: string
  latency_ms: number
  reported_cost_usd: number | null
}


async function call<T>(method: 'GET' | 'POST', path: string, payload?: unknown): Promise<T> {
  const res = await fetch(`/api/tools${path}`, {
    method,
    headers: payload === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  const data: unknown = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}: ${detailOf(data) ?? res.statusText}`)
  // 応答はサーバの型定義どおりである前提で扱う（同一リポジトリで型を揃えている）
  return data as T
}

export const tools = {
  toneMeta: () => call<ToneMeta>('GET', '/tone/meta'),
  tone: (target: Mode, body: { text: string; recipient: Recipient; medium: Medium }) =>
    call<ToneResult>('POST', `/tone?target=${encodeURIComponent(target)}`, body),
  rewrite: (body: {
    text: string
    recipient: Recipient
    medium: Medium
    purpose: Purpose
    findings: { title: string; detail: string }[]
    flagged_sentences: string[]
    model: ClaudeModel
  }) => call<RewriteResult>('POST', '/tone/rewrite', body),
}
