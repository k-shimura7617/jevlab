import { useState } from 'react'
import { Page, useTitle } from '../../shell'
import { ops, type EventKind } from '../api'
import { usePolling } from '../state'

// サーバの監査ログの種類（src/jevlab/ops/audit.py の KIND_LABELS）と同じ並び
const KINDS: [EventKind, string][] = [
  ['received', '受信'],
  ['guard', '個人情報のガード'],
  ['pii_review', '個人情報の確認'],
  ['classify', '仕分け'],
  ['route', '振り分け'],
  ['review', '分類の確認'],
  ['escalate', 'エスカレーション'],
  ['assign', '担当'],
  ['note', 'メモ'],
  ['close', '完了'],
  ['audit', '抜き取り確認'],
  ['error', 'エラー'],
  ['retry', '再実行'],
  ['miss', '検知漏れの報告'],
]

/** 日本時間の日付（YYYY-MM-DD）。 */
const ymd = (d: Date) => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Tokyo' }).format(d)

export function AuditLog() {
  useTitle('監査ログ')
  const [from, setFrom] = useState(() => ymd(new Date(Date.now() - 6 * 86_400_000)))
  const [to, setTo] = useState(() => ymd(new Date()))
  const [item, setItem] = useState('')
  const [kinds, setKinds] = useState<EventKind[]>([])
  const [encoding, setEncoding] = useState<'utf-8' | 'shift_jis'>('utf-8')
  const exports = usePolling(ops.auditExports, 5000)
  const params = new URLSearchParams()
  if (from) params.set('from', from)
  if (to) params.set('to', to)
  if (item.trim()) params.set('item', item.trim())
  kinds.forEach((k) => params.append('kinds', k))
  params.set('encoding', encoding)
  const bad = from !== '' && to !== '' && from > to
  const toggle = (k: EventKind, on: boolean) => setKinds((prev) => (on ? [...prev, k] : prev.filter((x) => x !== k)))

  return (
    <Page crumbs={[{ label: '運用', to: '/ops' }, { label: '監査ログ' }]}>
      <div className="panel-head">
        <h1>監査ログ</h1>
        <span className="muted small">本文・差出人・メモの本文は出力しません</span>
      </div>
      <section className="panel">
        <div className="form-grid">
          <label htmlFor="audit-from">期間</label>
          <span className="small">
            <input id="audit-from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /> 〜{' '}
            <input aria-label="期間の終わり" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </span>
          <label htmlFor="audit-item">件</label>
          <input id="audit-item" value={item} placeholder="T-0001（空欄はすべて）" maxLength={20} onChange={(e) => setItem(e.target.value)} />
          <span>種類</span>
          <span className="small" role="group" aria-label="種類">
            {KINDS.map(([k, label]) => (
              <label key={k} className="day-check">
                <input type="checkbox" checked={kinds.includes(k)} onChange={(e) => toggle(k, e.target.checked)} />
                {label}
              </label>
            ))}
            <span className="muted">（選ばなければすべて）</span>
          </span>
          <label htmlFor="audit-encoding">文字コード</label>
          <select id="audit-encoding" value={encoding} onChange={(e) => setEncoding(e.target.value === 'shift_jis' ? 'shift_jis' : 'utf-8')}>
            <option value="utf-8">UTF-8（BOM 付き）</option>
            <option value="shift_jis">Shift_JIS</option>
          </select>
        </div>
        <div className="row">
          {bad ? (
            <span className="error small">期間の始まりが終わりより後です</span>
          ) : (
            <a className="btn" href={`/api/ops/audit.csv?${params.toString()}`} download onClick={() => setTimeout(exports.reload, 800)}>
              CSV を書き出す
            </a>
          )}
        </div>
      </section>
      <section className="panel">
        <h2>書き出しの記録</h2>
        {exports.error && <div className="error small">{exports.error}</div>}
        {exports.data && exports.data.length === 0 && <p className="muted small">まだありません</p>}
        {exports.data && exports.data.length > 0 && (
          <table data-testid="audit-exports">
            <thead>
              <tr>
                <th>日時</th>
                <th>条件</th>
                <th className="num">行数</th>
              </tr>
            </thead>
            <tbody>
              {exports.data.map((x) => (
                <tr key={x.at}>
                  <td>{new Date(x.at).toLocaleString('ja-JP')}</td>
                  <td>{x.conditions}</td>
                  <td className="num">{x.rows}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </Page>
  )
}
