// メールソフトで返信する（jevlab はメールを送らない。宛先・件名・本文を入れた状態でメールソフトを開く）
import type { Item } from './api'

export const canMail = (item: Item): boolean => /@/.test(item.from_address)

// mailto の本文はメールソフトによって長いと切れる・開かないので、長い本文は入れずにコピーで渡す
const MAILTO_BODY_MAX = 1500
export const bodyFitsMailto = (body: string): boolean => encodeURIComponent(body).length <= MAILTO_BODY_MAX

export const replyHref = (item: Item, body?: string): string => {
  const subject = item.subject && !/^re:/i.test(item.subject) ? `Re: ${item.subject}` : item.subject || 'Re: お問い合わせの件'
  const params = [`subject=${encodeURIComponent(subject)}`, ...(body && bodyFitsMailto(body) ? [`body=${encodeURIComponent(body)}`] : [])]
  return `mailto:${encodeURIComponent(item.from_address)}?${params.join('&')}`
}
