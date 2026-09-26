// ツールの共通部品
import { useEffect, useRef, useState } from 'react'

/** 処理中の経過秒数（止まったら 0 に戻す）。 */
export function useElapsed(running: boolean): number {
  const [sec, setSec] = useState(0)
  const started = useRef(0)
  useEffect(() => {
    if (!running) return
    started.current = Date.now()
    const timer = setInterval(() => setSec(Math.floor((Date.now() - started.current) / 1000)), 500)
    return () => {
      clearInterval(timer)
      setSec(0)
    }
  }, [running])
  return sec
}
