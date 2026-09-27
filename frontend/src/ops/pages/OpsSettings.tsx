import { type ReactNode } from 'react'
import { Link } from 'react-router'
import { type Mode } from '../../api'
import { Page, targetStatus, useShell, useTitle } from '../../shell'
import type { CategoryDef, PiiAction, PiiType, Settings } from '../api'
import { useAutoSave } from '../autosave'
import { SaveState } from '../components'
import { AdminTabs, SettingsTabs } from '../tabs'
import { ACTION_LABELS, ACTION_NOTES } from '../format'
import { useOps } from '../state'

const TARGETS: readonly Mode[] = ['custom', 'jev', 'mock']
const GUARD_TARGETS = ['custom', 'mock'] as const

const TARGET_NAMES: Record<Mode, string> = { custom: 'Kev（ローカル・閉域）', jev: 'Jev（外部・課金あり）', mock: 'MOCK（API を呼ばない）' }
const PII_ORDER: PiiType[] = ['person_name', 'phone', 'email', 'sns_account', 'postal_code', 'address', 'birthday', 'card', 'bank_account']
const ACTIONS: PiiAction[] = ['allow', 'mask', 'block']

function Section({ id, title, desc, children }: { id: string; title: string; desc?: string; children: ReactNode }) {
  return (
    <section className="panel settings-section" id={id}>
      <h2>{title}</h2>
      {desc && <p className="muted small">{desc}</p>}
      {children}
    </section>
  )
}

// 閾値の目盛りは 0.05 刻み（細かすぎても差が読めないため）。割合など閾値でないものは step で変える
function Threshold({ label, value, onChange, note, step = 0.05, min = 0 }: { label: string; value: number; onChange: (v: number) => void; note?: string; step?: number; min?: number }) {
  return (
    <label className="threshold">
      <span>{label}</span>
      <input type="range" min={min} max={1} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} aria-label={label} />
      <span className="num">{value.toFixed(2)}</span>
      {note && <span className="muted small">{note}</span>}
    </label>
  )
}

function TargetSelect<T extends Mode>({
  value,
  onChange,
  id,
  options,
}: {
  value: T
  onChange: (m: T) => void
  id: string
  options: readonly T[]
}) {
  const { status } = useShell()
  return (
    <select id={id} value={value} onChange={(e) => onChange(options.find((o) => o === e.target.value) ?? value)}>
      {options.map((t) => {
        const ts = targetStatus(status, t)
        return (
          <option key={t} value={t}>
            {TARGET_NAMES[t]}
            {ts && !ts.available ? '（いまは使えません）' : ''}
          </option>
        )
      })}
    </select>
  )
}

type Key = keyof Settings

/** 画面ごとに編集する項目を決める（ほかの画面の項目は、保存のときに最新の値を使う）。 */
function keysOf(keys: readonly Key[]) {
  return {
    changed: (d: Settings, s: Settings) => keys.some((k) => JSON.stringify(d[k]) !== JSON.stringify(s[k])),
    merge: (latest: Settings, d: Settings): Settings => ({ ...latest, ...Object.fromEntries(keys.map((k) => [k, d[k]])) }),
  }
}
const USER = keysOf(['categories', 'fallback_category', 'guard', 'classify'])
const ADMIN = keysOf(['guard', 'classify', 'kev_first', 'assign'])

const MAX_ACTIVE = 9

// 振り分けた後の扱い（設定の auto_close・judge_reply の組み合わせを 1 つの選択にする）
type AfterRoute = 'assign' | 'done' | 'judge'
const AFTER_ROUTE_LABELS: Record<AfterRoute, string> = {
  assign: '担当に回す',
  done: '対応不要として完了',
  judge: '対応が要るかを Jev が判断',
}
const afterRouteOf = (x: { auto_close: boolean; judge_reply: boolean }): AfterRoute =>
  x.auto_close ? 'done' : x.judge_reply ? 'judge' : 'assign'
const afterRouteFields = (v: AfterRoute) => ({ auto_close: v === 'done', judge_reply: v === 'judge' })

/**
 * 分類が想定外の答えになったときの振り分け先（fallback_category）は、利用者に選ばせず自動で決める。
 * いまの値が有効ならそのまま、外れたら「その他」、なければ「担当に回す」分類にする（自動で完了にする分類は避ける）。
 */
function withFallback(s: Settings): Settings {
  const active = s.categories.filter((x) => x.active)
  // 想定外の件を黙って完了にしないよう、「担当に回す」分類を優先する
  const next = active.find((x) => x.key === 'other') ?? active.find((x) => !x.auto_close && !x.judge_reply) ?? active.find((x) => !x.auto_close) ?? active[0]
  const current = active.find((x) => x.key === s.fallback_category)
  if ((current && !current.auto_close) || next === undefined) return s
  return { ...s, fallback_category: next.key }
}

function categoriesInvalid(d: Settings): string | null {
  const active = d.categories.filter((x) => x.active)
  const bad = d.categories.find((x) => !x.label.trim() || !x.criteria.trim() || !/^#\S+$/.test(x.channel))
  return bad
    ? `分類「${bad.label || '新しい分類'}」: 表示名・説明を入れ、チャンネルは #名前 の形にしてください`
    : active.length === 0
      ? '分類を 1 つ以上にしてください'
      : active.length > MAX_ACTIVE
        ? `分類は ${MAX_ACTIVE} 個までです`
        : null
}

const thresholdInvalid = (d: Settings): string | null =>
  d.classify.review_threshold > d.classify.auto_threshold ? '確認待ちの閾値が自動の閾値を超えています' : null
const settingsInvalid = (d: Settings): string | null => categoriesInvalid(d) ?? thresholdInvalid(d)

export function OpsSettings() {
  useTitle('設定')
  const { meta, settingsError, overview } = useOps()
  const used = new Set(overview?.used_categories ?? [])
  const auto = useAutoSave(USER.changed, USER.merge, settingsInvalid)
  const { draft, set } = auto
  if (!draft) return <Page crumbs={[{ label: '運用', to: '/ops' }, { label: '設定' }]}>{settingsError ?? '読み込み中…'}</Page>
  const g = draft.guard
  const c = draft.classify
  const catError = categoriesInvalid(draft)
  const setCat = (key: string, patch: Partial<CategoryDef>) =>
    set((s) => withFallback({ ...s, categories: s.categories.map((x) => (x.key === key ? { ...x, ...patch } : x)) }))
  return (
    <Page crumbs={[{ label: '運用', to: '/ops' }, { label: '設定' }]}>
      <SettingsTabs />
      <div {...auto.handlers}>
      <div className="panel-head">
        <h1>分類</h1>
        <SaveState status={auto.status} error={auto.error} />
      </div>

      <Section id="categories" title="分類とチャンネル" desc="Jev は説明を読んで分類し、そのチャンネルに投稿します。">
        <div className="scroll">
          <table className="category-table cards-narrow" data-testid="category-table">
            <thead>
              <tr>
                <th>分類名</th>
                <th>説明（Jev が読む）</th>
                <th>投稿先チャンネル</th>
                <th>振り分けた後</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {draft.categories
                .filter((x) => x.active)
                .map((x) => (
                  <tr key={x.key}>
                    <td>
                      <input aria-label={`${x.label}の分類名`} value={x.label} maxLength={20} onChange={(e) => setCat(x.key, { label: e.target.value })} />
                    </td>
                    <td>
                      <textarea aria-label={`${x.label}の説明`} rows={2} value={x.criteria} maxLength={300} onChange={(e) => setCat(x.key, { criteria: e.target.value })} />
                    </td>
                    <td>
                      <input aria-label={`${x.label}の投稿先チャンネル`} value={x.channel} maxLength={40} onChange={(e) => setCat(x.key, { channel: e.target.value })} />
                    </td>
                    <td>
                      <select
                        aria-label={`${x.label}を振り分けた後`}
                        value={afterRouteOf(x)}
                        onChange={(e) => setCat(x.key, afterRouteFields(e.target.value as AfterRoute))}
                      >
                        {(Object.keys(AFTER_ROUTE_LABELS) as AfterRoute[]).map((v) => (
                          <option key={v} value={v}>
                            {AFTER_ROUTE_LABELS[v]}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="link-btn"
                        disabled={draft.categories.filter((y) => y.active).length <= 1}
                        onClick={() => {
                          const usedBefore = used.has(x.key)
                          const msg = usedBefore
                            ? `分類「${x.label}」を削除しますか？\n過去の件はこの分類のまま残ります。`
                            : `分類「${x.label}」を削除しますか？`
                          if (!window.confirm(msg)) return
                          // 過去の件で使った分類は、一覧から外すだけにする（件がキーでつながっているため）
                          set((s) => withFallback(usedBefore ? { ...s, categories: s.categories.map((y) => (y.key === x.key ? { ...y, active: false } : y)) } : { ...s, categories: s.categories.filter((y) => y.key !== x.key) }))
                          void auto.commit()
                        }}
                      >
                        削除
                      </button>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        <button
          type="button"
          disabled={draft.categories.filter((x) => x.active).length >= MAX_ACTIVE}
          onClick={() =>
            set((s) => ({ ...s, categories: [...s.categories, { key: `cat-${Date.now().toString(36)}`, label: '', criteria: '', channel: '#', active: true, auto_close: false, judge_reply: false }] }))
          }
        >
          ＋ 分類を追加
        </button>
        {draft.categories.some((x) => !x.active) && (
          <details className="small">
            <summary>削除した分類（{draft.categories.filter((x) => !x.active).length}）</summary>
            <ul>
              {draft.categories
                .filter((x) => !x.active)
                .map((x) => (
                  <li key={x.key}>
                    {x.label}{' '}
                    <button
                      type="button"
                      className="link-btn"
                      disabled={draft.categories.filter((y) => y.active).length >= MAX_ACTIVE}
                      onClick={() => {
                        setCat(x.key, { active: true })
                        void auto.commit()
                      }}
                    >
                      戻す
                    </button>
                  </li>
                ))}
            </ul>
          </details>
        )}
        {catError && <div className="error small">{catError}</div>}
        <p className="note">
          振り分けた後
          <br />
          ・担当に回す: 担当を割り当てて Slack でメンションする。
          <br />
          ・対応不要として完了: Slack に投稿し、完了を書いて ✅ を付ける（お礼など）。
          <br />
          ・対応が要るかを Jev が判断: 要る件は担当に回し、要らない件は完了にする（その他など）。
        </p>
      </Section>

      <Section id="auto" title="自動で振り分ける確信度" desc="これ以上なら、人を通さずに振り分けます。">
        <Threshold label="自動で振り分ける確信度" min={c.review_threshold} value={c.auto_threshold} onChange={(v) => set((s) => ({ ...s, classify: { ...s.classify, auto_threshold: v } }))} />
        {Object.keys(c.label_thresholds).length > 0 && (
          <ul className="label-thresholds small">
            {Object.entries(c.label_thresholds).map(([k, v]) => (
              <li key={k}>
                {meta?.category_labels[k] ?? k}: <strong>{v.toFixed(2)}</strong>（成績で変更）{' '}
                <button
                  type="button"
                  className="link-btn"
                  onClick={() =>
                    set((s) => ({ ...s, classify: { ...s.classify, label_thresholds: Object.fromEntries(Object.entries(s.classify.label_thresholds).filter(([x]) => x !== k)) } }))
                  }
                >
                  共通の値に戻す
                </button>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section id="pii" title="個人情報">
        <label className="small block">
          <input type="checkbox" checked={g.use_model} disabled={!g.enabled} onChange={(e) => set((s) => ({ ...s, guard: { ...s.guard, use_model: e.target.checked } }))} /> 氏名などを Kev で判定する
        </label>
        <label className="small block">
          <input type="checkbox" checked={g.human_check} onChange={(e) => set((s) => ({ ...s, guard: { ...s.guard, human_check: e.target.checked } }))} /> 見つけたら人が確認する
        </label>
        <p className="muted small">
          Kev を使わないときは、規則で見つけた候補をすべて伏せます。
          <br />
          人が確認しないときは、見つけた個人情報を自動で伏せて送ります。
        </p>
      </Section>
      </div>
    </Page>
  )
}

/** 管理（開発側）: 細かい設定。利用者の設定画面には出さない。 */
export function AdminSettings() {
  useTitle('詳細設定')
  const { meta, settingsError } = useOps()
  const { status } = useShell()
  const auto = useAutoSave(ADMIN.changed, ADMIN.merge, thresholdInvalid)
  const { draft, set } = auto
  if (!draft) return <Page crumbs={[{ label: '管理', to: '/admin' }, { label: '詳細設定' }]}>{settingsError ?? '読み込み中…'}</Page>
  const g = draft.guard
  const c = draft.classify
  const a = draft.assign
  const labels: Record<string, string> = meta?.pii_types ?? {}
  const warn = (t: Mode) => {
    const ts = targetStatus(status, t)
    return ts && !ts.available ? <div className="warn-box small">{ts.reason}</div> : null
  }
  return (
    <Page crumbs={[{ label: '管理', to: '/admin' }, { label: '詳細設定' }]}>
      <AdminTabs />
      <div {...auto.handlers}>
      <div className="panel-head">
        <h1>詳細設定</h1>
        <SaveState status={auto.status} error={auto.error} />
      </div>

      <Section id="guard" title="個人情報のガードレール" desc="Jev に送る前にマスク・ブロック">
        <div className="form-grid">
          <label htmlFor="g-enabled">ガードレール</label>
          <div>
            <label className="small">
              <input
                id="g-enabled"
                type="checkbox"
                checked={g.enabled}
                onChange={(e) => {
                  const on = e.target.checked
                  if (!on && !window.confirm('ガードレールを外すと、元の本文のまま Jev に送ります。外しますか？')) return
                  set((s) => ({ ...s, guard: { ...s.guard, enabled: on } }))
                }}
              /> 有効にする
            </label>
            {!g.enabled && (
              <div className="error small" role="alert">
                元の本文のまま Jev に送ります。ダミーデータ以外では無効にしない
              </div>
            )}
          </div>
          <label htmlFor="g-model">Kev で判定する</label>
          <div>
            <label className="small">
              <input id="g-model" type="checkbox" checked={g.use_model} disabled={!g.enabled} onChange={(e) => set((s) => ({ ...s, guard: { ...s.guard, use_model: e.target.checked } }))} /> 氏名などの候補をモデルで判定する
            </label>
            <div className="muted small">
              オフ: 規則だけ。候補はすべてマスク、候補外の見落としは調べない
            </div>
          </div>
          <label htmlFor="g-target">判定に使う接続先</label>
          <div>
            <TargetSelect id="g-target" options={GUARD_TARGETS} value={g.target} onChange={(t) => set((s) => ({ ...s, guard: { ...s.guard, target: t } }))} />
            <div className="muted small">マスク前の本文を読むため、Jev は選べません</div>
            {warn(g.target)}
          </div>
          <label htmlFor="g-human">人の確認</label>
          <label className="small">
            <input id="g-human" type="checkbox" checked={g.human_check} onChange={(e) => set((s) => ({ ...s, guard: { ...s.guard, human_check: e.target.checked } }))} /> 検出したら人が確認（オフなら自動でマスク・ブロック）
          </label>
        </div>
        <Threshold label="候補を個人情報とみなす確率" value={g.candidate_threshold} onChange={(v) => set((s) => ({ ...s, guard: { ...s.guard, candidate_threshold: v } }))} />
        <Threshold label="取りこぼしを疑う確率" value={g.leftover_threshold} onChange={(v) => set((s) => ({ ...s, guard: { ...s.guard, leftover_threshold: v } }))} note="以上なら人が確認" />
        <h3>種類ごとの方針</h3>
        <div className="scroll">
          <table className="policy-table" data-testid="policy-table">
            <thead>
              <tr>
                <th>種類</th>
                {ACTIONS.map((a) => (
                  <th key={a}>
                    {ACTION_LABELS[a]}
                    <div className="muted small">{ACTION_NOTES[a]}</div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {PII_ORDER.map((t) => (
                <tr key={t}>
                  <td>{labels[t] ?? t}</td>
                  {ACTIONS.map((a) => (
                    <td key={a}>
                      <input
                        type="radio"
                        name={`policy-${t}`}
                        aria-label={`${labels[t] ?? t}: ${ACTION_LABELS[a]}`}
                        checked={g.policy[t] === a}
                        onChange={() => {
                          if (a === 'allow' && !window.confirm(`${labels[t] ?? t}を「${ACTION_LABELS[a]}」にすると、そのまま Jev に送ります。変えますか？`)) return
                          set((s) => ({ ...s, guard: { ...s.guard, policy: { ...s.guard.policy, [t]: a } } }))
                        }}
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="form-grid">
          <label htmlFor="g-blocked">ブロックした件</label>
          <select id="g-blocked" value={g.blocked_route} onChange={(e) => set((s) => ({ ...s, guard: { ...s.guard, blocked_route: e.target.value === 'kev' ? 'kev' : 'human' } }))}>
            <option value="human">人に回す（エスカレーション）</option>
            <option value="kev">Kev だけで仕分ける（外部に出さない）</option>
          </select>
        </div>
      </Section>

      <Section id="classify" title="仕分け" desc="確信度で振り分け">
        <div className="form-grid">
          <label htmlFor="c-target">判定に使う接続先</label>
          <div>
            <TargetSelect id="c-target" options={TARGETS} value={c.target} onChange={(t) => set((s) => ({ ...s, classify: { ...s.classify, target: t } }))} />
            {warn(c.target)}
          </div>
        </div>
        <Threshold label="自動で振り分ける確信度" value={c.auto_threshold} onChange={(v) => set((s) => ({ ...s, classify: { ...s.classify, auto_threshold: v } }))} note="以上なら自動" />
        <Threshold label="確認待ちにする確信度" value={c.review_threshold} onChange={(v) => set((s) => ({ ...s, classify: { ...s.classify, review_threshold: v } }))} note="未満はエスカレーション" />
        {c.review_threshold > c.auto_threshold && <div className="error small">確認待ちは自動以下にしてください</div>}
        <h3>分類ごとの自動の閾値</h3>
        {Object.keys(c.label_thresholds).length === 0 ? (
          <p className="muted small">
            すべて {c.auto_threshold.toFixed(2)}（<Link to="/ops/tuning">成績</Link>で変更）
          </p>
        ) : (
          <ul className="label-thresholds">
            {Object.entries(c.label_thresholds).map(([k, v]) => (
              <li key={k}>
                {meta?.category_labels[k] ?? k}: <strong>{v.toFixed(2)}</strong>{' '}
                <button
                  type="button"
                  className="link-btn"
                  onClick={() =>
                    set((s) => ({ ...s, classify: { ...s.classify, label_thresholds: Object.fromEntries(Object.entries(s.classify.label_thresholds).filter(([x]) => x !== k)) } }))
                  }
                >
                  共通の値に戻す
                </button>
              </li>
            ))}
          </ul>
        )}
        <h3>業務ルール</h3>
        <label className="small block">
          <input type="checkbox" checked={c.escalate_strong_frustration} onChange={(e) => set((s) => ({ ...s, classify: { ...s.classify, escalate_strong_frustration: e.target.checked } }))} /> 強い不満はエスカレーション
        </label>
        <Threshold label="強い不満とみなす確率" value={c.strong_frustration_at} onChange={(v) => set((s) => ({ ...s, classify: { ...s.classify, strong_frustration_at: v } }))} note="不満度 2 の確率" />
        <label className="small block">
          <input type="checkbox" checked={c.escalate_urgent} onChange={(e) => set((s) => ({ ...s, classify: { ...s.classify, escalate_urgent: e.target.checked } }))} /> 緊急は確信度に関係なくエスカレーション
        </label>
        <Threshold label="僅差とみなす差" value={c.split_margin} onChange={(v) => set((s) => ({ ...s, classify: { ...s.classify, split_margin: v } }))} note="上位 2 つの差がこれ未満なら人が確認" />
        <label className="small block">
          <input type="checkbox" checked={c.insufficient_gate} onChange={(e) => set((s) => ({ ...s, classify: { ...s.classify, insufficient_gate: e.target.checked } }))} /> 判断材料が足りない件は人が確認
        </label>
        <Threshold label="足りないとみなす確率" value={c.insufficient_at} onChange={(v) => set((s) => ({ ...s, classify: { ...s.classify, insufficient_at: v } }))} note="これ以上なら自動にしない" />
      </Section>

      <Section id="kev-first" title="Kev で先に判定" desc="十分な確信度なら Jev を呼ばない">
        <label className="small block">
          <input type="checkbox" checked={draft.kev_first.enabled} onChange={(e) => set((s) => ({ ...s, kev_first: { ...s.kev_first, enabled: e.target.checked } }))} /> 有効にする
        </label>
        <Threshold label="Kev で確定する確信度" value={draft.kev_first.threshold} onChange={(v) => set((s) => ({ ...s, kev_first: { ...s.kev_first, threshold: v } }))} />
        {draft.kev_first.enabled && warn('custom')}
        <p className="note">使う前に評価で一致率を確認</p>
      </Section>

      <Section id="assign" title="担当の自動の割り当て">
        <label className="small block">
          <input type="checkbox" checked={a.auto} onChange={(e) => set((s) => ({ ...s, assign: { ...s.assign, auto: e.target.checked } }))} /> 確率が高い件は自動で割り当てる
        </label>
        <Threshold label="自動で割り当てる確率" value={a.threshold} onChange={(v) => set((s) => ({ ...s, assign: { ...s.assign, threshold: v } }))} />
        <label className="small block">
          <input type="checkbox" checked={a.use_examples} onChange={(e) => set((s) => ({ ...s, assign: { ...s.assign, use_examples: e.target.checked } }))} /> 最近完了した件（{a.max_examples} 件まで）を例として渡す
        </label>
        <label className="small block">
          担当範囲の案は完了{' '}
          <input
            type="number"
            className="num-input"
            min={1}
            max={200}
            value={a.scope_draft_min}
            aria-label="担当範囲の案を作れる完了件数"
            onChange={(e) => {
              const v = Math.round(Number(e.target.value))
              if (Number.isFinite(v) && v >= 1 && v <= 200) set((s) => ({ ...s, assign: { ...s.assign, scope_draft_min: v } }))
            }}
          />{' '}
          件から作れる
        </label>
      </Section>
      </div>
    </Page>
  )
}
