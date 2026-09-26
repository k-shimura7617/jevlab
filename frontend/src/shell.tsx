import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { Link, NavLink, useLocation } from 'react-router'
import { api, errorMessage, type AppInfo, type Mode, type Status, type TargetStatus } from './api'
import { MODE_LABELS, SWITCH_ORDER, TARGET_SHORT, usd } from './format'
import { useOpsOptional } from './ops/state'
import { useActiveApps } from './runStore'

const TARGET_KEY = 'jevlab.target'

interface ShellState {
  status: Status | null
  statusError: string | null
  refreshStatus: () => void
  apps: AppInfo[]
  appsError: string | null
  // 画面で選んだ接続先（使えなければ使えるものに置き換えた実際の値）。状態の取得前は null
  target: Mode | null
  // 利用者が選んだ接続先（使えないときは target と食い違う）
  preferred: Mode | null
  setTarget: (t: Mode) => void
}

const ShellContext = createContext<ShellState | null>(null)

export function useShell(): ShellState {
  const ctx = useContext(ShellContext)
  if (ctx === null) throw new Error('useShell は ShellProvider の内側で使う')
  return ctx
}

export const targetStatus = (status: Status | null, t: Mode): TargetStatus | null =>
  status?.targets.find((x) => x.name === t) ?? null

const isMode = (v: string | null): v is Mode => v !== null && (SWITCH_ORDER as readonly string[]).includes(v)

function loadPreferred(): Mode | null {
  try {
    const v = localStorage.getItem(TARGET_KEY)
    return isMode(v) ? v : null
  } catch (e: unknown) {
    console.error('選んだ接続先を読み込めませんでした', e)
    return null
  }
}

/** 選んだ接続先 → サーバの既定 → 使える最初のもの の順に選ぶ。 */
function effectiveTarget(status: Status, preferred: Mode | null): Mode {
  const usable = (t: Mode | null): t is Mode => t !== null && targetStatus(status, t)?.available === true
  if (usable(preferred)) return preferred
  if (usable(status.default)) return status.default
  return SWITCH_ORDER.find(usable) ?? status.default
}

export function ShellProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [statusError, setStatusError] = useState<string | null>(null)
  const [apps, setApps] = useState<AppInfo[]>([])
  const [appsError, setAppsError] = useState<string | null>(null)
  const [preferred, setPreferred] = useState<Mode | null>(loadPreferred)
  const active = useActiveApps()

  const refreshStatus = useCallback(() => {
    api
      .status()
      .then((s) => {
        setStatus(s)
        setStatusError(null)
      })
      .catch((e: unknown) => setStatusError(`状態取得に失敗: ${errorMessage(e)}`))
  }, [])

  const setTarget = useCallback((t: Mode) => {
    setPreferred(t)
    try {
      localStorage.setItem(TARGET_KEY, t)
    } catch (e: unknown) {
      console.error('選んだ接続先を保存できませんでした（再読み込みで戻ります）', e)
    }
  }, [])

  useEffect(() => {
    refreshStatus()
    api
      .apps()
      .then(setApps)
      .catch((e: unknown) => setAppsError(`一覧の取得に失敗: ${errorMessage(e)}`))
    // Kev を起動・停止したあと画面に戻ったときに反映する
    window.addEventListener('focus', refreshStatus)
    return () => window.removeEventListener('focus', refreshStatus)
  }, [refreshStatus])

  // 実行中にタブを閉じる・再読み込みすると途中の結果が消えるため確認する（画面内の移動では実行を続ける）
  const running = active.length > 0
  // 実行中は利用額の表示を追いかける（1 本終わるごとの更新だけだと長い実行で古いままになる）
  useEffect(() => {
    if (!running) return
    const timer = setInterval(refreshStatus, 5000)
    return () => clearInterval(timer)
  }, [running, refreshStatus])
  useEffect(() => {
    if (!running) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [running])

  const target = status ? effectiveTarget(status, preferred) : null
  return (
    <ShellContext.Provider
      value={{ status, statusError, refreshStatus, apps, appsError, target, preferred: preferred ?? target, setTarget }}
    >
      {children}
    </ShellContext.Provider>
  )
}

export const ModeBadge = ({ mode }: { mode: Mode }) => (
  <span className={`mode-badge mode-${mode}`}>{MODE_LABELS[mode]}</span>
)

/** タブを並べたときにも接続先を取り違えないよう、タイトルに接続先を付ける。 */
export function useTitle(title: string) {
  const { target } = useShell()
  // 運用の画面はヘッダーの接続先を使わない（段階ごとに運用の設定で決まる）ので付けない
  const ops = useLocation().pathname.startsWith('/ops')
  useEffect(() => {
    document.title = target && !ops ? `[${TARGET_SHORT[target]}] ${title}` : title
  }, [title, target, ops])
}

function TargetSwitch() {
  const { status, target, setTarget } = useShell()
  if (!status) return null
  return (
    <div className="seg" role="group" aria-label="接続先" data-testid="target-switch">
      {SWITCH_ORDER.map((t) => {
        const ts = targetStatus(status, t)
        const available = ts?.available === true
        return (
          <button
            key={t}
            type="button"
            className={`seg-btn mode-${t}`}
            aria-pressed={t === target}
            disabled={!available}
            title={available ? `${MODE_LABELS[t]} ／ ${ts?.endpoint ?? ''}` : `使えません: ${ts?.reason ?? '状態不明'}`}
            onClick={() => setTarget(t)}
          >
            {TARGET_SHORT[t]}
          </button>
        )
      })}
    </div>
  )
}

/** Jev の利用額の目安。どの接続先を選んでいても常に出す。 */
function JevSpend() {
  const { status } = useShell()
  const jev = targetStatus(status, 'jev')
  if (!jev) return null
  const ratio = jev.cap_usd > 0 ? jev.total_usd / jev.cap_usd : 0
  return (
    <span
      className={ratio >= 0.8 ? 'spend spend-near' : 'spend'}
      data-testid="jev-spend"
      title={
        'jevlab から呼んだ分の目安'
      }
    >
      Jev 利用額（目安） <strong>{usd(jev.total_usd)}</strong> ／ 上限 ${jev.cap_usd.toFixed(2)}
    </span>
  )
}

function StatusLine() {
  const { status, statusError, target, preferred } = useShell()
  // 運用の画面では、接続先は段階ごとに運用の設定で決まる（左ペインに表示）。ヘッダーの切替は評価の画面だけで使う
  const ops = useLocation().pathname.startsWith('/ops')
  if (ops)
    return (
      <div className="status-line" data-testid="status">
        <JevSpend />
      </div>
    )
  if (statusError) return <div className="error small">{statusError}</div>
  if (!status || !target) return <div className="muted">状態を取得中…</div>
  const ts = targetStatus(status, target)
  const fallback = preferred !== null && preferred !== target ? targetStatus(status, preferred) : null
  // Jev の累計は右端に常に出すので、ここでは MOCK の模擬料金だけ出す
  const cost = !ts ? '' : target === 'jev' ? '' : ts.billed ? ` ／ 模擬料金の累計 ${usd(ts.total_usd)}` : ' ／ 課金なし'
  return (
    // 長さが変わる説明を左に、切替ボタンと利用額を右端に置き、接続先を切り替えてもボタンの位置が動かないようにする
    <div className="status-line" data-testid="status">
      {fallback && (
        <span className="warn-text small">
          {TARGET_SHORT[fallback.name]} は使えないため {TARGET_SHORT[target]} に接続中（{fallback.reason}）
        </span>
      )}
      <span className="muted small status-detail">
        {MODE_LABELS[target]} ／ {ts?.endpoint}
        {cost}
      </span>
      <TargetSwitch />
      <JevSpend />
    </div>
  )
}

const navCls = ({ isActive }: { isActive: boolean }) => (isActive ? 'active' : undefined)

/** 左ペインの 1 まとまり。見出し（ダッシュボード）だけを常に出し、個別の画面は一段下げて折りたためるようにする。 */
function NavGroup({
  id,
  title,
  to,
  inside,
  badge,
  extra,
  children,
}: {
  id: string
  title: string
  to: string
  // 今いる画面がこのまとまりに含まれるか（含まれるときは開いておく）
  inside: boolean
  // 閉じているときに見出しへ出す件数
  badge?: ReactNode
  extra?: ReactNode
  children: ReactNode
}) {
  const [toggled, setToggled] = useState<boolean | null>(null)
  const open = toggled ?? inside
  return (
    <div className={`nav-group${open ? ' open' : ''}`}>
      <div className="nav-head">
        <NavLink to={to} end className={navCls}>
          {title}
          {extra}
          {!open && badge}
        </NavLink>
        <button
          type="button"
          className="nav-toggle"
          aria-expanded={open}
          aria-controls={`nav-${id}`}
          aria-label={`${title}の画面一覧を${open ? '閉じる' : '開く'}`}
          onClick={() => setToggled(!open)}
        >
          {open ? '▾' : '▸'}
        </button>
      </div>
      {open && (
        <nav id={`nav-${id}`} className="nav-sub">
          {children}
        </nav>
      )}
    </div>
  )
}

function Sidebar() {
  const { apps, appsError } = useShell()
  const active = useActiveApps()
  const ops = useOpsOptional()
  const { pathname } = useLocation()
  const counts = ops?.overview?.counts
  const badge = (n: number | undefined, tone: 'warn' | 'ng' = 'warn') =>
    n ? <span className={`nav-badge ${tone}`}>{n}</span> : null
  const waiting = (counts?.pii_review ?? 0) + (counts?.review ?? 0) + (counts?.escalated ?? 0)
  const settings = ops?.settings
  const who = (label: string) => <span className="nav-who">（{label}）</span>
  const guardWho = settings && (!settings.guard.enabled ? '無効' : settings.guard.use_model ? TARGET_SHORT[settings.guard.target] : '規則のみ')
  const classifyWho =
    settings &&
    (settings.kev_first.enabled && settings.classify.target !== 'custom'
      ? `Kev→${TARGET_SHORT[settings.classify.target]}`
      : TARGET_SHORT[settings.classify.target])
  return (
    <aside className="sidebar">
      <Link className="brand" to="/ops">
        jevlab
      </Link>
      {ops?.overviewError && (
        <div className="nav-error" role="alert" title={ops.overviewError}>
          サーバに接続できません（再接続中）
        </div>
      )}
      <NavGroup
        id="ops"
        title="運用ダッシュボード"
        to="/ops"
        inside={pathname.startsWith('/ops')}
        badge={badge(waiting)}
        extra={ops?.overview?.simulator.playing && <span className="run-dot" title="受信中" aria-label="受信中" />}
      >
        <div className="nav-label">受付と人の対応</div>
        <NavLink to="/ops/inbox" className={navCls}>
          受付箱
        </NavLink>
        <NavLink to="/ops/pii" className={navCls}>
          個人情報の確認{guardWho && who(guardWho)}
          {badge(counts?.pii_review)}
        </NavLink>
        <NavLink to="/ops/review" className={navCls}>
          分類の確認{classifyWho && who(classifyWho)}
          {badge(counts?.review)}
        </NavLink>
        <NavLink to="/ops/escalations" className={navCls}>
          エスカレーション{badge(counts?.escalated, 'ng')}
        </NavLink>
        <NavLink to="/ops/channels" className={navCls}>
          チャンネル
        </NavLink>
        <div className="nav-label">管理</div>
        <NavLink to="/ops/staff" className={navCls}>
          担当者
        </NavLink>
        <NavLink to="/ops/connectors" className={navCls}>
          コネクタ
        </NavLink>
        <NavLink to="/ops/tuning" className={navCls}>
          閾値の調整
        </NavLink>
        <NavLink to="/ops/settings" className={navCls}>
          設定
        </NavLink>
      </NavGroup>
      <NavGroup
        id="eval"
        title="評価ダッシュボード"
        to="/eval"
        inside={pathname.startsWith('/eval')}
        extra={active.length > 0 && <span className="run-dot" title="評価を実行中" aria-label="評価を実行中" />}
      >
        <div className="nav-label">アプリ（ライブ評価）</div>
        {apps.map((a) => (
          <NavLink key={a.name} to={`/eval/apps/${encodeURIComponent(a.name)}/run`} className={navCls}>
            {a.title}
            {active.includes(a.name) && <span className="run-dot" title="評価を実行中" aria-label="実行中" />}
          </NavLink>
        ))}
      </NavGroup>
      <NavGroup id="tools" title="ツール" to="/tools/tone" inside={pathname.startsWith('/tools')}>
        <div className="nav-label">判定を使う道具</div>
        <NavLink to="/tools/tone" className={navCls}>
          言い方チェック
        </NavLink>
      </NavGroup>
      {appsError && <div className="error small">{appsError}</div>}
    </aside>
  )
}

export interface Crumb {
  label: string
  to?: string
}

export function Page({ crumbs, wide, children }: { crumbs: Crumb[]; wide?: boolean; children: ReactNode }) {
  return (
    <div className="layout">
      <Sidebar />
      <div className="content">
        <header className="topbar">
          <div className="crumbs">
            {crumbs.map((c, i) => (
              <span key={`${i}-${c.label}`}>
                {i > 0 && ' ／ '}
                {c.to ? <Link to={c.to}>{c.label}</Link> : c.label}
              </span>
            ))}
          </div>
          <StatusLine />
        </header>
        <main className={wide ? 'wide' : undefined}>{children}</main>
      </div>
    </div>
  )
}
