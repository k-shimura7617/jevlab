// 運用（受付箱）の API（src/jevlab/ops/api.py）。型はサーバの pydantic モデルに合わせる
import type { AnswerView, Mode } from '../api'
import { ApiError, detailOf } from '../api'

// slack は実際の Slack ワークスペースからの受信。chat は画面上の疑似チャット
export type Channel = 'mail' | 'chat' | 'csv' | 'api' | 'slack'
export type Status = 'queued' | 'processing' | 'pii_review' | 'review' | 'escalated' | 'routed' | 'closed' | 'error'
export type PiiType =
  | 'person_name'
  | 'phone'
  | 'email'
  | 'postal_code'
  | 'address'
  | 'card'
  | 'bank_account'
  | 'birthday'
  | 'sns_account'
export type PiiAction = 'allow' | 'mask' | 'block'
export type Decider = 'jev' | 'kev' | 'mock' | 'human'
export type Route = 'routed' | 'review' | 'escalated'

export interface Span {
  start: number
  end: number
  type: PiiType
  text: string
  source: 'rule' | 'human'
  score: number | null
  confirmed: boolean
}

export interface Item {
  id: string
  seq: number
  channel: Channel
  from_name: string
  from_address: string
  subject: string
  body: string
  received_at: string
  status: Status
  reason: string | null
  first_route: Route | null
  text: string
  pii: Span[]
  pii_leftover: number | null
  pii_decision: 'none' | 'masked' | 'blocked' | 'allowed' | 'skipped' | null
  // 個人情報の確認で人が編集中の内容（サーバに保存した下書き）。まとめて処理するときもこれを使う
  pii_draft: Span[] | null
  // エスカレーションの対応目安までの残り（営業時間の分。過ぎていれば負）
  sla_left_min: number | null
  // 個人情報のため Jev に送らなかった件の、ローカル（Kev）での参考の判定
  kev_reference: {
    category: string | null
    confidence: number | null
    assign_suggestion: string | null
    assign_probability: number | null
    model: string
  } | null
  sent_text: string | null
  answers: Record<string, AnswerView>
  category: string | null
  decided_by: Decider | null
  confidence: number | null
  fields: Record<string, string | null>
  // 担当者の ID（StaffMember.id）
  assignee: string | null
  assigned_by: 'auto' | 'human' | null
  assign_suggestion: string | null
  assign_confidence: number | null
  notes: string[]
  audit: boolean
  audit_result: 'ok' | 'fixed' | null
  cost_usd: number
  error: string | null
  expected: {
    category?: string
    frustration?: number
    urgent?: boolean
    pii?: { type: PiiType; text: string }[]
    order_id?: string | null
  } | null
  // 過去の問い合わせとして取り込んだ件（導入前の試算用）
  backfill: boolean
  // 確率が閾値に届かず、仮で割り当てたか
  assign_provisional: boolean
  updated_at: string
  closed_at: string | null
  // 返信のいらない分類として自動で完了にした（人は見ていない）
  auto_closed: boolean
  priority: Record<string, number>
  // 個人情報の確認に回った理由（確認待ちの件だけ）
  pii_flags: PiiFlag[]
}

export type PiiFlag = 'detected' | 'possible_name' | 'possible_missed'

export interface StaffMember {
  id: string
  name: string
  role: string
  scope: string
  // Slack のユーザー ID（U…）。空なら Slack ではメンションせず名前だけ書く
  slack_user_id?: string
  // 担当するか。オフの人は推定・割り当て・振り分け担当（当番）から外れる
  active: boolean
}

export interface AssignStats {
  closed: number
  with_suggestion: number
  matched: number
  auto_assigned: number
  auto_changed: number
  pairs: { suggested: string | null; actual: string; count: number }[]
  // 担当範囲の案の材料になる件の数（人が割り当てて完了した件）
  handled_by_staff: Record<string, number>
}

/** 担当範囲の案（Claude が完了した件の見出しと分類から作る。保存はしない）。 */
export interface ScopeDraft {
  staff_id: string
  scope: string
  notes: string[]
  based_on: number
  model: string
  latency_ms: number
}

export interface BulkResult {
  done: string[]
  errors: Record<string, string>
}

export type EventKind =
  | 'received'
  | 'guard'
  | 'pii_review'
  | 'classify'
  | 'route'
  | 'review'
  | 'escalate'
  | 'assign'
  | 'note'
  | 'close'
  | 'audit'
  | 'error'
  | 'retry'
  | 'miss'
  | 'reopen'
export type Actor = 'system' | 'kev' | 'jev' | 'mock' | 'human' | 'connector'

export interface OpsEvent {
  id: number
  item_id: string
  at: string
  kind: EventKind
  actor: Actor
  message: string
  data: Record<string, unknown>
}

export interface Post {
  id: number
  channel: string
  at: string
  author: string
  text: string
  item_id: string | null
  fields: Record<string, string | null>
}

export interface Meta {
  // 使っている分類（キー → 表示名。並び順どおり）
  categories: Record<string, string>
  // 廃止した分類も含む表示名（過去の件の表示に使う）
  category_labels: Record<string, string>
  statuses: Status[]
  channels: Record<Channel, string>
  pii_types: Record<PiiType, string>
  actions: PiiAction[]
  fields: Record<string, string>
  route_channels: Record<string, string>
  escalation_channel: string
  inbound_channel: string
  demo_count: number
}

export interface Flow {
  received: number
  guarded: number
  pii_found: number
  pii_review: number
  masked: number
  blocked: number
  classified: number
  kev_only: number
  auto: number
  review: number
  escalated: number
  closed: number
  error: number
  waiting: number
}

export interface Accuracy {
  n: number
  matched: number
}

export interface SimulatorState {
  playing: boolean
  interval_s: number
  cursor: number
  // 高速（間隔なし・並列数を上げる）と、そのときの並列数
  fast: boolean
  fast_workers: number
  total: number
}

export interface AuditExport {
  at: string
  conditions: string
  rows: number
}

export interface KevQueue {
  waiting: number
  running: number
  median_ms: number | null
  samples: number
  eta_s: number | null
  last_at: string | null
}

export interface Overview {
  counts: Record<Status, number>
  flow: Flow
  automation_rate: number | null
  final_accuracy: Accuracy
  model_accuracy: Accuracy
  cost_usd: number
  audit_pending: number
  simulator: SimulatorState
  recent: OpsEvent[]
  // 設定が Kev を使うときだけ入る
  kev: { endpoint: string; available: boolean; reason: string | null; uses: string[] } | null
  // Kev の処理待ちと見込み（Kev を使う設定のときだけ）
  kev_queue: KevQueue | null
}

export interface SlackSettings {
  // 振り分け・エスカレーションの投稿を実際の Slack にも流す
  outbound: boolean
  // 疑似チャンネル名 → Slack のチャンネル ID
  channel_map: Record<string, string>
  // 受信する Slack のチャンネル ID
  inbound_channels: string[]
  // 振り分け担当（当番）の担当者 ID
  dispatcher: string | null
  // 投稿に付ける画面へのリンクの起点
  app_url: string
  // 担当が決まらないまま対応目安が近づいたら 1 回だけ知らせる
  reminder: boolean
  reminder_before_min: number
}

export type SlackState = 'unconfigured' | 'off' | 'connecting' | 'on' | 'error'

export interface SlackStatus {
  bot_token: boolean
  app_token: boolean
  bot_user: string | null
  outbound: { state: SlackState; detail: string; count: number; last_error: string | null }
  inbound: { state: SlackState; detail: string; count: number; last_error: string | null }
  mirrorable: string[]
  // 投稿先のチャンネルから jevlab の投稿を消す作業
  purge: { running: boolean; deleted: number; skipped: number; total: number; error: string | null; finished_at: string | null }
}

export interface CategoryDef {
  key: string
  label: string
  // Jev が読む説明
  criteria: string
  // 振り分け先の疑似チャンネル（#…）
  channel: string
  active: boolean
  // 返信のいらない分類。自動で振り分けた件は、投稿したうえで自動で完了にする
  auto_close: boolean
  // 返信が要るかを件ごとに Jev で判定する（要らない件は返信不要と同じく自動で完了）
  judge_reply: boolean
}

export interface Settings {
  categories: CategoryDef[]
  // どれにも当てはまらないときの受け皿の分類
  fallback_category: string
  guard: {
    enabled: boolean
    // Kev（モデル）で判定するか。false なら規則だけ（氏名の候補はすべて個人情報として扱う）
    use_model: boolean
    // マスク前の本文を読むため、外部（Jev）は選べない
    target: 'custom' | 'mock'
    human_check: boolean
    candidate_threshold: number
    leftover_threshold: number
    policy: Record<PiiType, PiiAction>
    blocked_route: 'kev' | 'human'
  }
  classify: {
    target: Mode
    auto_threshold: number
    review_threshold: number
    label_thresholds: Record<string, number>
    escalate_strong_frustration: boolean
    // 「強い不満」の確率がこれ以上ならエスカレーション
    strong_frustration_at: number
    escalate_urgent: boolean
    // 分類の上位 2 つの差がこれ未満なら人が確認
    split_margin: number
    insufficient_gate: boolean
    insufficient_at: number
  }
  kev_first: { enabled: boolean; threshold: number }
  audit_rate: number
  // 以前に保存した設定には slack がないことがある（ないときは切断として扱う）
  connectors: Partial<Record<Channel, boolean>>
  slack: SlackSettings
  // エスカレーションの対応目安（営業時間で数える）
  sla: { hours: number; days: number[]; start: string; end: string; timezone: string; holidays: string[] }
  simulator: { playing: boolean; interval_s: number; cursor: number; fast: boolean }
  priority_weights: Record<string, number>
  staff: StaffMember[]
  assign: { auto: boolean; threshold: number; use_examples: boolean; max_examples: number; scope_draft_min: number }
}

export interface CurvePoint {
  threshold: number
  auto: number
  errors: number
  auto_rate: number
  error_rate: number | null
}

export interface Curve {
  label: string | null
  n: number
  points: CurvePoint[]
  recommended: number | null
  note: string
}

export interface TuningReport {
  source: 'human' | 'expected'
  target_error: number
  n: number
  overall: Curve
  by_label: Curve[]
}

export interface ImportRow {
  from_name: string
  from_address: string
  subject: string
  body: string
  // 過去の分類（mail の分類のキー）と元の受信日時。過去の問い合わせ（試算用）で使う
  category?: string | null
  received_at?: string | null
}

export interface MessageRow {
  from_name: string
  from_address: string
  subject: string
  body: string
  received_at: string | null
  source: string
}

/** 取り込むファイルを読んだ結果（表は列の対応を画面で決める。メール・Slack はメッセージのまま）。 */
export interface ParsedFile {
  kind: 'table' | 'messages'
  channel: 'csv' | 'mail' | 'slack'
  table: string[][]
  messages: MessageRow[]
  skipped: number
  note: string
}


async function call<T>(method: 'GET' | 'POST' | 'PUT', path: string, payload?: unknown): Promise<T> {
  const res = await fetch(`/api/ops${path}`, {
    method,
    headers: payload === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  })
  const data: unknown = await res.json().catch(() => null)
  if (!res.ok) throw new ApiError(res.status, `HTTP ${res.status}: ${detailOf(data) ?? res.statusText}`)
  // 応答はサーバの型定義どおりである前提で扱う（同一リポジトリで型を揃えている）
  return data as T
}

const itemPath = (id: string, action: string) => `/items/${encodeURIComponent(id)}/${action}`

// ガードレールの見逃しの報告。本文は残さず、位置と長さだけ
export interface MissReport {
  id: number
  item_id: string
  type: PiiType
  start: number
  end: number
  length: number
  leftover: number | null
  at: string
}

export interface MissSummary {
  reports: number
  by_type: Partial<Record<PiiType, number>>
  leftovers: number[]
  unscored: number
  catch: number
  threshold: number
  caught_now: number
  suggested: number | null
  caught_suggested: number
  scored_items: number
  review_now: number
  review_suggested: number
}

/** 行: モデル（個人情報とした / しなかった）、列: 人（個人情報 / でない）。 */
export interface PiiMatrix {
  tp: number
  fp: number
  fn: number
  tn: number
}

export interface PiiEval {
  items: number
  candidates: PiiMatrix
  leftover: PiiMatrix
  leftover_threshold: number
}

export const ops = {
  meta: () => call<Meta>('GET', '/meta'),
  progress: () => call<{ received: number; waiting: number }>('GET', '/progress'),
  overview: () => call<Overview>('GET', '/overview'),
  items: (statuses?: Status[]) =>
    call<Item[]>('GET', `/items${statuses?.length ? `?${statuses.map((s) => `status=${s}`).join('&')}` : ''}`),
  item: (id: string) => call<{ item: Item; events: OpsEvent[] }>('GET', `/items/${encodeURIComponent(id)}`),
  ingest: (body: { channel: Channel; from_name: string; from_address: string; subject: string; body: string }) =>
    call<Item>('POST', '/ingest', body),
  chat: (from_name: string, body: string) => call<Item>('POST', '/chat', { from_name, body }),
  importRows: (file_name: string, channel: ParsedFile['channel'], backfill: boolean, rows: ImportRow[]) =>
    call<{ imported: number; ids: string[] }>('POST', '/import', { file_name, channel, backfill, rows }),
  parseImport: (file_name: string, data: string) => call<ParsedFile>('POST', '/import/parse', { file_name, data }),
  submitPii: (id: string, spans: Span[], action: 'continue' | 'block') =>
    call<Item>('POST', itemPath(id, 'pii'), { spans, action }),
  savePiiDraft: (id: string, spans: Span[] | null) => call<Item>('PUT', itemPath(id, 'pii/draft'), { spans }),
  decide: (id: string, category: string, note?: string) => call<Item>('POST', itemPath(id, 'decide'), { category, note }),
  assign: (id: string, assignee: string) => call<Item>('POST', itemPath(id, 'assign'), { assignee }),
  note: (id: string, text: string) => call<Item>('POST', itemPath(id, 'note'), { text }),
  close: (id: string, category: string | null) => call<Item>('POST', itemPath(id, 'close'), { category }),
  retry: (id: string) => call<Item>('POST', itemPath(id, 'retry')),
  reopen: (id: string) => call<Item>('POST', itemPath(id, 'reopen')),
  reportMiss: (id: string, type: PiiType, start: number, end: number) =>
    call<MissReport>('POST', itemPath(id, 'miss'), { type, start, end }),
  misses: (catchRate: number) => call<MissSummary>('GET', `/misses?catch=${catchRate}`),
  piiEval: () => call<PiiEval>('GET', '/pii-eval'),
  settings: () => call<Settings>('GET', '/settings'),
  putSettings: (s: Settings) => call<Settings>('PUT', '/settings', s),
  simulator: (c: { playing?: boolean; interval_s?: number; fast?: boolean; step?: boolean; rewind?: boolean }) =>
    call<SimulatorState>('POST', '/simulator', c),
  reset: () => call<{ detail: string }>('POST', '/reset'),
  slack: () => call<SlackStatus>('GET', '/slack'),
  slackPurge: () => call<SlackStatus>('POST', '/slack/purge'),
  posts: (limit = 300) => call<Post[]>('GET', `/posts?limit=${limit}`),
  bulkAssign: (ids: string[], assignee: string) => call<BulkResult>('POST', '/bulk/assign', { ids, assignee }),
  bulkPii: (ids: string[]) => call<BulkResult>('POST', '/bulk/pii', { ids }),
  assignment: () => call<AssignStats>('GET', '/assignment'),
  scopeDraft: (staffId: string) =>
    call<ScopeDraft>('POST', `/staff/${encodeURIComponent(staffId)}/scope-draft`, {}),
  auditExports: () => call<AuditExport[]>('GET', '/audit/exports'),
  tuning: (source: 'human' | 'expected', targetError: number, allVersions = false) =>
    call<TuningReport>('GET', `/tuning?source=${source}&target_error=${targetError}${allVersions ? '&all_versions=true' : ''}`),
}
