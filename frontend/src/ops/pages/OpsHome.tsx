import { useState, type CSSProperties, type ReactNode } from 'react'
import { Link } from 'react-router'
import { errorMessage } from '../../api'
import { pct } from '../../format'
import { Page, useTitle } from '../../shell'
import { ops } from '../api'
import { costText, Kpi } from '../components'
import { ACTOR_LABELS, clockTime } from '../format'
import { useOps } from '../state'

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
          デモのメール・チャット {sim.total} 件を流す
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
          value={sim.interval_s}
          disabled={busy}
          onChange={(e) => run(() => ops.simulator({ interval_s: Number(e.target.value) }))}
        >
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
      {error && <div className="error small">{error}</div>}
    </section>
  )
}

const SCENARIO: { title: string; body: string; to: string; link: string }[] = [
  { title: '受信を始める', body: '「受信を開始」を押す', to: '/ops/inbox', link: '受付箱を開く' },
  { title: '個人情報を確認する', body: '確かめて送る', to: '/ops/pii', link: '個人情報の確認' },
  { title: '分類の確認を捌く', body: '1〜4 キーで確定', to: '/ops/review', link: '分類の確認' },
  { title: 'エスカレーションに対応する', body: '担当を決めて完了', to: '/ops/escalations', link: 'エスカレーション' },
  { title: '振り分け結果を見る', body: '投稿を見る', to: '/ops/channels', link: 'チャンネル' },
  { title: '閾値を見直す', body: '確認結果から決める', to: '/ops/tuning', link: '閾値の調整' },
]

function Scenario() {
  return (
    <details className="panel scenario">
      <summary>
        <strong>デモの進め方</strong> <span className="muted small">（6 ステップ）</span>
      </summary>
      <ol>
        {SCENARIO.map((s) => (
          <li key={s.title}>
            <strong>{s.title}</strong>
            <span className="muted small"> {s.body} </span>
            <Link className="small" to={s.to}>
              {s.link} →
            </Link>
          </li>
        ))}
      </ol>
    </details>
  )
}

/** 運用で Kev を使う設定なのに Kev に接続できないとき、止まっている処理を知らせる。 */
function KevDownBanner() {
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

export function OpsHome() {
  useTitle('運用ダッシュボード')
  const { overview, overviewError, settings } = useOps()
  const f = overview?.flow
  // 件数が少ないうちは割合がぶれるので出さない
  const acc = (a: { n: number; matched: number } | undefined) => (a && a.n >= 5 ? pct(a.matched / a.n) : '-')
  const waitingHuman = f ? f.pii_review + f.review + f.escalated : 0
  return (
    <Page wide crumbs={[{ label: '運用' }, { label: 'ダッシュボード' }]}>
      <div className="panel-head">
        <h1>こもれび雑貨店 サポート窓口</h1>
        <span className="muted small">受信 → ガードレール → 仕分け → 振り分け</span>
      </div>
      {overviewError && <div className="error">状態の取得に失敗: {overviewError}</div>}
      <KevDownBanner />
      <Scenario />
      <SimulatorPanel />

      {f && (
        <section className="panel" data-testid="flow">
          <h2>処理の流れ</h2>
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
              <Node title="自動で振り分け" count={f.auto} to="/ops/channels" tone="ok">
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
            sub="人を介さず振り分けた割合"
            tone="ok"
          />
          <Kpi label="人の対応待ち" value={waitingHuman} sub={`個人情報 ${f.pii_review} ／ 確認 ${f.review} ／ エスカレ ${f.escalated}`} tone={waitingHuman ? 'warn' : undefined} />
          <Kpi
            label="想定ラベルとの一致（最終）"
            value={acc(overview.final_accuracy)}
            sub={`モデルの予測だけなら ${acc(overview.model_accuracy)}（${overview.final_accuracy.n}件）`}
          />
          <Kpi label="抜き取り確認の待ち" value={overview.audit_pending} sub={settings ? `自動分の ${pct(settings.audit_rate, 0)} を抜き取り` : undefined} />
          <Kpi label="外部に送らなかった件" value={f.blocked + f.kev_only} sub={`ブロック ${f.blocked} ／ Kev で完結 ${f.kev_only}`} />
          <Kpi label="コスト（受付箱の分）" value={costText(overview.cost_usd)} sub="Jev の料金（Kev・MOCK は課金なし）" />
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
                  <Link to={`/ops/items/${e.item_id}`}>{e.item_id}</Link>
                  <span className="act-msg">{e.message}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </Page>
  )
}
