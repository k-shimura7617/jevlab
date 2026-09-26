// 画面が真っ白（真っ暗）にならないための備え。
// 1. 描画中の例外は境界で受け止め、エラーの内容と「再読み込み」を出す（画面の中で起きたものはサイドバーを残す）
// 2. 開発サーバで依存の作り直しが起きて、分割した JS が読めなくなったときは 1 回だけ自動で再読み込みする
//    （起動そのものに失敗して何も出ないときは、index.html の見張りが 1 回だけ再読み込みする）
// 3. 捕まえていない例外・Promise の失敗は、握りつぶさずコンソールに出し、画面の隅にも知らせる
import { Component, useSyncExternalStore, type ErrorInfo, type ReactNode } from 'react'
import { isRouteErrorResponse, useRouteError } from 'react-router'

const RELOAD_KEY = 'jevlab.preloadReloadAt'
// この時間内に同じ理由で再読み込みしていたら、繰り返さない（無限に再読み込みしないように）
const RELOAD_GUARD_MS = 30_000

function describe(error: unknown): string {
  if (isRouteErrorResponse(error)) return `${error.status} ${error.statusText}`
  // 名前が "Error" のときは本文だけ（「Error: 」を重ねない）
  if (error instanceof Error) return error.name === 'Error' ? error.message : `${error.name}: ${error.message}`
  return String(error)
}

export function ErrorScreen({ error, inline = false }: { error: unknown; inline?: boolean }) {
  return (
    <div className={inline ? 'fatal inline' : 'fatal'} role="alert">
      <h1>表示エラー</h1>
      <pre className="fatal-detail">{describe(error)}</pre>
      <div className="row">
        <button type="button" onClick={() => window.location.reload()}>
          再読み込み
        </button>
        {!inline && <a href="/ops">運用ダッシュボードを開く</a>}
      </div>
      {import.meta.env.DEV && <p className="muted small">直らなければ scripts/dev.sh を起動し直し、Ctrl+Shift+R で再読み込み。</p>}
    </div>
  )
}

/** ルーターの中で起きた描画エラー（サイドバーごと描けないときの最後の受け皿）。 */
export function RouteError() {
  const error = useRouteError()
  console.error('画面の表示中にエラーが起きました', error)
  return <ErrorScreen error={error} />
}

/** ルーターの外（最上位）で起きた描画エラー。 */
export class RootErrorBoundary extends Component<{ children: ReactNode }, { error: unknown }> {
  state: { error: unknown } = { error: null }

  static getDerivedStateFromError(error: unknown) {
    return { error }
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error('画面の表示中にエラーが起きました', error, info.componentStack)
  }

  render() {
    return this.state.error !== null ? <ErrorScreen error={this.state.error} /> : this.props.children
  }
}

// ---- 捕まえていない例外の知らせ ----

type Listener = () => void
type Notice = { id: number; message: string }
let notices: Notice[] = []
let nextId = 1
const listeners = new Set<Listener>()

function publish(next: Notice[]) {
  notices = next
  listeners.forEach((l) => l())
}

function notify(message: string) {
  publish([...notices.slice(-2), { id: nextId++, message }])
}

function subscribe(l: Listener) {
  listeners.add(l)
  return () => listeners.delete(l)
}

export function GlobalErrorNotices() {
  const list = useSyncExternalStore(subscribe, () => notices)
  if (!list.length) return null
  return (
    <div className="global-errors" role="alert" data-testid="global-errors">
      {list.map((n) => (
        <div key={n.id} className="global-error">
          <span>{n.message}</span>
          <button type="button" className="link-btn" onClick={() => publish(notices.filter((x) => x.id !== n.id))}>
            閉じる
          </button>
        </div>
      ))}
    </div>
  )
}

/** 起動時に 1 回だけ呼ぶ。 */
export function installGlobalErrorHandlers() {
  window.addEventListener('vite:preloadError', (event) => {
    let last = 0
    try {
      last = Number(sessionStorage.getItem(RELOAD_KEY) ?? 0)
    } catch (e: unknown) {
      console.error('再読み込みの記録を読めませんでした', e)
    }
    if (Date.now() - last < RELOAD_GUARD_MS) {
      console.error('画面を読み込めませんでした（再読み込み済み）', event)
      notify('画面を読み込めませんでした。Ctrl+Shift+R で再読み込みしてください')
      return
    }
    // 開発サーバが依存を作り直した直後などは、読み直せば直る
    event.preventDefault()
    try {
      sessionStorage.setItem(RELOAD_KEY, String(Date.now()))
    } catch (e: unknown) {
      console.error('再読み込みの記録を保存できませんでした', e)
    }
    window.location.reload()
  })
  window.addEventListener('error', (event) => {
    console.error('捕まえていないエラー', event.error ?? event.message)
    notify(`エラー: ${describe(event.error ?? event.message)}`)
  })
  window.addEventListener('unhandledrejection', (event) => {
    console.error('処理されなかった Promise の失敗', event.reason)
    notify(`処理の失敗: ${describe(event.reason)}`)
  })
}
