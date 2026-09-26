// 本文を表示し、マウスで選んだ範囲を本文の中の位置（文字の番号）で返す部品。
// 個人情報の確認と、検知漏れの報告で使う
import { useRef, useState, type ReactNode } from 'react'
import type { Span } from './api'

/** 画面上の選択の端点を、本文の文字位置に直す。
 * 本文は data-start を持つ span（地の文・ハイライトの文字）に分けて描いている。
 * ハイライトの種類ラベル（.pii-type）の中や、要素の境界に端点が来た場合も位置を決められるようにする。 */
function boundary(root: HTMLElement, textLength: number, node: Node, offset: number): number | null {
  const el = node instanceof Element ? node : node.parentElement
  if (!el || !root.contains(el)) return null
  const label = el.closest('.pii-type')
  if (label) {
    const mark = label.closest<HTMLElement>('[data-end]')
    return mark ? Number(mark.dataset.end) : null
  }
  if (node.nodeType === Node.TEXT_NODE) {
    const holder = el.closest<HTMLElement>('[data-start]')
    return holder ? Number(holder.dataset.start) + offset : null
  }
  // 要素の中の「offset 番目の子の手前」を指している
  const child = node.childNodes[offset]
  if (child) return startOf(root, textLength, child)
  return endOf(el as HTMLElement, textLength, root)
}

function startOf(root: HTMLElement, textLength: number, node: Node): number | null {
  if (node instanceof HTMLElement && node.dataset.start !== undefined) return Number(node.dataset.start)
  if (node.nodeType === Node.TEXT_NODE) return boundary(root, textLength, node, 0)
  const first = node.firstChild
  return first ? startOf(root, textLength, first) : null
}

function endOf(el: HTMLElement, textLength: number, root: HTMLElement): number {
  if (el === root) return textLength
  if (el.dataset.end !== undefined) return Number(el.dataset.end)
  if (el.dataset.start !== undefined) return Number(el.dataset.start) + (el.textContent?.length ?? 0)
  return el.parentElement ? endOf(el.parentElement, textLength, root) : textLength
}

export function SelectableText({
  text,
  spans,
  labels,
  onToggle,
  onSelect,
  testId = 'pii-text',
}: {
  text: string
  spans: Span[]
  labels: Record<string, string>
  // 省略すると、ハイライトは押せない表示だけになる（検知漏れの報告など、候補を変えない画面）
  onToggle?: (i: number) => void
  onSelect: (range: { start: number; end: number } | null) => void
  testId?: string
}) {
  const box = useRef<HTMLDivElement>(null)
  const [hint, setHint] = useState<string | null>(null)
  const sorted = spans.map((s, i) => ({ s, i })).sort((a, b) => a.s.start - b.s.start)
  const parts: ReactNode[] = []
  let pos = 0
  for (const { s, i } of sorted) {
    if (s.start < pos) continue
    parts.push(
      <span key={`t${pos}`} data-start={pos}>
        {text.slice(pos, s.start)}
      </span>,
    )
    const inner = (
      <>
        <span data-start={s.start}>{text.slice(s.start, s.end)}</span>
        <span className="pii-type">{labels[s.type] ?? s.type}</span>
      </>
    )
    const className = `pii-mark${s.confirmed ? '' : ' off'}${s.source === 'human' ? ' human' : ''}`
    parts.push(
      onToggle ? (
        <button
          key={`s${s.start}`}
          type="button"
          data-start={s.start}
          data-end={s.end}
          aria-pressed={s.confirmed}
          className={className}
          title={`${labels[s.type] ?? s.type}${s.score !== null ? `（判定 ${s.score.toFixed(2)}）` : s.source === 'human' ? '（人が追加）' : '（規則で確定）'}。クリックで${s.confirmed ? '対象から外す' : '対象に戻す'}`}
          onClick={() => onToggle(i)}
        >
          {inner}
        </button>
      ) : (
        <span key={`s${s.start}`} data-start={s.start} data-end={s.end} className={`${className} static`} title={labels[s.type] ?? s.type}>
          {inner}
        </span>
      ),
    )
    pos = s.end
  }
  parts.push(
    <span key={`t${pos}`} data-start={pos}>
      {text.slice(pos)}
    </span>,
  )
  const capture = () => {
    const sel = window.getSelection()
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed || !box.current) {
      onSelect(null)
      setHint(null)
      return
    }
    const r = sel.getRangeAt(0)
    const root = box.current
    if (!root.contains(r.commonAncestorContainer)) return
    const start = boundary(root, text.length, r.startContainer, r.startOffset)
    const end = boundary(root, text.length, r.endContainer, r.endOffset)
    if (start === null || end === null || end <= start) {
      onSelect(null)
      setHint('選択範囲を本文の中に収めてください')
      return
    }
    setHint(null)
    // 前後の空白は含めない
    const raw = text.slice(start, end)
    const lead = raw.length - raw.trimStart().length
    const trail = raw.length - raw.trimEnd().length
    onSelect(end - trail > start + lead ? { start: start + lead, end: end - trail } : null)
  }
  return (
    <>
      <div ref={box} className="fulltext selectable" data-testid={testId} onMouseUp={capture} onKeyUp={capture}>
        {parts}
      </div>
      {hint && <div className="warn-text small">{hint}</div>}
    </>
  )
}
