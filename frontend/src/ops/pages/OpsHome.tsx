import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import { Link, useLocation, useNavigate } from 'react-router'
import { errorMessage } from '../../api'
import { pct } from '../../format'
import { Page, useTitle } from '../../shell'
import { ops } from '../api'
import { costText, KevQueueNote, Kpi } from '../components'
import { ACTOR_LABELS, clockTime } from '../format'
import { useOps } from '../state'
import { Compose } from '../Compose'
import { AdminTabs } from '../tabs'

const TARGET_NAMES = { custom: 'Kev', jev: 'Jev', mock: 'MOCK' } as const

function Node({
  title,
  who,
  count,
  to,
  tone,
  children,
}: {
  title: string
  who?: string
  count: number
  to?: string
  tone?: 'accent' | 'warn' | 'ng' | 'ok' | 'muted'
  children?: ReactNode
}) {
  const body = (
    <>
      <div className="node-title">
        {title}
        {who && <span className="node-who">{who}</span>}
      </div>
      <div className="node-count" key={count}>
        {count}
      </div>
      {children && <div className="node-sub">{children}</div>}
    </>
  )
  const cls = `flow-node${tone ? ` tone-${tone}` : ''}`
  return to ? (
    <Link className={cls} to={to}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  )
}

function SimulatorPanel() {
  const { overview, refresh } = useOps()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const sim = overview?.simulator
  const run = (f: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    f()
      .then(refresh)
      .catch((e: unknown) => setError(errorMessage(e)))
      .finally(() => setBusy(false))
  }
  if (!sim) return null
  const done = sim.cursor >= sim.total
  return (
    <section className="panel sim-panel" data-testid="simulator">
      <div className="panel-head">
        <h2>受信シミュレータ</h2>
        <span className="muted small">
          用意したメール・チャット {sim.total} 件を流す
        </span>
      </div>
      <div className="row">
        {sim.playing ? (
          <button type="button" disabled={busy} onClick={() => run(() => ops.simulator({ playing: false }))}>
            ⏸ 一時停止
          </button>
        ) : (
          <button type="button" disabled={busy || done} onClick={() => run(() => ops.simulator({ playing: true }))}>
            ▶ 受信を開始
          </button>
        )}
        <button type="button" className="secondary" disabled={busy || done} onClick={() => run(() => ops.simulator({ step: true }))}>
          1 件だけ受信
        </button>
        <label className="muted small" htmlFor="sim-interval">
          間隔
        </label>
        <select
          id="sim-interval"
          value={sim.fast ? 'fast' : sim.interval_s}
          disabled={busy}
          onChange={(e) =>
            run(() =>
              ops.simulator(e.target.value === 'fast' ? { fast: true } : { fast: false, interval_s: Number(e.target.value) }),
            )
          }
        >
          <option value="fast">高速（並列 {sim.fast_workers}）</option>
          {[...new Set([0.5, 1, 2, 4, 8, 15, sim.interval_s])].sort((a, b) => a - b).map((s) => (
            <option key={s} value={s}>
              {s} 秒ごと
            </option>
          ))}
        </select>
        <div className="sim-progress" aria-label="受信済みの件数">
          <span style={{ width: pct(sim.total ? sim.cursor / sim.total : 0) }} />
        </div>
        <span className="small">
          {sim.cursor} / {sim.total} 件
          {(overview?.flow.waiting ?? 0) > 0 && <span className="muted">・処理待ち {overview?.flow.waiting}</span>}
          {sim.playing && <span className="live-dot" aria-label="受信中" />}
        </span>
        <span className="spacer" />
        <button type="button" className="secondary" disabled={busy || sim.playing} onClick={() => run(() => ops.simulator({ rewind: true }))}>
          最初から
        </button>
        <button
          type="button"
          className="danger"
          disabled={busy}
          onClick={() => {
            if (window.confirm('件・経過・投稿をすべて消します（設定は残す）。よろしいですか？'))
              run(() => ops.reset())
          }}
        >
          受付箱を空にする
        </button>
      </div>
      {sim.fast && overview?.kev && (
        <div className="warn-box small">個人情報のガードに Kev を使う設定です。Kev は 1 件ずつなので、ガードは速くなりません</div>
      )}
      {error && <div className="error small">{error}</div>}
    </section>
  )
}

/** 運用で Kev を使う設定なのに Kev に接続できないとき、止まっている処理を知らせる。 */
export function KevDownBanner() {
  // 数秒おきに読む概要に Kev の状態（と、Kev を使っている処理）が入るので、落ちた・戻ったがそのまま反映される
  const { overview } = useOps()
  const kev = overview?.kev
  if (!kev || kev.available || !kev.uses.length) return null
  return (
    <div className="warn-box" role="alert" data-testid="kev-down">
      Kev（{kev.endpoint}）に接続できません。{kev.uses.join('・')}が止まっています。Kev を起動してください
    </div>
  )
}

/**
 * 処理フローの進み具合。処理待ちが 0 から増えたときを 1 回の処理の始まりとし、
 * それまでに終わっていた件を除いて「全 N 件中、何件終わったか」を出す（取り込み・受信のまとまりごと）。
 */
// 処理中に進み具合を読む間隔（ダッシュボード全体の更新より短くして、数が細かく進むようにする）
const PROGRESS_MS = 300

/** 表示する数を、目標の数まで 1 つずつ（離れているときは少し大きく）進める。 */
function useCountUp(target: number): number {
  const [shown, setShown] = useState(target)
  useEffect(() => {
    if (shown === target) return
    const frame = requestAnimationFrame(() => {
      const diff = target - shown
      // 1 回の読み取りの間（約 18 フレーム）に追いつく幅で進める
      const step = Math.max(1, Math.ceil(Math.abs(diff) / 18))
      setShown(diff > 0 ? Math.min(target, shown + step) : Math.max(target, shown - step))
    })
    return () => cancelAnimationFrame(frame)
  }, [shown, target])
  return shown
}

function FlowProgress({ received: r0, waiting: w0, imported }: { received: number; waiting: number; imported: number | null }) {
  // 処理中は、軽い進み具合の API を短い間隔で読む（ダッシュボード全体の更新は 1.5 秒ごとで、まとめて進んで見えるため）
  const [live, setLive] = useState<{ received: number; waiting: number } | null>(null)
  const busy = w0 > 0 || (live?.waiting ?? 0) > 0
  useEffect(() => {
    if (!busy) return
    let stop = false
    const tick = () => {
      ops
        .progress()
        .then((p) => {
          if (!stop) setLive(p)
        })
        .catch(() => {
          // 読めなかったときは、ダッシュボード全体の更新の数を使う
        })
    }
    tick()
    const timer = setInterval(tick, PROGRESS_MS)
    return () => {
      stop = true
      clearInterval(timer)
    }
  }, [busy])
  // 新しい方の数を使う（受信した件数が多い方、同じなら処理待ちが少ない方）
  const useLive = live !== null && (live.received > r0 || (live.received === r0 && live.waiting <= w0))
  const received = useLive ? live.received : r0
  const waiting = useLive ? live.waiting : w0
  const [run, setRun] = useState<{ base: number; active: boolean } | null>(null)
  // 描画中に状態を合わせる（処理待ちの増減に合わせて、始まり・終わりを記録する）。
  // 取り込みの直後に開いたときの最初の処理は、取り込んだ件数を全体に数える（開くまでに終わった件も含める）
  if (waiting > 0 && !run?.active)
    setRun({ base: run === null && imported !== null ? Math.max(0, received - imported) : received - waiting, active: true })
  if (waiting === 0 && run?.active) setRun({ ...run, active: false })
  const base = Math.min(run?.base ?? 0, received)
  const total = received - base
  const done = useCountUp(total - waiting)
  return (
    <div className="row flow-progress" data-testid="flow-progress">
      <div className="sim-progress" role="progressbar" aria-label="処理済みの件数" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
        <span style={{ width: pct(total ? done / total : 0) }} />
      </div>
      <span className="small">
        {done} / {total} 件 処理済み
        {waiting > 0 && <span className="live-dot" aria-label="処理中" />}
      </span>
    </div>
  )
}

/** 管理（開発側）: 処理の状況。受信シミュレータ・お客様としてメールを送る・処理フロー。 */
export function AdminHome() {
  useTitle('処理の状況')
  const { overview, overviewError, settings, refresh } = useOps()
  const navigate = useNavigate()
  const [composing, setComposing] = useState(false)
  const f = overview?.flow
  // 件数が少ないうちは割合がぶれるので出さない
  const acc = (a: { n: number; matched: number } | undefined) => (a && a.n >= 5 ? pct(a.matched / a.n) : '-')
  const waitingHuman = f ? f.pii_review + f.review + f.escalated : 0
  // ファイル取り込みの後などに #flow で開いたら、処理フローまで送る
  const { hash, state } = useLocation()
  // ファイル取り込みから移ってきたときの、取り込んだ件数
  const imported = typeof state === 'object' && state !== null && 'imported' in state && typeof state.imported === 'number' ? state.imported : null
  const flowShown = f !== undefined
  useEffect(() => {
    if (hash === '#flow' && flowShown) document.getElementById('flow')?.scrollIntoView({ block: 'start' })
  }, [hash, flowShown])
  return (
    <Page wide crumbs={[{ label: '管理', to: '/admin' }, { label: '処理の状況' }]}>
      <AdminTabs />
      <div className="panel-head">
        <h1>処理の状況</h1>
        <button type="button" onClick={() => setComposing(true)}>
          ✉ お客様としてメールを送る
        </button>
      </div>
      {overviewError && <div className="error">状態の取得に失敗: {overviewError}</div>}
      <KevDownBanner />
      <SimulatorPanel />

      {f && (
        <section className="panel" data-testid="flow" id="flow">
          <h2>処理フロー</h2>
          <FlowProgress received={f.received} waiting={f.waiting} imported={imported} />
          <div className="flow">
            <Node title="受信" count={f.received} to="/ops/inbox" tone="accent">
              処理待ち {f.waiting}
            </Node>
            <span className="flow-arrow" aria-hidden>
              →
            </span>
            <Node
              title="ガードレール"
              who={settings ? (!settings.guard.enabled ? '無効' : settings.guard.use_model ? TARGET_NAMES[settings.guard.target] : '規則のみ') : undefined}
              count={f.pii_found}
              to="/ops/pii"
              tone="warn"
            >
              個人情報あり ／ マスク {f.masked}・ブロック {f.blocked}
              {f.pii_review > 0 && <span className="node-alert">人の確認待ち {f.pii_review}</span>}
              <KevQueueNote queue={overview?.kev_queue} available={overview?.kev?.available ?? true} />
            </Node>
            <span className="flow-arrow" aria-hidden>
              →
            </span>
            <Node
              title="仕分け"
              who={settings ? TARGET_NAMES[settings.classify.target] : undefined}
              count={f.classified}
              tone="accent"
            >
              {f.kev_only > 0 ? `うち Kev だけで確定 ${f.kev_only}` : '分類・抽出・優先度'}
            </Node>
            <span className="flow-arrow" aria-hidden>
              →
            </span>
            <div className="flow-branches">
              <Node title="自動で振り分け" count={f.auto} to="/admin/channels" tone="ok">
                チャンネルへ投稿
              </Node>
              <Node title="分類の確認" count={f.review} to="/ops/review" tone="warn">
                人が承認・修正
              </Node>
              <Node title="エスカレーション" count={f.escalated} to="/ops/escalations" tone="ng">
                担当者が対応
              </Node>
            </div>
            <span className="flow-arrow" aria-hidden>
              →
            </span>
            <Node title="対応完了" count={f.closed} tone="muted">
              {f.error > 0 ? <span className="node-alert">エラー {f.error}</span> : '担当者が完了した件'}
            </Node>
          </div>
        </section>
      )}

      {overview && f && (
        <section className="kpis" data-testid="kpis">
          <Kpi
            label="自動処理率"
            value={overview.automation_rate === null || f.classified < 5 ? '-' : pct(overview.automation_rate, 0)}
            tone="ok"
          />
          <Kpi label="対応待ち" value={waitingHuman} sub={`個人情報 ${f.pii_review} ／ 確認 ${f.review} ／ エスカレ ${f.escalated}`} tone={waitingHuman ? 'warn' : undefined} />
          <Kpi
            label="想定ラベルとの一致（最終）"
            value={acc(overview.final_accuracy)}
            sub={`モデルの予測だけなら ${acc(overview.model_accuracy)}（${overview.final_accuracy.n}件）`}
          />
          <Kpi label="外部に送らなかった件" value={f.blocked + f.kev_only} sub={`ブロック ${f.blocked} ／ Kev で完結 ${f.kev_only}`} />
          <Kpi label="コスト（受付箱の分）" value={costText(overview.cost_usd)} />
        </section>
      )}

      {overview && (
        <section className="panel" data-testid="activity">
          <div className="panel-head">
            <h2>最新の動き</h2>
            <Link to="/ops/inbox" className="small">
              受付箱をすべて見る
            </Link>
          </div>
          {overview.recent.length === 0 ? (
            <p className="muted">まだ受信していません</p>
          ) : (
            <ul className="activity">
              {overview.recent.map((e) => (
                <li key={e.id} className={`actor-${e.actor}`} style={{ '--delay': '0s' } as CSSProperties}>
                  <span className="muted small">{clockTime(e.at)}</span>
                  <span className="actor">{ACTOR_LABELS[e.actor]}</span>
                  <Link to={`/ops/inbox?id=${encodeURIComponent(e.item_id)}`}>{e.item_id}</Link>
                  <span className="act-msg">{e.message}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {composing && (
        <Compose
          onClose={() => setComposing(false)}
          onSent={(item) => {
            setComposing(false)
            refresh()
            navigate(`/ops/inbox?id=${encodeURIComponent(item.id)}`)
          }}
        />
      )}
    </Page>
  )
}
