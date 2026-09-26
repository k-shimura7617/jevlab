import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { errorMessage } from '../../api'
import { Page, useTitle } from '../../shell'
import { ops, type Item } from '../api'
import { Empty, ItemRow, WeightSliders } from '../components'
import { priorityOf } from '../format'
import { ItemPanel } from '../ItemPanel'
import { useOps, usePolling } from '../state'
import { FoldClose } from '../../components/fold'

type Tab = 'review' | 'audit'
type Order = 'priority' | 'oldest'

/** 優先度の重み。動かすとすぐ並び替え、少し待ってから設定に保存する（他の画面・次回にも効くように）。 */
export function useWeights(): [Record<string, number>, (w: Record<string, number>) => void, string | null] {
  const { settings, saveSettings } = useOps()
  const [local, setLocal] = useState<Record<string, number> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
  }, [])
  const weights = local ?? settings?.priority_weights ?? {}
  const change = (w: Record<string, number>) => {
    setLocal(w)
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => {
      if (!settings) return
      saveSettings({ ...settings, priority_weights: w })
        .then(() => setError(null))
        .catch((e: unknown) => setError(`重みの保存に失敗: ${errorMessage(e)}`))
    }, 600)
  }
  return [weights, change, error]
}

export function sortItems(items: Item[], order: Order, weights: Record<string, number>): Item[] {
  return [...items].sort((a, b) =>
    order === 'oldest' ? a.seq - b.seq : priorityOf(b, weights) - priorityOf(a, weights) || a.seq - b.seq,
  )
}

export function Review() {
  useTitle('分類の確認')
  const { meta, refresh } = useOps()
  const [params, setParams] = useSearchParams()
  const [tab, setTab] = useState<Tab>(params.get('tab') === 'audit' ? 'audit' : 'review')
  const [order, setOrder] = useState<Order>('priority')
  const [weights, setWeights, weightError] = useWeights()
  const list = usePolling(() => ops.items(['review', 'routed']))
  const all = list.data ?? []
  const review = all.filter((i) => i.status === 'review')
  const audit = all.filter((i) => i.status === 'routed' && i.audit && i.audit_result === null)
  const [category, setCategory] = useState<string>('all')
  const pool = tab === 'review' ? review : audit
  const items = sortItems(
    pool.filter((i) => category === 'all' || i.category === category),
    order,
    weights,
  )
  const wanted = params.get('id')
  const current = items.find((i) => i.id === wanted) ?? items[0]
  const select = useCallback((id: string) => setParams({ tab, id }, { replace: true }), [setParams, tab])
  // 表示した件を URL に固定する（新着で優先度順が変わっても、見ている件が入れ替わらないように）
  useEffect(() => {
    if (current && current.id !== wanted) select(current.id)
  }, [current, wanted, select])
  const [keyError, setKeyError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  // 確定の知らせは数秒で消す
  useEffect(() => {
    if (!done) return
    const timer = setTimeout(() => setDone(null), 3000)
    return () => clearTimeout(timer)
  }, [done])
  const sending = useRef(false)

  // 数字キーで分類を確定し、次の件へ進む（デモで素早く捌けるように）
  useEffect(() => {
    if (!current || !meta || current.id !== wanted) return
    const keys = Object.keys(meta.categories)
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat || e.ctrlKey || e.metaKey || e.altKey || sending.current) return
      const t = e.target
      if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement) return
      if (t instanceof HTMLElement && t.isContentEditable) return
      const idx = items.findIndex((i) => i.id === current.id)
      // j / k で次・前の件へ
      if (e.key === 'j' || e.key === 'k') {
        const to = items[idx + (e.key === 'j' ? 1 : -1)]
        if (to) select(to.id)
        return
      }
      const n = Number(e.key)
      const category = Number.isInteger(n) ? keys[n - 1] : undefined
      if (!category) return
      e.preventDefault()
      sending.current = true
      setKeyError(null)
      const next = items[idx + 1]
      ops
        .decide(current.id, category)
        .then(() => {
          setDone(`${current.id} を「${meta.categories[category] ?? category}」で確定しました`)
          if (next) select(next.id)
          list.reload()
          refresh()
        })
        .catch((err: unknown) => setKeyError(`${current.id} を確定できませんでした: ${errorMessage(err)}`))
        .finally(() => {
          sending.current = false
        })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [current, wanted, meta, items, list, refresh, select])

  return (
    <Page wide crumbs={[{ label: '運用', to: '/ops' }, { label: '分類の確認' }]}>
      <div className="panel-head">
        <h1>分類の確認</h1>
        <span className="muted small">
          数字キーで確定、j / k で移動
          {meta && `（${Object.values(meta.categories).map((l, i) => `${i + 1}: ${l}`).join(' ／ ')}）`}
        </span>
      </div>
      <div className="queue-layout">
        <section className="panel list-pane">
          <div className="tabs" role="tablist">
            <button type="button" role="tab" className="tab" aria-selected={tab === 'review'} onClick={() => setTab('review')}>
              確認待ち <span className="n">{review.length}</span>
            </button>
            <button type="button" role="tab" className="tab" aria-selected={tab === 'audit'} onClick={() => setTab('audit')}>
              抜き取り確認 <span className="n">{audit.length}</span>
            </button>
          </div>
          <div className="row">
            <span className="muted small">並び順</span>
            <div className="seg" role="group" aria-label="並び順">
              <button type="button" className="seg-btn" aria-pressed={order === 'priority'} onClick={() => setOrder('priority')}>
                優先度
              </button>
              <button type="button" className="seg-btn" aria-pressed={order === 'oldest'} onClick={() => setOrder('oldest')}>
                受信順
              </button>
            </div>
          </div>
          <div className="cat-filter" role="group" aria-label="分類で絞り込む">
            <button type="button" className="filter-chip" aria-pressed={category === 'all'} onClick={() => setCategory('all')}>
              すべて
            </button>
            {meta &&
              Object.entries(meta.categories).map(([k, label]) => (
                <button key={k} type="button" className="filter-chip" aria-pressed={category === k} onClick={() => setCategory(k)}>
                  {label}
                  <span className="n">{pool.filter((i) => i.category === k).length}</span>
                </button>
              ))}
          </div>
          {order === 'priority' && (
            <details className="weights-box">
              <summary className="small">優先度の重み（並び順）</summary>
              <WeightSliders weights={weights} onChange={setWeights} />
              {weightError && <div className="error small">{weightError}</div>}
              <FoldClose />
            </details>
          )}
          {tab === 'audit' && (
            <p className="note">自動で振り分けた件の一部を、人が確かめます。</p>
          )}
          {list.error && <div className="error small">{list.error}</div>}
          <div className="item-list" data-testid="review-list">
            {items.map((i) => (
              <button key={i.id} type="button" className="row-button" aria-current={i.id === current?.id ? 'true' : undefined} aria-label={`${i.id} ${i.subject || i.body.slice(0, 30)}`} onClick={() => select(i.id)}>
                <ItemRow item={i} meta={meta} weights={weights} active={i.id === current?.id} />
              </button>
            ))}
            {list.data &&
              items.length === 0 &&
              (pool.length > 0 ? (
                <Empty>
                  この分類の件はありません{' '}
                  <button type="button" className="link-btn" onClick={() => setCategory('all')}>
                    絞り込みを解除
                  </button>
                </Empty>
              ) : (
                <Empty>{tab === 'review' ? '確認待ちの件はありません' : '抜き取り確認の待ちはありません'}</Empty>
              ))}
          </div>
        </section>
        <section className="panel detail-pane">
          {keyError && (
            <div className="error small" role="alert">
              {keyError}
            </div>
          )}
          <div className="toast-area" role="status" aria-live="polite">
            {done && <div className="toast">✓ {done}</div>}
          </div>
          {!list.data ? (
            <Empty>読み込み中…</Empty>
          ) : current ? (
            <ItemPanel id={current.id} onChanged={list.reload} />
          ) : (
            <Empty>{tab === 'review' ? '確認待ちの件はありません' : '抜き取り確認の待ちはありません'}</Empty>
          )}
        </section>
      </div>
    </Page>
  )
}
