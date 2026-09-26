import { Link, useParams } from 'react-router'
import { Page, useTitle } from '../../shell'
import { ItemPanel } from '../ItemPanel'

export function ItemPage() {
  const id = useParams().id ?? ''
  useTitle(`${id} - 件の詳細`)
  return (
    <Page crumbs={[{ label: '運用', to: '/ops' }, { label: '受付箱', to: '/ops/inbox' }, { label: id }]}>
      <div className="panel-head">
        <span />
        <Link className="small" to={`/ops/inbox?id=${encodeURIComponent(id)}`}>
          受付箱の一覧で見る
        </Link>
      </div>
      <section className="panel">
        <ItemPanel id={id} />
      </section>
    </Page>
  )
}
