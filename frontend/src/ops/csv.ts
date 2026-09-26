// CSV の読み込み（RFC 4180: ダブルクォートで囲んだ値の中のカンマ・改行・"" を扱う）

export function parseCsv(input: string): string[][] {
  const text = input.replace(/^﻿/, '')
  const rows: string[][] = []
  let row: string[] = []
  let field = ''
  let quoted = false
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i]
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') {
        field += '"'
        i += 1
      } else if (c === '"') {
        quoted = false
      } else {
        field += c
      }
      continue
    }
    if (c === '"' && field === '') {
      quoted = true
    } else if (c === ',') {
      row.push(field)
      field = ''
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i += 1
      row.push(field)
      rows.push(row)
      row = []
      field = ''
    } else {
      field += c
    }
  }
  if (quoted) throw new Error('CSV の形式が不正です（閉じていないダブルクォートがあります）')
  if (field !== '' || row.length) {
    row.push(field)
    rows.push(row)
  }
  return rows.filter((r) => r.some((v) => v.trim() !== ''))
}

const quote = (v: string) => (/[",\r\n]/.test(v) ? `"${v.replaceAll('"', '""')}"` : v)
export const toCsv = (rows: string[][]) => rows.map((r) => r.map(quote).join(',')).join('\r\n')

export type MappedField = 'from_name' | 'from_address' | 'subject' | 'body' | 'category' | 'received_at'
export const MAPPED_FIELDS: { id: MappedField; label: string; hints: string[] }[] = [
  { id: 'from_name', label: '差出人', hints: ['from_name', 'name', '差出人', '氏名', '名前', '送信者', 'requester'] },
  { id: 'from_address', label: 'アドレス', hints: ['from_address', 'email', 'mail', 'address', 'アドレス', 'メール'] },
  { id: 'subject', label: '件名', hints: ['subject', 'title', '件名', 'タイトル'] },
  { id: 'body', label: '本文（必須）', hints: ['body', 'text', 'message', 'content', 'description', '本文', '内容', 'メッセージ'] },
  { id: 'category', label: '過去の分類', hints: ['category', 'type', '分類', '種別', 'カテゴリ', '区分'] },
  { id: 'received_at', label: '受信日時', hints: ['received', 'created', 'date', '受信日', '受付日', '日時', '作成日'] },
]

// 過去の分類の値から、jevlab の分類（mail の分類のキー）を推測する
const CATEGORY_HINTS: Record<string, string[]> = {
  inquiry: ['問い合わせ', '問合せ', '問合わせ', '質問', '相談', 'inquiry', 'question'],
  complaint: ['クレーム', '苦情', '不満', 'complaint'],
  thanks: ['お礼', '感謝', 'thanks'],
  other: ['その他', 'other'],
}

/** 過去の分類の値を jevlab の分類に当てる（見つからなければ空文字＝使わない）。 */
export function guessCategory(value: string, labels: Record<string, string>): string {
  const v = value.trim().toLowerCase()
  if (!v) return ''
  const byLabel = Object.entries(labels).find(([k, l]) => v === k.toLowerCase() || v === l.toLowerCase())
  if (byLabel) return byLabel[0]
  return Object.entries(CATEGORY_HINTS).find(([k, hints]) => k in labels && hints.some((h) => v.includes(h.toLowerCase())))?.[0] ?? ''
}

/** 受信日時の値を ISO 8601 にする（Excel の日付のシリアル値・「2025/01/10 9:00」など）。読めなければ null。 */
export function toIsoDate(value: string): string | null {
  const v = value.trim()
  if (!v) return null
  if (/^\d+(\.\d+)?$/.test(v)) {
    // Excel の日付（1899-12-30 からの日数。時刻は小数部）。手元の時刻として読む
    const days = Number(v)
    const base = new Date(1899, 11, 30)
    const d = new Date(base.getTime() + Math.round(days * 86_400_000))
    return Number.isNaN(d.getTime()) ? null : d.toISOString()
  }
  const d = new Date(v.replace(/\//g, '-').replace(' ', 'T'))
  return Number.isNaN(d.getTime()) ? null : d.toISOString()
}

/** 見出し行から列の対応を推測する（見つからなければ -1）。 */
export function guessMapping(header: string[]): Record<MappedField, number> {
  const norm = header.map((h) => h.trim().toLowerCase())
  const find = (hints: string[]) => norm.findIndex((h) => hints.some((x) => h === x.toLowerCase() || h.includes(x.toLowerCase())))
  return Object.fromEntries(MAPPED_FIELDS.map((f) => [f.id, find(f.hints)])) as Record<MappedField, number>
}

export const SAMPLE_CSV = toCsv([
  ['受信日時', '差出人', 'アドレス', '件名', '本文', '分類'],
  ['2025/09/01 10:12', '中村 美和', 'miwa@example.com', 'ラッピングについて', 'KM-250926-0101 の注文ですが、プレゼント用のラッピングは追加できますか。', '問い合わせ'],
  ['2025/09/02 15:40', '北川 修', 'osamu@example.jp', '届いた皿が欠けていました', '昨日届いた豆皿（KM-250925-0088）の縁が欠けていました。交換をお願いします。\n写真も送れます。', 'クレーム'],
  ['2025/09/03 09:05', '見本出版 編集部', 'press@example.com', '取材のお願い', '雑誌の「暮らしの道具」特集で、貴店を紹介させていただけないでしょうか。', 'その他'],
])
