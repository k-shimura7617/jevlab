import { useLayoutEffect, useRef } from 'react'

// 画面の下端から空ける幅（px。枠のある画面では、ページの下の余白もこの幅にする: ops.css の main:has(.fill)）
const BOTTOM_GAP = 16
// 1 列に並べる幅（ops.css の @media と同じ）。このときは高さを決めず、ページごとスクロールする
const NARROW = '(max-width: 1000px)'

/**
 * 一覧と詳細の枠を、画面の下端ぎりぎりまで伸ばす（中はそれぞれスクロールする）。
 * 枠の上にある見出しやタブの高さは画面ごとに違うので、枠の位置から高さを決める。
 */
export function useFillHeight<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const fit = () => {
      if (window.matchMedia(NARROW).matches) {
        el.style.height = ''
        return
      }
      const top = el.getBoundingClientRect().top + window.scrollY
      const height = Math.floor(window.innerHeight - top - BOTTOM_GAP)
      el.style.height = `${Math.max(320, height)}px`
      // 枠の下にあるページの余白の分だけはみ出すので、その分を縮めてページ自体はスクロールさせない
      const overflow = document.documentElement.scrollHeight - window.innerHeight
      if (overflow > 0) el.style.height = `${Math.max(320, height - overflow)}px`
    }
    fit()
    window.addEventListener('resize', fit)
    // 上の見出し・知らせの高さが変わったときも合わせ直す
    const observer = new ResizeObserver(fit)
    if (el.parentElement) observer.observe(el.parentElement)
    return () => {
      window.removeEventListener('resize', fit)
      observer.disconnect()
    }
  }, [])
  return ref
}
