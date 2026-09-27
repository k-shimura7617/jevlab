// お客様としてメールを送る（試験用。管理画面から使う）
import { useEffect, useState } from 'react'
import { errorMessage } from '../api'
import { ops, type Channel, type Item } from './api'

export function Compose({ onClose, onSent }: { onClose: () => void; onSent: (item: Item) => void }) {
  const [channel, setChannel] = useState<Extract<Channel, 'mail' | 'api'>>('mail')
  const [fromName, setFromName] = useState('')
  const [fromAddress, setFromAddress] = useState('')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const dirty = Boolean(body.trim() || subject.trim())
  const close = () => {
    if (!dirty || window.confirm('書きかけのメールを破棄して閉じますか？')) onClose()
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })
  const send = () => {
    if (!body.trim()) {
      setError('本文を入力してください')
      return
    }
    setBusy(true)
    setError(null)
    ops
      .ingest({ channel, from_name: fromName.trim() || 'デモの来場者', from_address: fromAddress.trim(), subject: subject.trim(), body })
      .then(onSent)
      .catch((e: unknown) => setError(errorMessage(e)))
      .finally(() => setBusy(false))
  }
  return (
    <>
      <div className="sheet-backdrop" onClick={close} />
      <aside className="sheet compose" role="dialog" aria-modal="true" aria-label="お客様としてメールを送る">
        <button type="button" className="close" onClick={close} aria-label="閉じる">
          ×
        </button>
        <h2>メールを送る（お客様役）</h2>
        <p className="muted small">
          support@komorebi.example に届いた扱いで処理されます。
        </p>
        <div className="form-grid">
          <label htmlFor="c-channel">受信経路</label>
          <select id="c-channel" value={channel} onChange={(e) => setChannel(e.target.value === 'api' ? 'api' : 'mail')}>
            <option value="mail">メール（support@komorebi.example）</option>
            <option value="api">API（外部システムからの登録）</option>
          </select>
          <label htmlFor="c-name">差出人</label>
          <input id="c-name" autoFocus value={fromName} placeholder="山田 花子" onChange={(e) => setFromName(e.target.value)} />
          <label htmlFor="c-addr">アドレス</label>
          <input id="c-addr" value={fromAddress} placeholder="hanako@example.com" onChange={(e) => setFromAddress(e.target.value)} />
          <label htmlFor="c-subject">件名</label>
          <input id="c-subject" value={subject} placeholder="注文した商品について" onChange={(e) => setSubject(e.target.value)} />
        </div>
        <textarea
          aria-label="本文"
          value={body}
          rows={9}
          placeholder={'例: 注文番号 KM-250926-0001 のマグカップが割れて届きました。明日の誕生日に使いたかったのに…。\n電話は 090-0000-1234 です。'}
          onChange={(e) => setBody(e.target.value)}
        />
        <div className="row">
          <button type="button" onClick={send} disabled={busy}>
            {busy ? '送信中…' : '送信'}
          </button>
          <button type="button" className="secondary" onClick={close}>
            キャンセル
          </button>
        </div>
        {error && <div className="error">{error}</div>}
      </aside>
    </>
  )
}
