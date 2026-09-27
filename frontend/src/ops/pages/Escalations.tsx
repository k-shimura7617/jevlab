import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { errorMessage } from '../../api'
import { Page, useTitle } from '../../shell'
import { ops, type Item } from '../api'
import { BulkBar, CheckRow, Empty, ItemRow, WeightSliders } from '../components'
import { pct } from '../../format'
import { elapsedLabel, staffName } from '../format'
import { matchesWho, useAssigneeFilter } from '../assigneeFilter'
import { ItemPanel } from '../ItemPanel'
import { useNow, useOps, usePolling } from '../state'
import { sortItems, useWeights } from './Review'
import { FoldClose } from '../../components/fold'

// 対応の目安（受信からの経過）。これを超えたら赤く表示する
/** 対応目安までの残り（営業時間）を短く書く。 */
function slaText(left: number): string {
  if (left < 0) return '超過'
  return left >= 60 ? `残り ${Math.floor(left / 60)} 時間（営業時間）` : `残り ${Math.max(1, Math.floor(left))} 分（営業時間）`
}

function Handling({ item, onDone }: { item: Item; onDone: () => void }) {
  const { meta, settings } = useOps()
  const [note, setNote] = useState('')
  const [category, setCategory] = useState(item.category ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 保存が終わって一覧が更新されるまで、選んだ担当者を表示し続ける。
  // 選んだ時点の担当（base）から一覧側が変わったら（反映された・他で変えられた）、一覧の値に戻す
  const current = item.assignee ?? ''
  const [pending, setPending] = useState<{ value: string; base: string } | null>(null)
  const assignee = pending !== null && pending.base === current ? pending.value : current
  const setAssignee = (value: string) => setPending({ value, base: current })
  const run = (f: () => Promise<unknown>, after?: () => void) => {
    setBusy(true)
    setError(null)
    f()
      .then(() => {
        after?.()
        onDone()
      })
      .catch((e: unknown) => {
        setPending(null)
        setError(errorMessage(e))
      })
      .finally(() => setBusy(false))
  }
  return (
    <section className="handling" data-testid="handling">
      <h3>対応</h3>
      {item.kev_reference && (
        <div className="suggest reference" data-testid="kev-reference">
          <span>
            参考（{settings?.guard.target === 'mock' ? 'MOCK' : 'Kev'}）: 分類 <strong>{meta?.category_labels[item.kev_reference.category ?? ''] ?? '-'}</strong>
            {' / '}担当の推定 <strong>{item.kev_reference.assign_suggestion ? staffName(settings?.staff, item.kev_reference.assign_suggestion) : 'なし'}</strong>
          </span>
        </div>
      )}
      {item.assign_suggestion && (
        <div className="suggest" data-testid="assign-suggestion">
          <span>
            担当の推定: <strong>{staffName(settings?.staff, item.assign_suggestion)}</strong>
            <span className="conf">（確率 {pct(item.assign_confidence ?? 0, 0)}）</span>
          </span>
          {item.assigned_by === 'auto' && item.assignee === item.assign_suggestion ? (
            <span className="muted small">{item.assign_provisional ? '仮で割り当て済み' : '自動で割り当て済み'}</span>
          ) : (
            item.assignee !== item.assign_suggestion && (
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => {
                  const v = item.assign_suggestion ?? ''
                  setAssignee(v)
                  run(() => ops.assign(item.id, v))
                }}
              >
                この担当にする
              </button>
            )
          )}
        </div>
      )}
      <div className="form-grid">
        <label htmlFor="assignee">担当者</label>
        <select
          id="assignee"
          value={assignee}
          disabled={busy}
          onChange={(e) => {
            const v = e.target.value
            setAssignee(v)
            run(() => ops.assign(item.id, v))
          }}
        >
          <option value="">未割り当て</option>
          {(settings?.staff ?? [])
            .filter((s) => s.active || s.id === item.assignee)
            .map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
                {s.role ? `（${s.role}）` : ''}
              </option>
            ))}
        </select>
        <label htmlFor="note">メモ</label>
        <div className="row tight">
          <input id="note" value={note} placeholder="例: 代替品を本日発送。お客様へ電話済み" onChange={(e) => setNote(e.target.value)} />
          <button type="button" className="secondary" disabled={busy || !note.trim()} onClick={() => run(() => ops.note(item.id, note.trim()), () => setNote(''))}>
            追加
          </button>
        </div>
        <label htmlFor="final-category">最終の分類</label>
        <select id="final-category" value={category} onChange={(e) => setCategory(e.target.value)}>
          {item.category === null && <option value="">（未分類）</option>}
          {meta &&
            Object.entries(meta.categories).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
        </select>
      </div>
      <div className="row">
        <button type="button" disabled={busy || !category} onClick={() => run(() => ops.close(item.id, category || null))}>
          対応完了にする
        </button>
        {!category && <span className="muted small">最終の分類を選ぶと完了にできます</span>}
      </div>
      {error && <div className="error small">{error}</div>}
    </section>
  )
}

export function Escalations() {
  useTitle('エスカレーション')
  const { meta, refresh, settings } = useOps()
  const [category, setCategory] = useState<string>('all')
  const [weights, setWeights, weightError] = useWeights()
  const list = usePolling(() => ops.items(['escalated']))
  // Slack のリンクで開いたときは、その件の担当者で絞り、同じ担当のほかの件も続けて片づけられるようにする
  const { id: wanted, who: mine, select, setWho: setMine } = useAssigneeFilter(list.data)
  const all = list.data ?? []
  const staff = settings?.staff ?? []
  const filtered = all.filter(
    (i) =>
      matchesWho(i, mine) &&
      (category === 'all' || i.category === category),
  )
  const items = sortItems(filtered, 'priority', weights)
  // リンク（Slack など）で開いた件が、もうエスカレーション中でない（完了など）ときは、受付箱でその件を開く
  const gone = list.data !== null && wanted !== null && !all.some((i) => i.id === wanted)
  const navigate = useNavigate()
  useEffect(() => {
    if (gone && wanted) navigate(`/ops/inbox?id=${encodeURIComponent(wanted)}`, { replace: true })
  }, [gone, wanted, navigate])
  const current = items.find((i) => i.id === wanted) ?? items[0]
  // 表示した件を URL に固定する（新着で優先度順が変わっても、書きかけのメモが別の件に切り替わらないように）
  useEffect(() => {
    if (!gone && current && current.id !== wanted) select(current.id)
  }, [gone, current, wanted, select])
  const now = useNow()
  // 担当者を選んでいないとき: 選んだ件の集合。選んでいるとき: その人の担当から「外す」件と、新しく「足す」件を別々に持つ
  // （その人の担当の件は、外すと決めない限り選ばれている扱い。後から届いた件も選ばれた状態で並ぶ）
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [added, setAdded] = useState<Set<string>>(new Set())
  const [removed, setRemoved] = useState<Set<string>>(new Set())
  const [target, setTarget] = useState('')
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkMsg, setBulkMsg] = useState<string | null>(null)
  const [bulkError, setBulkError] = useState<string | null>(null)
  useEffect(() => {
    if (!bulkMsg) return
    const timer = setTimeout(() => setBulkMsg(null), 6000)
    return () => clearTimeout(timer)
  }, [bulkMsg])
  const assignedToTarget = (i: Item) => target !== '' && i.assignee === target
  const suggested = (i: Item) => target !== '' && !i.assignee && i.assign_suggestion === target
  const isChecked = (i: Item) => (target ? (assignedToTarget(i) ? !removed.has(i.id) : added.has(i.id)) : checked.has(i.id))
  const edit = (set: (f: (prev: Set<string>) => Set<string>) => void, id: string, on: boolean) =>
    set((prev) => {
      const next = new Set(prev)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })
  const toggle = (i: Item, on: boolean) => {
    if (!target) edit(setChecked, i.id, on)
    else if (assignedToTarget(i)) edit(setRemoved, i.id, !on)
    else edit(setAdded, i.id, on)
  }
  const selectAll = (on: boolean) => {
    if (!target) return setChecked(new Set(on ? items.map((i) => i.id) : []))
    setAdded(new Set(on ? items.filter((i) => !assignedToTarget(i)).map((i) => i.id) : []))
    setRemoved(new Set(on ? [] : items.filter(assignedToTarget).map((i) => i.id)))
  }
  // 担当者を選ぶと、その人の担当の件を上に並べ、推定がその人の未割り当ての件を次に並べる
  const shown = target ? [...items.filter(assignedToTarget), ...items.filter(suggested), ...items.filter((i) => !assignedToTarget(i) && !suggested(i))] : items
  const selected = items.filter(isChecked)
  const toAdd = target ? items.filter((i) => !assignedToTarget(i) && added.has(i.id)) : []
  const toRemove = target ? items.filter((i) => assignedToTarget(i) && removed.has(i.id)) : []
  const chooseTarget = (v: string) => {
    setTarget(v)
    setAdded(new Set())
    setRemoved(new Set())
  }
  const bulkAssign = () => {
    if (!target) return
    const name = staffName(staff, target)
    const add = toAdd.map((i) => i.id)
    const remove = toRemove.map((i) => i.id)
    setBulkBusy(true)
    setBulkError(null)
    const empty = { done: [] as string[], errors: {} as Record<string, string> }
    Promise.all([add.length ? ops.bulkAssign(add, target) : empty, remove.length ? ops.bulkAssign(remove, '') : empty])
      .then(([a, r]) => {
        setBulkMsg(`${name}: 追加 ${a.done.length} 件・外す ${r.done.length} 件`)
        const failed = Object.entries({ ...a.errors, ...r.errors })
        if (failed.length) setBulkError(failed.map(([id, m]) => `${id}: ${m}`).join(' ／ '))
        // 反映された分は一覧の担当から選ばれ方が決まる。失敗した分だけ、操作を残す
        setAdded(new Set(Object.keys(a.errors)))
        setRemoved(new Set(Object.keys(r.errors)))
      })
      .catch((e: unknown) => setBulkError(errorMessage(e)))
      .finally(() => {
        setBulkBusy(false)
        list.reload()
        refresh()
      })
  }
  return (
    <Page wide crumbs={[{ label: '運用', to: '/ops' }, { label: 'エスカレーション' }]}>
      <div className="panel-head">
        <h1>エスカレーション</h1>
        <span className="muted small">
          対応目安 {settings?.sla.hours ?? 9} 営業時間
        </span>
      </div>
      <div className="queue-layout">
        <section className="panel list-pane">
          <div className="row">
            <label className="muted small" htmlFor="filter-assignee">
              担当
            </label>
            <select id="filter-assignee" value={mine} onChange={(e) => setMine(e.target.value)}>
              <option value="all">すべて（{all.length}）</option>
              <option value="none">未割り当て（{all.filter((i) => !i.assignee).length}）</option>
              {staff.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}（{all.filter((i) => i.assignee === s.id).length}）
                </option>
              ))}
            </select>
          </div>
          <div className="cat-filter" role="group" aria-label="分類で絞り込む">
            <button type="button" className="filter-chip" aria-pressed={category === 'all'} onClick={() => setCategory('all')}>
              すべて
            </button>
            {meta &&
              Object.entries(meta.categories).map(([k, label]) => (
                <button key={k} type="button" className="filter-chip" aria-pressed={category === k} onClick={() => setCategory(k)}>
                  {label}
                  <span className="n">{all.filter((i) => i.category === k).length}</span>
                </button>
              ))}
          </div>
          <BulkBar
            count={selected.length}
            total={items.length}
            onAll={() => selectAll(true)}
            onNone={() => selectAll(false)}
          >
            <span className="bulk-group">
              <select aria-label="一括で割り当てる担当者" value={target} onChange={(e) => chooseTarget(e.target.value)}>
                <option value="">担当者を選ぶ</option>
                {staff.filter((s) => s.active).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
              <button type="button" disabled={bulkBusy || !target || toAdd.length + toRemove.length === 0} onClick={bulkAssign}>
                {target ? `更新（追加 ${toAdd.length}・解除 ${toRemove.length}）` : `割り当て（${selected.length} 件）`}
              </button>
            </span>
          </BulkBar>
          {bulkMsg && <div className="done-box small">{bulkMsg}</div>}
          {bulkError && (
            <div className="error small" role="alert">
              {bulkError}
            </div>
          )}
          <details className="weights-box">
            <summary className="small">優先度の重み（並び順）</summary>
            <WeightSliders weights={weights} onChange={setWeights} />
            {weightError && <div className="error small">{weightError}</div>}
            <FoldClose />
          </details>
          {list.error && <div className="error small">{list.error}</div>}
          <div className="item-list" data-testid="escalation-list">
            {shown.map((i) => {
              const left = i.sla_left_min ?? 0
              const sla = left < 0 ? 'late' : left <= 60 ? 'soon' : 'ok'
              return (
                <CheckRow key={i.id} checked={isChecked(i)} onCheck={(v) => toggle(i, v)} label={`${i.id}「${i.subject || i.body.slice(0, 20)}」を一括の対象にする`}>
                  <button type="button" className="row-button" aria-current={i.id === current?.id ? 'true' : undefined} onClick={() => select(i.id)}>
                    <ItemRow
                      item={i}
                      meta={meta}
                      weights={weights}
                      active={i.id === current?.id}
                      extra={
                        <>
                          <span className={`sla sla-${sla}`} title={`受信から ${elapsedLabel(i.received_at, now)}`}>
                            ⏱ {slaText(left)}
                          </span>
                          <span className="assignee">
                            {i.assignee ? (
                              staffName(staff, i.assignee)
                            ) : i.assign_suggestion ? (
                              <>
                                <span className={suggested(i) ? 'suggest-mark on' : 'suggest-mark'}>推定</span> {staffName(staff, i.assign_suggestion)}
                              </>
                            ) : (
                              '未割り当て'
                            )}
                            {i.assigned_by === 'auto' && <span className="assign-by">{i.assign_provisional ? '仮' : '自動'}</span>}
                          </span>
                        </>
                      }
                    />
                  </button>
                </CheckRow>
              )
            })}
            {list.data && items.length === 0 &&
              (all.length > 0 ? (
                <Empty>
                  絞り込みに当てはまる件はありません{' '}
                  <button type="button" className="link-btn" onClick={() => { setMine('all'); setCategory('all') }}>
                    絞り込みを解除
                  </button>
                </Empty>
              ) : (
                <Empty>エスカレーション中の件はありません</Empty>
              ))}
          </div>
        </section>
        <section className="panel detail-pane">
          {!list.data ? (
            <Empty>読み込み中…</Empty>
          ) : current ? (
            <>
              <Handling
                key={current.id}
                item={current}
                onDone={() => {
                  list.reload()
                  refresh()
                }}
              />
              <ItemPanel id={current.id} onChanged={list.reload} here="escalations" />
            </>
          ) : (
            <Empty>エスカレーション中の件はありません</Empty>
          )}
        </section>
      </div>
    </Page>
  )
}
