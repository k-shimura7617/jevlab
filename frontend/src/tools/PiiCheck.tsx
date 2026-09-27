import { useEffect, useState, type ReactElement } from 'react'
import { errorMessage } from '../api'
import { tools, type PiiCheckResult } from './api'

/**
 * ツールの入力をローカルで調べるボタン（規則と、設定で使うときだけ Kev。Jev・Claude には送らない）。
 * 結果はモーダルで出す（入力欄の大きさ・位置を変えない）。「伏せる」で入力欄を【種類】に置き換える。
 */
export function PiiCheckButton({ text, onMask, label }: { text: string; onMask: (masked: string) => void; label: string }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" className="secondary small pii-check-btn" disabled={!text.trim()} onClick={() => setOpen(true)}>
        個人情報チェック（ローカル）
      </button>
      {open && (
        <PiiCheckModal
          text={text}
          label={label}
          onClose={() => setOpen(false)}
          onMask={(masked) => {
            onMask(masked)
            setOpen(false)
          }}
        />
      )}
    </>
  )
}

function PiiCheckModal({ text, label, onClose, onMask }: { text: string; label: string; onClose: () => void; onMask: (masked: string) => void }) {
  const [result, setResult] = useState<PiiCheckResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    tools
      .piiCheck(text)
      .then((r) => alive && setResult(r))
      .catch((e: unknown) => alive && setError(errorMessage(e)))
    return () => {
      alive = false
    }
  }, [text])
  const found = result?.spans.filter((s) => s.confirmed) ?? []
  return (
    <>
      <div className="sheet-backdrop" onClick={onClose} />
      <div className="modal" role="dialog" aria-modal="true" aria-label={`個人情報チェック（${label}）`} data-testid="pii-check">
        <h2>個人情報チェック（{label}）</h2>
        {error && <div className="error small">{error}</div>}
        {!result && !error && <p className="muted small">確認中…</p>}
        {result && (
          <>
            <p className="muted small">{result.model_name ? `規則と ${result.model_name} で確認。` : '規則だけで確認（Kev は使っていない）。'}</p>
            <div className="pii-check-text">{highlight(text, found.map((s) => [s.start, s.end]))}</div>
            {found.length ? (
              <ul className="pii-check-list small">
                {found.map((s) => (
                  <li key={`${s.start}-${s.end}`}>
                    {result.labels[s.type] ?? s.type}: {s.text}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="small">見つかりませんでした。</p>
            )}
          </>
        )}
        <div className="row judge-row">
          <button type="button" className="secondary" onClick={onClose}>
            閉じる
          </button>
          <button type="button" disabled={!result || found.length === 0} onClick={() => result && onMask(result.masked_text)}>
            伏せる
          </button>
        </div>
      </div>
    </>
  )
}

function highlight(text: string, ranges: [number, number][]) {
  const sorted = [...ranges].sort((a, b) => a[0] - b[0])
  const out: (string | ReactElement)[] = []
  let pos = 0
  for (const [start, end] of sorted) {
    if (start < pos) continue
    out.push(text.slice(pos, start))
    out.push(<mark key={start}>{text.slice(start, end)}</mark>)
    pos = end
  }
  out.push(text.slice(pos))
  return out
}
