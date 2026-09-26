import type { MouseEvent } from 'react'

// 折りたたみ（details）の中の最後に置く「閉じる」。
// 開いた中身が長いと見出しまで戻らないと閉じられないため、下にも閉じる手段を置く
export function FoldClose() {
  const close = (e: MouseEvent<HTMLButtonElement>) => {
    const details = e.currentTarget.closest('details')
    if (!details) return
    details.open = false
    details.scrollIntoView({ block: 'nearest' })
  }
  return (
    <div className="fold-close">
      <button type="button" className="secondary" onClick={close}>
        閉じる
      </button>
    </div>
  )
}
