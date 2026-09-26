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

export type MappedField = 'from_name' | 'from_address' | 'subject' | 'body'
export const MAPPED_FIELDS: { id: MappedField; label: string; hints: string[] }[] = [
  { id: 'from_name', label: '差出人', hints: ['from_name', 'name', '差出人', '氏名', '名前', '送信者'] },
  { id: 'from_address', label: 'アドレス', hints: ['from_address', 'email', 'mail', 'address', 'アドレス', 'メール'] },
  { id: 'subject', label: '件名', hints: ['subject', 'title', '件名', 'タイトル'] },
  { id: 'body', label: '本文（必須）', hints: ['body', 'text', 'message', 'content', '本文', '内容', 'メッセージ'] },
]

/** 見出し行から列の対応を推測する（見つからなければ -1）。 */
export function guessMapping(header: string[]): Record<MappedField, number> {
  const norm = header.map((h) => h.trim().toLowerCase())
  const find = (hints: string[]) => norm.findIndex((h) => hints.some((x) => h === x.toLowerCase() || h.includes(x.toLowerCase())))
  return Object.fromEntries(MAPPED_FIELDS.map((f) => [f.id, find(f.hints)])) as Record<MappedField, number>
}

export const SAMPLE_CSV = toCsv([
  ['差出人', 'アドレス', '件名', '本文'],
  ['中村 美和', 'miwa@example.com', 'ラッピングについて', 'KM-250926-0101 の注文ですが、プレゼント用のラッピングは追加できますか。'],
  ['北川 修', 'osamu@example.jp', '届いた皿が欠けていました', '昨日届いた豆皿（KM-250925-0088）の縁が欠けていました。交換をお願いします。\n写真も送れます。'],
  ['見本出版 編集部', 'press@example.com', '取材のお願い', '雑誌の「暮らしの道具」特集で、貴店を紹介させていただけないでしょうか。'],
])
