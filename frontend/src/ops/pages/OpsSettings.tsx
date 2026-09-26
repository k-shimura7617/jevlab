import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { type Mode } from '../../api'
import { pct } from '../../format'
import { Page, targetStatus, useShell, useTitle } from '../../shell'
import type { PiiAction, PiiType, Settings } from '../api'
import { useAutoSave } from '../autosave'
import { SaveState, WeightSliders } from '../components'
import { ACTION_LABELS, ACTION_NOTES } from '../format'
import { useOps } from '../state'

const TARGETS: readonly Mode[] = ['custom', 'jev', 'mock']
const GUARD_TARGETS = ['custom', 'mock'] as const
const WEEKDAYS = ['月', '火', '水', '木', '金', '土', '日']

const TARGET_NAMES: Record<Mode, string> = { custom: 'Kev（ローカル・閉域）', jev: 'Jev（外部・課金あり）', mock: 'MOCK（API を呼ばない）' }
const PII_ORDER: PiiType[] = ['person_name', 'phone', 'email', 'sns_account', 'postal_code', 'address', 'birthday', 'card', 'bank_account']
const ACTIONS: PiiAction[] = ['allow', 'mask', 'block']

function Section({ id, title, desc, children }: { id: string; title: string; desc: string; children: ReactNode }) {
  return (
    <section className="panel settings-section" id={id}>
      <h2>{title}</h2>
      <p className="muted small">{desc}</p>
      {children}
    </section>
  )
}

// 閾値の目盛りは 0.05 刻み（細かすぎても差が読めないため）。割合など閾値でないものは step で変える
function Threshold({ label, value, onChange, note, step = 0.05 }: { label: string; value: number; onChange: (v: number) => void; note?: string; step?: number }) {
  return (
    <label className="threshold">
      <span>{label}</span>
      <input type="range" min={0} max={1} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} aria-label={label} />
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

// この画面で編集する項目（ほかの画面の項目は、保存のときに最新の値を使う）
const KEYS = ['guard', 'classify', 'kev_first', 'audit_rate', 'sla', 'priority_weights'] as const
const settingsChanged = (d: Settings, s: Settings) => KEYS.some((k) => JSON.stringify(d[k]) !== JSON.stringify(s[k]))
const mergeSettings = (latest: Settings, d: Settings): Settings => ({ ...latest, ...Object.fromEntries(KEYS.map((k) => [k, d[k]])) })

function slaInvalid(sla: Settings['sla']): string | null {
  return !(sla.hours > 0)
    ? '対応目安は 0 より大きくしてください'
    : sla.start >= sla.end
      ? '営業時間の開始は終了より前にしてください'
      : sla.days.length === 0
        ? '営業日を選んでください'
        : sla.holidays.some((d) => !/^\d{4}-\d{2}-\d{2}$/.test(d))
          ? '休業日は 2026-01-01 の形で書いてください'
          : null
}

const settingsInvalid = (d: Settings): string | null =>
  d.classify.review_threshold > d.classify.auto_threshold ? '確認待ちの閾値が自動の閾値を超えています' : slaInvalid(d.sla)

export function OpsSettings() {
  useTitle('運用の設定')
  const { meta, settingsError } = useOps()
  const { status } = useShell()
  // 休業日の欄は入力中の文字列をそのまま持つ（区切りのカンマを打った途端に消えないように）
  const [holidayText, setHolidayText] = useState<string | null>(null)
  const auto = useAutoSave(settingsChanged, mergeSettings, settingsInvalid)
  const { draft, set } = auto
  if (!draft) return <Page crumbs={[{ label: '運用', to: '/ops' }, { label: '設定' }]}>{settingsError ?? '読み込み中…'}</Page>
  const g = draft.guard
  const c = draft.classify
  const sla = draft.sla
  const slaError = slaInvalid(sla)
  const labels: Record<string, string> = meta?.pii_types ?? {}
  const warn = (t: Mode) => {
    const ts = targetStatus(status, t)
    return ts && !ts.available ? <div className="warn-box small">{ts.reason}</div> : null
  }
  return (
    <Page crumbs={[{ label: '運用', to: '/ops' }, { label: '設定' }]}>
      <div {...auto.handlers}>
      <div className="panel-head">
        <h1>運用の設定</h1>
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
            すべて {c.auto_threshold.toFixed(2)}（<Link to="/ops/tuning">閾値の調整</Link>で変更）
          </p>
        ) : (
          <ul className="label-thresholds">
            {Object.entries(c.label_thresholds).map(([k, v]) => (
              <li key={k}>
                {meta?.categories[k] ?? k}: <strong>{v.toFixed(2)}</strong>{' '}
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

      <Section id="audit" title="抜き取り確認" desc="自動分の一部を人が確認">
        <Threshold label="抜き取る割合" step={0.01} value={draft.audit_rate} onChange={(v) => set((s) => ({ ...s, audit_rate: v }))} note={pct(draft.audit_rate, 0)} />
      </Section>

      <Section id="sla" title="対応目安" desc="営業時間で数える">
        <div className="form-grid">
          <label htmlFor="sla-hours">対応目安</label>
          <label className="small">
            <input
              id="sla-hours"
              type="number"
              className="num-input"
              min={0.5}
              step={0.5}
              value={Number.isFinite(sla.hours) ? sla.hours : ''}
              onChange={(e) => set((s) => ({ ...s, sla: { ...s.sla, hours: e.target.value === '' ? Number.NaN : Number(e.target.value) } }))}
            />{' '}
            営業時間
          </label>
          <label htmlFor="sla-start">営業時間</label>
          <span className="small">
            <input id="sla-start" type="time" value={sla.start} onChange={(e) => set((s) => ({ ...s, sla: { ...s.sla, start: e.target.value } }))} /> 〜{' '}
            <input aria-label="営業時間の終わり" type="time" value={sla.end} onChange={(e) => set((s) => ({ ...s, sla: { ...s.sla, end: e.target.value } }))} />
          </span>
          <span>営業日</span>
          <span className="small" role="group" aria-label="営業日">
            {WEEKDAYS.map((w, d) => (
              <label key={w} className="day-check">
                <input
                  type="checkbox"
                  checked={sla.days.includes(d)}
                  onChange={(e) =>
                    set((s) => ({ ...s, sla: { ...s.sla, days: e.target.checked ? [...s.sla.days, d].sort() : s.sla.days.filter((x) => x !== d) } }))
                  }
                />
                {w}
              </label>
            ))}
          </span>
          <label htmlFor="sla-holidays">休業日</label>
          <input
            id="sla-holidays"
            value={holidayText ?? sla.holidays.join(', ')}
            placeholder="例: 2026-12-29, 2026-12-30"
            onChange={(e) => {
              const v = e.target.value
              setHolidayText(v)
              set((s) => ({ ...s, sla: { ...s.sla, holidays: v.split(/[\s,、]+/).filter(Boolean) } }))
            }}
          />
        </div>
        {slaError && <div className="error small">{slaError}</div>}
      </Section>

      <Section id="staff" title="担当者" desc="「担当者」画面で管理します。">
        <Link to="/ops/staff">担当者の画面を開く →</Link>
      </Section>

      <Section id="weights" title="優先度の重み" desc="大きいほど上に並ぶ">
        <WeightSliders weights={draft.priority_weights} onChange={(w) => set((s) => ({ ...s, priority_weights: w }))} />
      </Section>
      </div>
    </Page>
  )
}
