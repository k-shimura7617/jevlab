import { useState } from 'react'
import { errorMessage } from '../../api'
import { pct } from '../../format'
import { Page, useTitle } from '../../shell'
import { ops, type ScopeDraft, type Settings, type StaffMember } from '../api'
import { useAutoSave } from '../autosave'
import { SaveState } from '../components'
import { staffName } from '../format'
import { useOps, usePolling } from '../state'

const newId = () => `staff-${Date.now().toString(36)}`

function StaffRow({
  member,
  assigned,
  handled,
  need,
  drafting,
  onChange,
  onRemove,
  onDraft,
}: {
  member: StaffMember
  assigned: number
  // 担当範囲の案の材料になる、人が割り当てて完了した件の数と、案を作れる件数
  handled: number
  need: number
  drafting: boolean
  onChange: (m: StaffMember) => void
  onRemove: () => void
  onDraft: () => void
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
    <tr data-testid="staff-row" className={member.active ? undefined : 'muted'}>
      <td>
        <input
          type="checkbox"
          aria-label={`${member.name || '新しい担当者'}の担当オン`}
          checked={member.active}
          onChange={(e) => onChange({ ...member, active: e.target.checked })}
        />
      </td>
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
        <button
          type="button"
          className="link-btn small"
          disabled={handled < need || drafting}
          title={handled < need ? `完了 ${need} 件から作れます` : undefined}
          onClick={onDraft}
        >
          {drafting ? '案を作成中…' : `完了 ${handled}${handled < need ? `/${need}` : ''} 件から案を作る`}
        </button>
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
            // 削除はすぐ保存されるので、いつも確かめる
            const doing = assigned > 0 ? `（${assigned} 件を対応中。担当は ID のまま残ります）` : ''
            if (window.confirm(`${member.name || '新しい担当者'}を削除しますか？${doing}`)) onRemove()
          }}
        >
          削除
        </button>
      </td>
    </tr>
  )
}

const badSlackId = (s: StaffMember) => Boolean(s.slack_user_id) && !/^[UW][A-Z0-9]{6,20}$/.test(s.slack_user_id ?? '')
const staffChanged = (d: Settings, s: Settings) =>
  JSON.stringify(d.staff) + JSON.stringify(d.assign) !== JSON.stringify(s.staff) + JSON.stringify(s.assign)
// この画面で編集した担当者と割り当ての設定だけを、最新の設定に重ねる（他の画面での変更を上書きしない）
const mergeStaff = (latest: Settings, d: Settings): Settings => ({
  ...latest,
  staff: d.staff.map((s) => ({ ...s, name: s.name.trim(), role: s.role.trim(), scope: s.scope.trim() })),
  assign: d.assign,
})
const staffInvalid = (d: Settings): string | null =>
  d.staff.some((s) => !s.name.trim()) ? '名前が空の担当者があります' : d.staff.some(badSlackId) ? 'Slack ID の形が違います' : null

export function Staff() {
  useTitle('担当者')
  const { settings, settingsError, overview } = useOps()
  const stats = usePolling(ops.assignment, 5000)
  const escalated = usePolling(() => ops.items(['escalated']), 5000)
  const auto = useAutoSave(staffChanged, mergeStaff, staffInvalid)
  const { draft, set } = auto
  // 担当範囲の案（Claude）。採用すると一覧の担当範囲に入り、そのまま保存する
  const [proposal, setProposal] = useState<ScopeDraft | null>(null)
  const [drafting, setDrafting] = useState<string | null>(null)
  const [draftError, setDraftError] = useState<string | null>(null)
  if (!draft || !settings) return <Page crumbs={[{ label: '運用', to: '/ops' }, { label: '担当者' }]}>{settingsError ?? '読み込み中…'}</Page>
  const badSlack = draft.staff.some(badSlackId)
  const a = draft.assign
  const st = stats.data
  const assignedCount = (id: string) => (escalated.data ?? []).filter((i) => i.assignee === id).length
  const makeDraft = (staffId: string) => {
    if (drafting) return
    setDrafting(staffId)
    setDraftError(null)
    ops
      .scopeDraft(staffId)
      .then(setProposal)
      .catch((e: unknown) => setDraftError(errorMessage(e)))
      .finally(() => setDrafting(null))
  }
  const adopt = (p: ScopeDraft) => {
    set((s) => ({ ...s, staff: s.staff.map((x) => (x.id === p.staff_id ? { ...x, scope: p.scope } : x)) }))
    void auto.commit()
    setProposal(null)
  }
  return (
    <Page crumbs={[{ label: '運用', to: '/ops' }, { label: '担当者' }]}>
      <div {...auto.handlers}>
      <div className="panel-head">
        <h1>担当者</h1>
        <SaveState status={auto.status} error={auto.error} />
      </div>

      <section className="panel">
        <h2>担当者の一覧</h2>
        <p className="muted small">
          担当範囲は重ならないように（Jev が読んで推定）
        </p>
        <div className="scroll">
          <table className="staff-table">
            <thead>
              <tr>
                <th>担当</th>
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
                  handled={st?.handled_by_staff[m.id] ?? 0}
                  need={a.scope_draft_min}
                  drafting={drafting === m.id}
                  onChange={(next) => set((s) => ({ ...s, staff: s.staff.map((x, j) => (j === i ? next : x)) }))}
                  onRemove={() => set((s) => ({ ...s, staff: s.staff.filter((_, j) => j !== i) }))}
                  onDraft={() => makeDraft(m.id)}
                />
              ))}
            </tbody>
          </table>
        </div>
        {draftError && <div className="error small">{draftError}</div>}
        {proposal && (
          <div className="scope-proposal" data-testid="scope-proposal">
            <h3>
              {staffName(draft.staff, proposal.staff_id)}の担当範囲の案<span className="muted small">（完了 {proposal.based_on} 件から・{proposal.model}）</span>
            </h3>
            <textarea
              aria-label="担当範囲の案"
              rows={2}
              maxLength={300}
              value={proposal.scope}
              onChange={(e) => setProposal({ ...proposal, scope: e.target.value })}
            />
            {proposal.notes.length > 0 && (
              <ul className="small muted">
                {proposal.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            )}
            <div className="row">
              <button type="button" disabled={!proposal.scope.trim()} onClick={() => adopt(proposal)}>
                この案を使う
              </button>
              <button type="button" className="secondary" onClick={() => setProposal(null)}>
                閉じる
              </button>
            </div>
          </div>
        )}
        <div className="row">
          <button type="button" className="secondary" onClick={() => set((s) => ({ ...s, staff: [...s.staff, { id: newId(), name: '', role: '', scope: '', active: true }] }))}>
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
      </div>
    </Page>
  )
}
