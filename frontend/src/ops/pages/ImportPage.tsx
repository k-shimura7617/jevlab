import { useNavigate } from 'react-router'
import { Page, useTitle } from '../../shell'
import { SettingsTabs } from '../tabs'
import { FileImport } from './Connectors'

/** 取り込み: 既存の問い合わせのファイルを一括で受付箱に入れる。 */
export function ImportPage() {
  useTitle('取り込み')
  const navigate = useNavigate()
  return (
    <Page wide crumbs={[{ label: '運用', to: '/ops' }, { label: '設定' }]}>
      <SettingsTabs />
      <div className="panel-head">
        <h1>取り込み</h1>
      </div>
      {/* 取り込んだ件が処理される様子を、ダッシュボードの処理フローで見る（完了済みとして取り込んだ件は処理しない） */}
      <FileImport onImported={(n) => navigate('/ops#flow', { state: n === null ? null : { imported: n } })} />
    </Page>
  )
}
