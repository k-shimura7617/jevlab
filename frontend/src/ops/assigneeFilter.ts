import { useCallback, useEffect } from 'react'
import { useSearchParams } from 'react-router'
import type { Item } from './api'

/** 担当者での絞り込み。all: すべて ／ none: 未割り当て ／ それ以外: 担当者の ID */
export type Who = string

export const matchesWho = (i: Item, who: Who) => who === 'all' || (who === 'none' ? !i.assignee : i.assignee === who)

/**
 * 一覧の「担当者」の絞り込みと、選んだ件を URL に持つ（?id=…&who=…）。
 *
 * Slack のリンクなどで件を開いたとき（who がないとき）は、その件の担当者で絞る。
 * 同じ担当者のほかの件も続けて片づけられるように、絞り込みは URL に残して固定する。
 */
export function useAssigneeFilter(items: Item[] | null) {
  const [params, setParams] = useSearchParams()
  const id = params.get('id')
  const who: Who = params.get('who') ?? 'all'
  const opened = items?.find((i) => i.id === id)
  const needsWho = id !== null && params.get('who') === null && opened !== undefined
  useEffect(() => {
    if (!needsWho || !opened) return
    setParams({ id: opened.id, who: opened.assignee ?? 'none' }, { replace: true })
  }, [needsWho, opened, setParams])
  const select = useCallback(
    (next: string | null) => setParams(next ? { id: next, who } : { who }, { replace: true }),
    [setParams, who],
  )
  const setWho = useCallback(
    (next: Who) => setParams(id ? { id, who: next } : { who: next }, { replace: true }),
    [setParams, id],
  )
  return { id, who, select, setWho }
}
