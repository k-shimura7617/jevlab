// 運用画面の共通状態（定義・全体の数字・設定）と、定期的に取り直すための仕組み
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { errorMessage } from '../api'
import { ops, type Meta, type Overview, type Settings } from './api'

// ローカルのデモなので短めの間隔で取り直し、受信・処理の進み具合をその場で見せる
export const POLL_MS = 1500

export interface Polled<T> {
  data: T | null
  error: string | null
  reload: () => void
}

/** 一定間隔で取り直す。タブが裏にある間は止める。
 * 取得中に読み直しを頼まれたら、終わった直後にもう一度取る（操作直後の結果を取りこぼさない）。 */
export function usePolling<T>(load: () => Promise<T>, intervalMs: number = POLL_MS): Polled<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const loadRef = useRef(load)
  const busy = useRef(false)
  const pending = useRef(false)
  useEffect(() => {
    loadRef.current = load
  })
  const reload = useCallback(function run() {
    if (busy.current) {
      pending.current = true
      return
    }
    busy.current = true
    pending.current = false
    loadRef
      .current()
      .then((d) => {
        setData(d)
        setError(null)
      })
      .catch((e: unknown) => setError(errorMessage(e)))
      .finally(() => {
        busy.current = false
        if (pending.current) run()
      })
  }, [])
  useEffect(() => {
    reload()
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') reload()
    }, intervalMs)
    return () => clearInterval(timer)
  }, [reload, intervalMs])
  return { data, error, reload }
}

/** 一定間隔で更新される現在時刻（経過時間の表示用。描画中に Date.now を呼ばないため）。 */
export function useNow(intervalMs = 10_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(timer)
  }, [intervalMs])
  return now
}

interface OpsState {
  meta: Meta | null
  overview: Overview | null
  overviewError: string | null
  settings: Settings | null
  settingsError: string | null
  refresh: () => void
  saveSettings: (next: Settings) => Promise<void>
}

const OpsContext = createContext<OpsState | null>(null)

export function useOps(): OpsState {
  const ctx = useContext(OpsContext)
  if (ctx === null) throw new Error('useOps は OpsProvider の内側で使う')
  return ctx
}

/** サイドバーの件数表示など、運用画面の外からも参照するため（無ければ null）。 */
export const useOpsOptional = (): OpsState | null => useContext(OpsContext)

export function OpsProvider({ children }: { children: ReactNode }) {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const overview = usePolling(ops.overview)

  useEffect(() => {
    ops
      .meta()
      .then(setMeta)
      .catch((e: unknown) => setSettingsError(`運用の定義の取得に失敗: ${errorMessage(e)}`))
    ops
      .settings()
      .then(setSettings)
      .catch((e: unknown) => setSettingsError(`運用の設定の取得に失敗: ${errorMessage(e)}`))
  }, [])

  const refresh = useCallback(() => {
    overview.reload()
    ops
      .settings()
      .then(setSettings)
      .catch((e: unknown) => setSettingsError(`運用の設定の取得に失敗: ${errorMessage(e)}`))
  }, [overview])

  const saveSettings = useCallback(async (next: Settings) => {
    // 失敗は呼び出し側で表示する（入力中の値を残すため、ここでは握りつぶさない）
    const saved = await ops.putSettings(next)
    setSettings(saved)
    setSettingsError(null)
  }, [])

  return (
    <OpsContext.Provider
      value={{
        meta,
        overview: overview.data,
        overviewError: overview.error,
        settings,
        settingsError,
        refresh,
        saveSettings,
      }}
    >
      {children}
    </OpsContext.Provider>
  )
}
