import { useState } from 'react'
import { Link } from 'react-router'
import { Page, useTitle } from '../../shell'
import { useOps } from '../state'
import { SettingsTabs } from '../tabs'
import { FileImport } from './Connectors'
import { FlowProgress } from './OpsHome'

/** 取り込み: 既存の問い合わせのファイルを一括で受付箱に入れる。 */
export function ImportPage() {
  useTitle('取り込み')
  const { overview } = useOps()
  // 取り込んだ件数（完了済みとして取り込んだときは null）
  const [imported, setImported] = useState<number | null | undefined>(undefined)
  const f = overview?.flow
  return (
    <Page wide crumbs={[{ label: '運用', to: '/ops' }, { label: '設定' }]}>
      <SettingsTabs />
      <div className="panel-head">
        <h1>取り込み</h1>
      </div>
      {imported !== undefined && (
        <section className="panel" data-testid="import-done">
          {imported === null ? (
            <p>完了済みの問い合わせとして取り込みました。</p>
          ) : (
            <>
              <p>{imported} 件を取り込みました。</p>
              {f && <FlowProgress received={f.received} waiting={f.waiting} imported={imported} />}
            </>
          )}
          <Link to="/ops/inbox">受付箱を開く</Link>
        </section>
      )}
      <FileImport onImported={setImported} />
    </Page>
  )
}
