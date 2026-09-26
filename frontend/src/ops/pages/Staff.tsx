import { useState } from 'react'
import { errorMessage } from '../../api'
import { pct } from '../../format'
import { Page, useTitle } from '../../shell'
import { ops, type Settings, type StaffMember } from '../api'
import { staffName } from '../format'
import { useOps, usePolling } from '../state'

const newId = () => `staff-${Date.now().toString(36)}`

function StaffRow({
  member,
  assigned,
  onChange,
  onRemove,
}: {
  member: StaffMember
  assigned: number
  onChange: (m: StaffMember) => void
  onRemove: () => void
}) {
  const field = (key: 'name' | 'role', label: string, placeholder: string) => (
    <input
      aria-label={`${member.name || '新しい担当者'}の${label}`}
      maxLength={60}
      value={member[key]}
      placeholder={placeholder}
      onChange={(e) => onChange({ ...member, [key]: e.target.value })}
    />
  )
  return (
    <tr data-testid="staff-row">
      <td>{field('name', '名前', '例: 山本')}</td>
      <td>{field('role', '所属・役割', '例: 配送・在庫')}</td>
      <td className="scope">
        <textarea
          aria-label={`${member.name || '新しい担当者'}の担当範囲`}
          rows={2}
          maxLength={300}
          value={member.scope}
          placeholder="例: 配送の遅れ・誤配送・在庫の確認"
          onChange={(e) => onChange({ ...member, scope: e.target.value })}
        />
      </td>
      <td>
        <input
          aria-label={`${member.name || '新しい担当者'}の Slack ID`}
          className="slack-id"
          maxLength={21}
          value={member.slack_user_id ?? ''}
          placeholder="U0123…"
          onChange={(e) => onChange({ ...member, slack_user_id: e.target.value.trim() })}
        />
      </td>
      <td className="num">{assigned}</td>
      <td>
        <button
          type="button"
          className="link-btn"
          aria-label={`${member.name || '新しい担当者'}を削除`}
          onClick={() => {
            if (assigned === 0 || window.confirm(`${member.name} は ${assigned} 件を対応中です（担当は ID のまま残ります）。削除しますか？`))
              onRemove()
          }}
        >
          削除
        </button>
      </td>
    </tr>
  )
}

export function Staff() {
  useTitle('担当者')
  const { settings, settingsError, saveSettings, overview } = useOps()
  const [edited, setEdited] = useState<Settings | null>(null)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const stats = usePolling(ops.assignment, 5000)
  const escalated = usePolling(() => ops.items(['escalated']), 5000)
  const draft = edited ?? settings
  if (!draft || !settings) return <Page crumbs={[{ label: '運用', to: '/ops' }, { label: '担当者' }]}>{settingsError ?? '読み込み中…'}</Page>
  const set = (f: (s: Settings) => Settings) => {
    setEdited(f(draft))
    setMsg(null)
  }
  const dirty = edited !== null && JSON.stringify(edited.staff) + JSON.stringify(edited.assign) !== JSON.stringify(settings?.staff) + JSON.stringify(settings?.assign)
  const badSlack = draft.staff.some((s) => s.slack_user_id && !/^[UW][A-Z0-9]{6,20}$/.test(s.slack_user_id))
  const invalid = draft.staff.some((s) => !s.name.trim()) || badSlack
  const save = () => {
    if (saving) return
    setSaving(true)
    setError(null)
    // この画面で編集した担当者と割り当ての設定だけを、最新の設定に重ねて保存する（他の画面での変更を上書きしない）
    saveSettings({
      ...settings,
      staff: draft.staff.map((s) => ({ ...s, name: s.name.trim(), role: s.role.trim(), scope: s.scope.trim() })),
      assign: draft.assign,
    })
      .then(() => {
        setEdited(null)
        setMsg('保存しました')
      })
      .catch((e: unknown) => setError(errorMessage(e)))
      .finally(() => setSaving(false))
  }
  const a = draft.assign
  const st = stats.data
  const assignedCount = (id: string) => (escalated.data ?? []).filter((i) => i.assignee === id).length
  return (
    <Page crumbs={[{ label: '運用', to: '/ops' }, { label: '担当者' }]}>
      <div className="panel-head">
        <h1>担当者</h1>
        <div className="row">
          {dirty && <span className="warn-text small">未保存の変更があります</span>}
          <button type="button" className="secondary" disabled={!dirty} onClick={() => setEdited(null)}>
            元に戻す
          </button>
          <button type="button" disabled={!dirty || invalid || saving} onClick={save}>
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
      {msg && <div className="done-box">{msg}</div>}
      {error && <div className="error">{error}</div>}

      <section className="panel">
        <h2>担当者の一覧</h2>
        <p className="muted small">
          担当範囲は重ならないように（Jev が読んで推定）
        </p>
        <div className="scroll">
          <table className="staff-table">
            <thead>
              <tr>
                <th>名前</th>
                <th>所属・役割</th>
                <th>担当範囲（Jev が読む説明）</th>
                <th>Slack ID</th>
                <th className="num">対応中</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {draft.staff.map((m, i) => (
                <StaffRow
                  key={m.id}
                  member={m}
                  assigned={assignedCount(m.id)}
                  onChange={(next) => set((s) => ({ ...s, staff: s.staff.map((x, j) => (j === i ? next : x)) }))}
                  onRemove={() => set((s) => ({ ...s, staff: s.staff.filter((_, j) => j !== i) }))}
                />
              ))}
            </tbody>
          </table>
        </div>
        <div className="row">
          <button type="button" className="secondary" onClick={() => set((s) => ({ ...s, staff: [...s.staff, { id: newId(), name: '', role: '', scope: '' }] }))}>
            ＋ 担当者を追加
          </button>
          {draft.staff.some((s) => !s.name.trim()) && <span className="error small">名前が空の担当者があります</span>}
          {badSlack && <span className="error small">Slack ID は U で始まる英大文字と数字です</span>}
        </div>
      </section>

      <section className="panel">
        <h2>自動の割り当て</h2>
        <label className="small block">
          <input type="checkbox" checked={a.auto} onChange={(e) => set((s) => ({ ...s, assign: { ...s.assign, auto: e.target.checked } }))} /> 確率が高い件は自動で割り当てる
        </label>
        <label className="threshold">
          <span>自動で割り当てる確率</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={a.threshold}
            aria-label="自動で割り当てる確率"
            onChange={(e) => set((s) => ({ ...s, assign: { ...s.assign, threshold: Number(e.target.value) } }))}
          />
          <span className="num">{a.threshold.toFixed(2)}</span>
        </label>
        <label className="small block">
          <input type="checkbox" checked={a.use_examples} onChange={(e) => set((s) => ({ ...s, assign: { ...s.assign, use_examples: e.target.checked } }))} /> 最近完了した件（{a.max_examples} 件まで）を例として渡す
        </label>
      </section>

      <section className="panel" data-testid="assign-stats">
        <h2>推定の当たり具合</h2>
        {stats.error && <div className="error small">{stats.error}</div>}
        {!st ? (
          <p className="muted">集計中…</p>
        ) : st.with_suggestion === 0 ? (
          <p className="muted">まだ完了した件がありません。</p>
        ) : (
          <>
            <div className="kpis">
              <div className="kpi">
                <div className="muted small">推定と実際の担当の一致</div>
                <div className="kpi-value">{pct(st.matched / st.with_suggestion, 0)}</div>
                <div className="kpi-sub">
                  人が担当を決めて完了した {st.with_suggestion} 件のうち {st.matched} 件
                  {st.with_suggestion < 10 && '（件数が少ないため参考値）'}
                </div>
              </div>
              <div className="kpi">
                <div className="muted small">自動で割り当てた件</div>
                <div className="kpi-value">{st.auto_assigned}</div>
                <div className="kpi-sub">うち人が変えた {st.auto_changed} 件</div>
              </div>
            </div>
            {st.pairs.length > 0 && (
              <>
                <h3>取り違えの多い組</h3>
                <p className="muted small">推定 → 実際。担当範囲を見直す手がかりです。</p>
                <ul className="pairs">
                  {st.pairs.map((p) => (
                    <li key={`${p.suggested}-${p.actual}`}>
                      {p.suggested ? staffName(draft.staff, p.suggested) : '推定なし'} → <strong>{staffName(draft.staff, p.actual)}</strong>：{p.count} 件
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
        {overview && <p className="muted small">いまエスカレーション中 {overview.counts.escalated} 件</p>}
      </section>
    </Page>
  )
}
