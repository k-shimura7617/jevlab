import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { errorMessage } from '../../api'
import { Page, useTitle } from '../../shell'
import { QueueTabs } from '../tabs'
import { ops } from '../api'
import { Empty, ItemRow } from '../components'
import { ItemPanel } from '../ItemPanel'
import { useOps, usePolling } from '../state'

export function Review() {
  useTitle('分類の確認')
  const { meta, refresh } = useOps()
  const [params, setParams] = useSearchParams()
  const list = usePolling(() => ops.items(['review']))
  const pool = list.data ?? []
  const [category, setCategory] = useState<string>('all')
  // 届いた順（古い順）に並べる
  const items = pool.filter((i) => category === 'all' || i.category === category).sort((a, b) => a.seq - b.seq)
  const wanted = params.get('id')
  const current = items.find((i) => i.id === wanted) ?? items[0]
  const select = useCallback((id: string) => setParams({ id }, { replace: true }), [setParams])
  // 表示した件を URL に固定する（新着が届いても、見ている件が入れ替わらないように）
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
    <Page wide crumbs={[{ label: '運用', to: '/ops' }, { label: '対応待ち' }, { label: '分類の確認' }]}>
      <QueueTabs />
      <div className="panel-head">
        <h1>分類の確認</h1>
        <span className="muted small">
          数字キーで確定、j / k で移動
          {meta && `（${Object.values(meta.categories).map((l, i) => `${i + 1}: ${l}`).join(' ／ ')}）`}
        </span>
      </div>
      <div className="queue-layout">
        <section className="panel list-pane">
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
          {list.error && <div className="error small">{list.error}</div>}
          <div className="item-list" data-testid="review-list">
            {items.map((i) => (
              <button key={i.id} type="button" className="row-button" aria-current={i.id === current?.id ? 'true' : undefined} aria-label={`${i.id} ${i.subject || i.body.slice(0, 30)}`} onClick={() => select(i.id)}>
                <ItemRow item={i} meta={meta} active={i.id === current?.id} />
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
                <Empty>確認待ちの件はありません</Empty>
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
            <Empty>確認待ちの件はありません</Empty>
          )}
        </section>
      </div>
    </Page>
  )
}
