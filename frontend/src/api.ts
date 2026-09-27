// FastAPI（src/jevlab/web.py）の応答型と呼び出し。型はサーバ側の pydantic モデルに合わせる

export type Mode = 'mock' | 'custom' | 'jev'
export type QuestionType = 'choice' | 'score' | 'noul'
export type Label = boolean | number | string

export interface TargetStatus {
  name: Mode
  endpoint: string
  available: boolean
  // 使えないときの理由（切替ボタンの説明に出す）
  reason: string | null
  billed: boolean
  total_usd: number
  cap_usd: number
}

export interface Status {
  default: Mode
  targets: TargetStatus[]
}

export interface QuestionInfo {
  id: string
  type: QuestionType
  title: string
  instructions: string
  // choice の選択肢キー / score の段階番号 / noul の "true"・"false" → 表示名
  options: Record<string, string>
}

export interface AppInfo {
  name: string
  title: string
  description: string
  questions: QuestionInfo[]
  primary: string
  sample_count: number
}

export interface Sample {
  id: string
  body: string
  labels: Record<string, Label>
}

export interface AnswerView {
  type: QuestionType
  prediction: Label
  value: number | null
  confidence: number | null
  probabilities: Record<string, number>
}

export interface JudgeResult {
  answers: Record<string, AnswerView>
  model: string
  input_tokens: number
  latency_ms: number
  cost_usd: number
}

export interface CaseResult {
  sample: Sample
  result: JudgeResult
  ok: Record<string, boolean>
}

export interface CalibrationBin {
  lower: number
  upper: number
  count: number
  mean_confidence: number
  accuracy: number
}

export interface QuestionMetrics {
  id: string
  type: QuestionType
  accuracy: number
  ece: number | null
  reliability: CalibrationBin[]
  mae: number | null
  brier: number | null
  bias: NoulBias | null
}

/** Noul の偏り（正解が「いいえ」の件の P(はい) の平均）と、評価データでいちばんよく分けられる閾値。 */
export interface NoulBias {
  n_true: number
  n_false: number
  mean_yes_when_true: number | null
  mean_yes_when_false: number | null
  best_threshold: number
  accuracy_at_best: number
  accuracy_at_default: number
}

export interface EvalReport {
  app: string
  n: number
  questions: QuestionMetrics[]
  mean_latency_ms: number
  total_cost_usd: number
  cases: CaseResult[]
}

export interface RunRecord {
  at: string
  app: string
  mode: Mode
  model: string
  n: number
  total: number
  mean_accuracy: number
  questions: { id: string; accuracy: number }[]
  mean_latency_ms: number
  total_cost_usd: number
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// 入力の検証エラーに出る項目名を、画面の表記に直す（知らない項目は名前を出さない）
const FIELD_LABELS: Record<string, string> = {
  name: '名前',
  role: '所属・役割',
  scope: '担当範囲',
  slack_user_id: 'Slack ID',
  text: '文面',
  body: '本文',
  subject: '件名',
  from_name: '差出人',
  from_address: '連絡先',
  app_url: '画面へのリンク',
  channel_map: '投稿先',
  inbound_channels: '受信するチャンネル',
  candidate_threshold: '候補を個人情報とみなす確率',
  leftover_threshold: '取りこぼしを疑う確率',
  auto_threshold: '自動で振り分ける確信度',
  review_threshold: '確認待ちにする確信度',
  strong_frustration_at: '強い不満とみなす確率',
  split_margin: '僅差とみなす差',
  threshold: 'しきい値',
}

/** 入力の検証エラー（FastAPI / pydantic の 422）の 1 件を、日本語の短い文にする。 */
function validationMessage(v: unknown): string {
  if (typeof v !== 'object' || v === null) return String(v)
  const e = v as { type?: unknown; loc?: unknown; msg?: unknown; ctx?: unknown }
  const ctx = typeof e.ctx === 'object' && e.ctx !== null ? (e.ctx as Record<string, unknown>) : {}
  // loc の末尾に近い項目名から、画面の表記を探す（body / query などの置き場所や番号は使わない）
  const names = Array.isArray(e.loc) ? e.loc.slice(1).filter((x): x is string => typeof x === 'string') : []
  const field = [...names].reverse().map((n) => FIELD_LABELS[n]).find((x) => x !== undefined) ?? ''
  const msg = typeof e.msg === 'string' ? e.msg.replace(/^Value error, /, '') : ''
  const text =
    (
      {
        missing: '入力してください',
        string_too_short: '入力してください',
        string_too_long: `${String(ctx.max_length)} 文字以内にしてください`,
        too_long: `${String(ctx.max_length)} 件以内にしてください`,
        string_pattern_mismatch: '形式が正しくありません',
        less_than_equal: `${String(ctx.le)} 以下にしてください`,
        greater_than_equal: `${String(ctx.ge)} 以上にしてください`,
        literal_error: '選べない値です',
        enum: '選べない値です',
      } as Record<string, string>
    )[String(e.type)] ?? msg
  return field ? `${field}: ${text}` : text
}

/** API のエラー応答から、画面に出す文を取り出す。 */
export const detailOf = (data: unknown): string | null => {
  if (typeof data !== 'object' || data === null || !('detail' in data)) return null
  const { detail } = data
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(validationMessage).join(' ／ ')
  return JSON.stringify(detail)
}

async function request(path: string, init?: RequestInit): Promise<unknown> {
  const res = await fetch(path, init)
  const data: unknown = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}: ${detailOf(data) ?? res.statusText}`)
  return data
}

// 応答はサーバの型定義どおりである前提で扱う（同一リポジトリで型を揃えている）
const get = <T>(path: string) => request(path) as Promise<T>
const post = <T>(path: string, payload?: unknown) =>
  request(path, {
    method: 'POST',
    headers: payload === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  }) as Promise<T>

const appBase = (name: string) => `/api/apps/${encodeURIComponent(name)}`
// モデルを呼ぶ API は接続先を明示する（サーバの既定に頼ると画面の表示と食い違うため）
const withTarget = (path: string, target: Mode) => `${path}?target=${encodeURIComponent(target)}`

export const api = {
  status: () => get<Status>('/api/status'),
  apps: () => get<AppInfo[]>('/api/apps'),
  app: (name: string) => get<AppInfo>(appBase(name)),
  samples: (name: string) => get<Sample[]>(`${appBase(name)}/samples`),
  judge: (name: string, target: Mode, body: string) =>
    post<JudgeResult>(withTarget(`${appBase(name)}/judge`, target), { body }),
  judgeSample: (name: string, target: Mode, id: string) =>
    post<CaseResult>(withTarget(`${appBase(name)}/samples/${encodeURIComponent(id)}/judge`, target)),
  evaluate: (name: string, target: Mode) => post<EvalReport>(withTarget(`${appBase(name)}/evaluate`, target)),
  summary: (name: string, target: Mode, cases: CaseResult[]) =>
    post<EvalReport>(withTarget(`${appBase(name)}/summary`, target), { cases }),
  latestRuns: () => get<RunRecord[]>('/api/runs/latest'),
  appRuns: (name: string) => get<RunRecord[]>(`${appBase(name)}/runs`),
}

export const errorMessage = (e: unknown): string => (e instanceof Error ? e.message : String(e))
