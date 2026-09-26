import { useEffect, useState } from 'react'
import { api, errorMessage, type AppInfo, type Sample } from './api'

export type AppData =
  | { state: 'loading' }
  | { state: 'error'; message: string }
  | { state: 'ready'; info: AppInfo; samples: Sample[] }

/** アプリ定義と評価データをまとめて取得する。 */
export function useAppData(name: string): AppData {
  const [data, setData] = useState<AppData>({ state: 'loading' })
  useEffect(() => {
    let alive = true
    setData({ state: 'loading' })
    Promise.all([api.app(name), api.samples(name)])
      .then(([info, samples]) => alive && setData({ state: 'ready', info, samples }))
      .catch((e: unknown) => alive && setData({ state: 'error', message: `アプリ情報の取得に失敗: ${errorMessage(e)}` }))
    return () => {
      alive = false
    }
  }, [name])
  return data
}
