import type { AnswerView, Label, Mode, QuestionInfo } from './api'

export const pct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)}%`
export const usd = (x: number) => `$${x.toFixed(6)}`
export const ms = (x: number) => `${x.toFixed(0)} ms`

export const localTime = (iso: string) =>
  new Date(iso).toLocaleString('ja-JP', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })

// custom は Kev を想定（TYPESAFE_BASE_URL を別の互換サーバに向けた場合も同じ表示になる）
export const MODE_LABELS: Record<Mode, string> = { mock: 'MOCK', custom: 'Kev（ローカル）', jev: 'Jev（本番・課金あり）' }
// ダッシュボードでの接続先の並び（Kev と Jev を並べて比べる）
export const MODE_ORDER: readonly Mode[] = ['custom', 'jev', 'mock']
// 切替ボタン・比較の列見出し用の短い名前と並び
export const TARGET_SHORT: Record<Mode, string> = { jev: 'Jev', custom: 'Kev', mock: 'MOCK' }
export const SWITCH_ORDER: readonly Mode[] = ['jev', 'custom', 'mock']

/** 選択肢キー・段階番号・true/false を表示名に変換する。 */
export const optionLabel = (q: QuestionInfo, key: Label) => q.options[String(key)] ?? String(key)

/** 予測を options のキー（"billing" / "2" / "true"）にそろえる。 */
export const answerKey = (a: AnswerView) => String(a.prediction)

/** noul は確率分布を返さないため、値から 2 値の分布を作る。 */
export function answerProbabilities(a: AnswerView): Record<string, number> {
  if (a.type !== 'noul') return a.probabilities
  const v = a.value ?? 0
  return { true: v, false: 1 - v }
}

/** 予測した選択肢の確率。noul は閾値の遠さ（0.5 から離れているほど高い）。 */
export function answerConfidence(a: AnswerView): number {
  if (a.type === 'noul') {
    const v = a.value ?? 0
    return Math.max(v, 1 - v)
  }
  return a.confidence ?? 0
}

/** 数値つきの短い表示（コンソール・カード用）。 */
export function answerBrief(q: QuestionInfo, a: AnswerView): string {
  switch (a.type) {
    case 'choice':
      return `${optionLabel(q, a.prediction)}`
    case 'score':
      return `${(a.value ?? 0).toFixed(2)}`
    case 'noul':
      return `${(a.value ?? 0).toFixed(2)}`
  }
}

// 確信度による振り分け。しきい値は運用で「人が見る量」を決めるための目安
export type Triage = 'auto' | 'check' | 'escalate'
export const TRIAGE_ORDER: readonly Triage[] = ['auto', 'check', 'escalate']
export const TRIAGE_LABELS: Record<Triage, string> = {
  auto: '自動で仕分け',
  check: '念のため確認',
  escalate: '人間にエスカレーション',
}
export const TRIAGE_RANGES: Record<Triage, string> = { auto: '≥ 0.9', check: '0.5〜0.9', escalate: '< 0.5' }
export const triageOf = (confidence: number): Triage =>
  confidence >= 0.9 ? 'auto' : confidence >= 0.5 ? 'check' : 'escalate'

/** 選択肢の色。「その他」系は目立たない灰色、それ以外は並び順で割り当てる。 */
export function optionColor(q: QuestionInfo, key: string): string {
  if (key === 'other') return 'var(--c-other)'
  const i = Object.keys(q.options).indexOf(key)
  return `var(--c${Math.max(i, 0) % 6})`
}

/** 本文が「件名: …」で始まるメールなら件名と本文に分ける。 */
export function splitSubject(body: string): { subject: string | null; text: string } {
  const m = /^件名:\s*(.+)\n+([\s\S]*)$/.exec(body)
  return m ? { subject: m[1] ?? null, text: m[2] ?? '' } : { subject: null, text: body }
}
