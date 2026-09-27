// 設定の自動保存。
// - 文字・数字の欄とスライダー: カーソルが外れたときに保存する（打ちかけの値で処理が変わらないように）
// - チェック・選択・ラジオ: 変えたときに保存する
// - 保存できない値のまま画面を離れようとしたら、破棄してよいかを聞く
// 保存は、この画面の項目だけを最新の設定に重ねる（別の画面・タブでの変更を上書きしない）
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ChangeEvent, type FocusEvent, type SyntheticEvent } from 'react'
import { useBlocker } from 'react-router'
import { errorMessage } from '../api'
import { ops, type Settings } from './api'
import { useOps } from './state'

export interface AutoSave {
  draft: Settings | null
  set: (f: (s: Settings) => Settings) => void
  /** いまの下書きを保存する（ボタンの操作の直後など）。保存できたかを返す。 */
  commit: () => Promise<boolean>
  /** 画面の外枠に付ける（中の欄のカーソルが外れた・選択が変わったときに保存する）。 */
  handlers: {
    onBlur: (e: FocusEvent) => void
    onChange: (e: ChangeEvent) => void
    onPointerUp: (e: SyntheticEvent) => void
    onKeyUp: (e: SyntheticEvent) => void
  }
  status: string | null
  error: string | null
}

const isRange = (t: EventTarget) => t instanceof HTMLInputElement && t.type === 'range'
const savesOnChange = (t: EventTarget) =>
  t instanceof HTMLSelectElement || (t instanceof HTMLInputElement && (t.type === 'checkbox' || t.type === 'radio'))

/**
 * @param changed この画面で編集する項目が、サーバの設定から変わっているか
 * @param merge 最新の設定に、この画面の項目を重ねる
 * @param validate 保存できない理由（なければ null）
 */
export function useAutoSave(
  changed: (draft: Settings, saved: Settings) => boolean,
  merge: (latest: Settings, draft: Settings) => Settings,
  validate: (draft: Settings) => string | null,
): AutoSave {
  const { settings, saveSettings } = useOps()
  const [edited, setEdited] = useState<Settings | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const draft = edited ?? settings
  // 変更と保存が同じイベントの中で続くので、最新の下書きは ref でも持つ（set で更新し、サーバの設定が変わったら合わせる）
  const draftRef = useRef<Settings | null>(draft)
  useLayoutEffect(() => {
    draftRef.current = edited ?? settings
  }, [edited, settings])
  // 保存中の約束（保存中にさらに変えた分は、いまの保存が終わってから続けて保存する）
  const inflight = useRef<Promise<boolean> | null>(null)
  const dirty = edited !== null && settings !== null && changed(edited, settings)

  const set = useCallback((f: (s: Settings) => Settings) => {
    const base = draftRef.current
    if (!base) return
    const next = f(base)
    draftRef.current = next
    setEdited(next)
    setStatus(null)
  }, [])

  // 保存の続きは、そのときの最新の commit で行う（保存の後で settings が変わるため）
  const commitRef = useRef<() => Promise<boolean>>(() => Promise.resolve(true))
  const commit = useCallback(async (): Promise<boolean> => {
    if (inflight.current) {
      await inflight.current
      return commitRef.current()
    }
    const next = draftRef.current
    if (!next || !settings || !changed(next, settings)) return true
    const invalid = validate(next)
    if (invalid) {
      setError(invalid)
      return false
    }
    setError(null)
    const run = (async () => {
      try {
        const latest = await ops.settings()
        await saveSettings(merge(latest, next))
        // 保存している間にさらに編集していたら、その下書きは残す（続けて保存する）
        if (draftRef.current === next) setEdited(null)
        setStatus('保存しました')
        return true
      } catch (e: unknown) {
        // 保存できなかった変更は取り消し、サーバの値に戻す（そのままだと以後の保存もすべて失敗する）
        setEdited(null)
        setStatus(null)
        setError(`${errorMessage(e)}（元に戻しました）`)
        return false
      }
    })()
    inflight.current = run
    try {
      return await run
    } finally {
      inflight.current = null
    }
  }, [settings, saveSettings, changed, merge, validate])
  useLayoutEffect(() => {
    commitRef.current = commit
  }, [commit])

  const later = useCallback(() => void Promise.resolve().then(commit), [commit])
  const handlers = {
    onBlur: () => later(),
    onChange: (e: ChangeEvent) => {
      if (savesOnChange(e.target)) later()
    },
    onPointerUp: (e: SyntheticEvent) => {
      if (isRange(e.target)) later()
    },
    onKeyUp: (e: SyntheticEvent) => {
      if (isRange(e.target)) later()
    },
  }

  // 保存していない変更を残したまま離れるとき: 保存できる値なら保存してから移り、保存できない値のときだけ聞く
  const invalid = dirty && draft ? validate(draft) : null
  const blocker = useBlocker(dirty)
  // 同じ「移動の保留」を 1 回だけ処理する（保存中の再描画で効果が走り直しても、移動を二重に進めない）
  const handling = useRef(false)
  useEffect(() => {
    if (blocker.state !== 'blocked') {
      handling.current = false
      return
    }
    if (handling.current) return
    handling.current = true
    if (invalid !== null) {
      if (window.confirm(`保存できない値があります（${invalid}）。変更を破棄して移動しますか？`)) blocker.proceed()
      else blocker.reset()
      return
    }
    void commitRef.current().then((ok) => {
      if (ok || window.confirm('保存できませんでした。破棄して移動しますか？')) blocker.proceed()
      else blocker.reset()
    })
  }, [blocker, invalid])
  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  return { draft, set, commit, handlers, status, error }
}
