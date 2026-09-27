import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { errorMessage } from '../../api'
import { Page, useTitle } from '../../shell'
import { ops, type Channel, type ImportRow, type ParsedFile, type Settings, type SlackSettings, type SlackState, type SlackStatus } from '../api'
import { guessCategory, guessMapping, MAPPED_FIELDS, SAMPLE_CSV, toIsoDate, type MappedField } from '../csv'
import { useAutoSave } from '../autosave'
import { SaveState } from '../components'
import { useOps, usePolling } from '../state'
import { AdminTabs, SettingsTabs } from '../tabs'

const CONNECTORS: { id: Channel; name: string; icon: string; real: string; desc: string }[] = [
  {
    id: 'mail',
    name: 'メール（Gmail）',
    icon: '✉',
    real: '本番: Gmail API の push 通知（Pub/Sub）で受信',
    desc: 'support@komorebi.example に届いたメールを受付箱に取り込む',
  },
  {
    id: 'chat',
    name: 'チャット（疑似 Slack）',
    icon: '#',
    real: '実際の Slack は下の「Slack（実際のワークスペース）」',
    desc: '画面上の #お問い合わせ窓口 の書き込みを取り込む',
  },
  { id: 'csv', name: 'CSV 取り込み', icon: '⇪', real: '既存システムの書き出しや過去分の一括登録に使う', desc: '画面からファイルを選んで取り込む' },
  { id: 'api', name: 'API', icon: '⚙', real: '問い合わせフォーム・基幹システムから直接登録', desc: 'POST /api/ops/ingest に JSON を送る' },
]

function ConnectorCards() {
  const { settings, saveSettings } = useOps()
  const [error, setError] = useState<string | null>(null)
  if (!settings) return null
  const toggle = (id: Channel) =>
    saveSettings({ ...settings, connectors: { ...settings.connectors, [id]: !(settings.connectors[id] ?? false) } }).catch((e: unknown) =>
      setError(errorMessage(e)),
    )
  return (
    <section className="connector-grid" data-testid="connectors">
      {CONNECTORS.map((c) => {
        const on = settings.connectors[c.id] ?? false
        return (
          <div key={c.id} className={`connector${on ? ' on' : ''}`}>
            <div className="connector-head">
              <span className="connector-icon" aria-hidden>
                {c.icon}
              </span>
              <strong>{c.name}</strong>
              <span className={`conn-state${on ? ' on' : ''}`}>{on ? '接続済み' : '切断'}</span>
            </div>
            <p className="small">{c.desc}</p>
            <p className="muted small">{c.real}</p>
            <button type="button" className={on ? 'secondary' : undefined} onClick={() => toggle(c.id)}>
              {on ? '切断する' : '接続する'}
            </button>
          </div>
        )
      })}
      {error && <div className="error small">{error}</div>}
    </section>
  )
}

const SLACK_STATE: Record<SlackState, string> = {
  unconfigured: '未設定',
  off: '停止',
  connecting: '接続中…',
  on: '動作中',
  error: 'エラー',
}

const SLACK_ID = /^[CG][A-Z0-9]{6,20}$/

const splitIds = (v: string) => v.split(/[\s,、]+/).filter(Boolean)
const badSlackIds = (d: Settings) => [...Object.values(d.slack.channel_map).filter(Boolean), ...d.slack.inbound_channels].filter((id) => !SLACK_ID.test(id))
const slackChanged = (d: Settings, s: Settings) =>
  JSON.stringify(d.slack) !== JSON.stringify(s.slack) || d.connectors.slack !== s.connectors.slack
const mergeSlack = (latest: Settings, d: Settings): Settings => ({
  ...latest,
  slack: d.slack,
  connectors: { ...latest.connectors, slack: d.connectors.slack },
})
const slackInvalid = (d: Settings): string | null => {
  const bad = badSlackIds(d)
  return bad.length ? `チャンネル ID の形式が違います: ${bad.join(', ')}` : null
}

/** 投稿先のチャンネルから jevlab の投稿を消す（デモで流しすぎたとき用）。 */
function SlackPurge({ purge, onStarted }: { purge: SlackStatus['purge']; onStarted: () => void }) {
  const [error, setError] = useState<string | null>(null)
  // この画面で始めた削除が終わったら、ポップアップで知らせる（重大な操作なので見落とさないように）。
  // 始める前の終了時刻を控え、それと違う終了時刻が来たら「今回の削除が終わった」とみなす
  const watching = useRef<{ before: string | null } | null>(null)
  useEffect(() => {
    const w = watching.current
    if (!w || purge.running || !purge.finished_at || purge.finished_at === w.before) return
    watching.current = null
    window.alert(
      purge.error
        ? `Slack の投稿の削除が途中で止まりました（${purge.deleted} 件を削除）。\n${purge.error}`
        : `Slack の投稿の削除が終わりました。\n${purge.deleted} 件を削除${purge.skipped > 0 ? `、消せない ${purge.skipped} 件は残しました` : ''}。`,
    )
  }, [purge])
  const start = () => {
    if (!window.confirm('Slack の投稿先チャンネルから、jevlab の投稿（スレッドの返信を含む）をすべて削除します。本当に削除しますか？')) return
    setError(null)
    watching.current = { before: purge.finished_at }
    ops
      .slackPurge()
      .then(onStarted)
      .catch((e: unknown) => {
        watching.current = null
        setError(errorMessage(e))
      })
  }
  return (
    <div className="row">
      <button type="button" className="danger" disabled={purge.running} onClick={start}>
        Slack の投稿を全削除
      </button>
      {purge.running && <span className="small">削除中 {purge.deleted} / {purge.total || '…'}</span>}
      {!purge.running && purge.finished_at && !purge.error && <span className="muted small">{purge.deleted} 件を削除しました{purge.skipped > 0 && `（消せない ${purge.skipped} 件は残しました）`}</span>}
      {purge.error && <span className="error small">{purge.error}</span>}
      {error && <span className="error small">{error}</span>}
    </div>
  )
}

function SlackPanel() {
  const status = usePolling(ops.slack, 3000)
  const auto = useAutoSave(slackChanged, mergeSlack, slackInvalid)
  // 受信するチャンネル ID の欄は入力中の文字列をそのまま持つ（区切りを打った途端に消えないように）
  const [inboundText, setInboundText] = useState<string | null>(null)
  const d = auto.draft
  if (!d) return null
  const st = status.data
  const draft = d.slack
  const setEdit = (next: SlackSettings) => auto.set((s) => ({ ...s, slack: next }))
  const setReceivingDraft = (on: boolean) => auto.set((s) => ({ ...s, connectors: { ...s.connectors, slack: on } }))
  const inbound = inboundText ?? draft.inbound_channels.join(', ')
  const receiving = d.connectors.slack ?? false
  return (
    <section className="panel" data-testid="slack" {...auto.handlers}>
      {status.error && <div className="error small">{status.error}</div>}
      {st && (
        <div className="slack-status">
          <span>
            送信: <strong className={`slack-${st.outbound.state}`}>{SLACK_STATE[st.outbound.state]}</strong>（{st.outbound.count} 件）
          </span>
          <span>
            受信: <strong className={`slack-${st.inbound.state}`}>{SLACK_STATE[st.inbound.state]}</strong>（{st.inbound.count} 件）
          </span>
          {st.bot_user && <span className="muted small">ボット {st.bot_user}</span>}
        </div>
      )}
      {st && (!st.bot_token || !st.app_token) && (
        <p className="warn-box small">
          トークンが未設定です。
          <br />
          手順は docs/slack-setup.md にあります。
        </p>
      )}
      {st?.outbound.last_error && <div className="error small">送信: {st.outbound.last_error}</div>}
      {st?.inbound.last_error && <div className="error small">受信: {st.inbound.last_error}</div>}
      <div className="form-grid">
        <label htmlFor="slack-out">送信</label>
        <label className="small">
          <input
            id="slack-out"
            type="checkbox"
            checked={draft.outbound}
            onChange={(e) => setEdit({ ...draft, outbound: e.target.checked })}
          />{' '}
          振り分け・エスカレーションの投稿を Slack にも流す
        </label>
        <label htmlFor="slack-in">受信</label>
        <label className="small">
          <input
            id="slack-in"
            type="checkbox"
            checked={receiving}
            onChange={(e) => setReceivingDraft(e.target.checked)}
          />{' '}
          下のチャンネルの書き込みを受付箱に取り込む
        </label>
        <label htmlFor="slack-inbound">受信するチャンネル ID</label>
        <input
          id="slack-inbound"
          value={inbound}
          placeholder="例: C0123456789"
          onChange={(e) => {
            setInboundText(e.target.value)
            setEdit({ ...draft, inbound_channels: splitIds(e.target.value) })
          }}
        />
      </div>
      <h3>呼び出し</h3>
      <div className="form-grid">
        <label htmlFor="slack-dispatcher">振り分け担当（当番）</label>
        <select
          id="slack-dispatcher"
          value={draft.dispatcher ?? ''}
          onChange={(e) => setEdit({ ...draft, dispatcher: e.target.value || null })}
        >
          <option value="">未設定</option>
          {d.staff
            .filter((m) => m.active || m.id === draft.dispatcher)
            .map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
                {m.active ? '' : '（担当オフ）'}
                {m.slack_user_id ? '' : '（Slack ID なし）'}
              </option>
            ))}
        </select>
        <label htmlFor="slack-url">画面へのリンク</label>
        <input id="slack-url" value={draft.app_url} onChange={(e) => setEdit({ ...draft, app_url: e.target.value.trim() })} />
      </div>
      <p className="muted small">スレッドで担当者（未定なら振り分け担当）を呼ぶ</p>
      <h3>投稿先のチャンネル ID</h3>
      <div className="form-grid">
        {(st?.mirrorable ?? Object.keys(draft.channel_map)).map((ch) => (
          <div key={ch} className="contents">
            <label htmlFor={`slack-map-${ch}`}>{ch}</label>
            <input
              id={`slack-map-${ch}`}
              value={draft.channel_map[ch] ?? ''}
              placeholder="空欄なら流さない"
              onChange={(e) => {
                const v = e.target.value.trim()
                const { [ch]: _removed, ...rest } = draft.channel_map
                setEdit({ ...draft, channel_map: v ? { ...rest, [ch]: v } : rest })
              }}
            />
          </div>
        ))}
      </div>
      <p className="muted small">お問い合わせ窓口（元の本文）は流しません。</p>
      <SaveState status={auto.status} error={auto.error} />
    </section>
  )
}

/** 管理（開発側）: Slack に流した投稿の全削除。 */
function SlackPurgePanel() {
  const status = usePolling(ops.slack, 3000)
  const st = status.data
  if (!st?.bot_token) return null
  return (
    <section className="panel slack-purge">
      <h2>Slack の投稿の削除</h2>
      <SlackPurge purge={st.purge} onStarted={status.reload} />
    </section>
  )
}

/** 設定: Slack（利用者が触る）。 */
export function SlackSettings() {
  useTitle('Slack')
  return (
    <Page crumbs={[{ label: '運用', to: '/ops' }, { label: '設定' }]}>
      <SettingsTabs />
      <div className="panel-head">
        <h1>Slack</h1>
      </div>
      <SlackPanel />
    </Page>
  )
}

const MAX_FILE_MB = 20
const CHUNK = 500
const SOURCE_LABELS: Record<ParsedFile['channel'], string> = { csv: '表（CSV・Excel）', mail: 'メール', slack: 'Slack' }

/** ファイルを base64 で読む（サーバで形式を見分けて読む）。 */
const readBase64 = (f: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).replace(/^data:[^,]*,/, ''))
    r.onerror = () => reject(new Error(`${f.name} を読めませんでした`))
    r.readAsDataURL(f)
  })

/** 既存の問い合わせのファイル（CSV・Excel・メール・Slack のエクスポート）を取り込む。 */
function FileImport() {
  const { settings, meta, refresh } = useOps()
  const navigate = useNavigate()
  const [fileName, setFileName] = useState('')
  const [parsed, setParsed] = useState<ParsedFile | null>(null)
  const [hasHeader, setHasHeader] = useState(true)
  const [mapping, setMapping] = useState<Record<MappedField, number> | null>(null)
  // 過去の分類の値 → jevlab の分類（空文字は使わない）
  const [valueMap, setValueMap] = useState<Record<string, string>>({})
  const [backfill, setBackfill] = useState(false)
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const labels = meta?.categories ?? {}

  const load = (f: File) => {
    setError(null)
    setParsed(null)
    if (f.size > MAX_FILE_MB * 1024 * 1024) {
      setError(`${MAX_FILE_MB}MB を超えるファイルは読めません（期間やラベルで分けて書き出してください）`)
      return
    }
    setBusy(true)
    readBase64(f)
      .then((data) => ops.parseImport(f.name, data))
      .then((p) => {
        if (p.kind === 'table' && !p.table.length) throw new Error('行がありません')
        setFileName(f.name)
        setParsed(p)
        const m = p.kind === 'table' ? guessMapping(p.table[0] ?? []) : null
        setMapping(m)
        // 完了済みとして取り込むかは、利用者が選ぶ（既定はオフ）
        setBackfill(false)
        setValueMap({})
      })
      .catch((e: unknown) => setError(errorMessage(e)))
      .finally(() => setBusy(false))
  }

  const table = parsed?.kind === 'table' ? parsed.table : []
  const header = table[0] ?? []
  const data = hasHeader ? table.slice(1) : table
  const columns = Array.from({ length: Math.max(0, ...table.map((r) => r.length)) }, (_, i) =>
    hasHeader ? header[i] || `列${i + 1}` : `列${i + 1}`,
  )
  const cell = (r: string[], f: MappedField) => (mapping && mapping[f] >= 0 ? (r[mapping[f]] ?? '') : '')
  const categoryValues = mapping && mapping.category >= 0 ? [...new Set(data.map((r) => cell(r, 'category').trim()).filter(Boolean))] : []
  const categoryOf = (v: string) => valueMap[v.trim()] ?? guessCategory(v, labels)
  const rows: (ImportRow & { source?: string })[] =
    parsed?.kind === 'messages'
      ? parsed.messages
      : data.map((r) => ({
          from_name: cell(r, 'from_name').trim(),
          from_address: cell(r, 'from_address').trim(),
          subject: cell(r, 'subject').trim(),
          body: cell(r, 'body'),
          category: categoryOf(cell(r, 'category')) || null,
          received_at: toIsoDate(cell(r, 'received_at')),
        }))
  const valid = rows.filter((r) => r.body.trim())
  const labeled = valid.filter((r) => r.category).length
  const enabled = settings?.connectors.csv ?? false

  const submit = async () => {
    if (!parsed) return
    const how = backfill ? '完了済みの問い合わせとして（Jev で判定・課金あり）' : ''
    if (!window.confirm(`${valid.length} 件を${how}取り込みますか？`)) return
    setBusy(true)
    setError(null)
    const ids: string[] = []
    try {
      for (let i = 0; i < valid.length; i += CHUNK) {
        setProgress(`${i} / ${valid.length} 件`)
        const res = await ops.importRows(fileName, parsed.channel, backfill, valid.slice(i, i + CHUNK))
        ids.push(...res.ids)
      }
      setParsed(null)
      refresh()
      // 取り込んだ件が処理される様子を、ダッシュボードの処理フローで見る
      navigate('/ops#flow', { state: backfill ? null : { imported: ids.length } })
    } catch (e: unknown) {
      setError(`${ids.length} 件まで取り込んだところで失敗しました: ${errorMessage(e)}`)
    } finally {
      setBusy(false)
      setProgress(null)
    }
  }

  return (
    <section className="panel" data-testid="csv-import" id="csv">
      <div className="panel-head">
        <h2>ファイル取り込み</h2>
        <a className="small" href={`data:text/csv;charset=utf-8,${encodeURIComponent(`\ufeff${SAMPLE_CSV}`)}`} download="sample_inquiries.csv">
          サンプル CSV
        </a>
      </div>
      {!enabled && <div className="warn-box">CSV 取り込みのコネクタが切断されています。</div>}
      <label
        className="dropzone"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault()
          const f = e.dataTransfer.files[0]
          if (f) load(f)
        }}
      >
        <input
          type="file"
          accept=".csv,.txt,.xlsx,.eml,.mbox,.zip"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) load(f)
            e.target.value = ''
          }}
        />
        <span>CSV（UTF-8・Shift_JIS）・Excel（.xlsx）・メール（.eml・.mbox）・Slack のエクスポート（.zip）</span>
      </label>
      {busy && !parsed && <p className="muted small">読み込み中…</p>}
      {parsed && (
        <>
          <div className="row">
            <strong>{fileName}</strong>
            <span className="muted small">
              {SOURCE_LABELS[parsed.channel]} ／ {rows.length} 件{parsed.skipped > 0 && `（読めなかった ${parsed.skipped} 件を除く）`}
            </span>
            {parsed.kind === 'table' && (
              <label className="small">
                <input type="checkbox" checked={hasHeader} onChange={(e) => setHasHeader(e.target.checked)} /> 1 行目は見出し
              </label>
            )}
          </div>
          {parsed.kind === 'table' && mapping && (
            <>
              <h3>列の対応</h3>
              <div className="mapping">
                {MAPPED_FIELDS.map((f) => (
                  <label key={f.id}>
                    <span>{f.label}</span>
                    <select
                      value={mapping[f.id]}
                      onChange={(e) => {
                        const next = { ...mapping, [f.id]: Number(e.target.value) }
                        setMapping(next)
                      }}
                    >
                      <option value={-1}>（使わない）</option>
                      {columns.map((c, i) => (
                        <option key={i} value={i}>
                          {c}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
              {categoryValues.length > 0 && (
                <>
                  <h3>過去の分類の対応</h3>
                  <div className="mapping" data-testid="category-values">
                    {categoryValues.slice(0, 30).map((v) => (
                      <label key={v}>
                        <span>{v}</span>
                        <select value={categoryOf(v)} onChange={(e) => setValueMap({ ...valueMap, [v]: e.target.value })}>
                          <option value="">（使わない）</option>
                          {Object.entries(labels).map(([k, l]) => (
                            <option key={k} value={k}>
                              {l}
                            </option>
                          ))}
                        </select>
                      </label>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
          <h3>先頭 5 件</h3>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>受信日時</th>
                  <th>差出人</th>
                  <th>件名</th>
                  <th>本文</th>
                  {parsed.kind === 'table' && <th>分類</th>}
                  {parsed.channel === 'slack' && <th>チャンネル</th>}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 5).map((r, i) => (
                  <tr key={i} className={r.body.trim() ? undefined : 'off'}>
                    <td>{r.received_at ? new Date(r.received_at).toLocaleString('ja-JP') : '-'}</td>
                    <td>{r.from_name || r.from_address || '-'}</td>
                    <td>{r.subject || '-'}</td>
                    <td className="body">{r.body || '（本文なし・取り込まない）'}</td>
                    {parsed.kind === 'table' && <td>{r.category ? labels[r.category] : '-'}</td>}
                    {parsed.channel === 'slack' && <td>{r.source}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <label className="small block">
            <input type="checkbox" checked={backfill} onChange={(e) => setBackfill(e.target.checked)} /> 完了済みの問い合わせとして取り込む
          </label>
          <p className="muted small">
            {backfill ? (
              <>
                過去分と Jev の判定を比べて閾値を決める（課金あり）。Slack・人には回さない。
                <br />
                分類あり {labeled} 件
              </>
            ) : (
              '通常の流れ（個人情報の確認・振り分け・Slack）に乗せる'
            )}
          </p>
          <div className="row">
            <button type="button" disabled={busy || !enabled || !valid.length} onClick={() => void submit()}>
              {busy && progress ? `取り込み中（${progress}）` : `${valid.length} 件を取り込む`}
            </button>
            {rows.length !== valid.length && <span className="muted small">本文が空の {rows.length - valid.length} 件は取り込みません</span>}
          </div>
        </>
      )}
      {error && <div className="error">{error}</div>}
    </section>
  )
}

export function Connectors() {
  useTitle('コネクタ')
  const origin = window.location.origin
  return (
    <Page wide crumbs={[{ label: '管理', to: '/admin' }, { label: 'コネクタ' }]}>
      <AdminTabs />
      <div className="panel-head">
        <h1>コネクタ</h1>
        <span className="muted small">受付箱への入り口。</span>
      </div>
      <ConnectorCards />
      <FileImport />
      <SlackPurgePanel />
      <section className="panel">
        <h2>API から登録する</h2>
        <p className="muted small">API コネクタが接続中なら、次の形で登録できます。</p>
        <pre className="code">{`curl -X POST ${origin}/api/ops/ingest \\
  -H 'Content-Type: application/json' \\
  -d '{"channel":"api","from_name":"山田 花子","from_address":"hanako@example.com",
       "subject":"在庫の確認","body":"木製のペン立ては再入荷しますか？"}'`}</pre>
      </section>
    </Page>
  )
}
