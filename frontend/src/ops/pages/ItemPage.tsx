import { Navigate, useParams } from 'react-router'

// 件の詳細は受付箱で開く（以前の Slack のリンク /ops/items/{番号} もここから移す）
export function ItemPage() {
  const id = useParams().id ?? ''
  return <Navigate to={`/ops/inbox?id=${encodeURIComponent(id)}`} replace />
}
