import { useState } from 'react'
import { useNavigate } from 'react-router'
import { errorMessage } from '../../api'
import { Page, useTitle } from '../../shell'
import { ops, type Channel, type ImportRow, type SlackSettings, type SlackState } from '../api'
import { guessMapping, MAPPED_FIELDS, parseCsv, SAMPLE_CSV, type MappedField } from '../csv'
import { useOps, usePolling } from '../state'

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
              <span className={`conn-state${on ? ' on' : ''}`}>{on ? '接続済み（デモ）' : '切断'}</span>
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

function SlackPanel() {
  const { settings, saveSettings } = useOps()
  const status = usePolling(ops.slack, 3000)
  const [edit, setEdit] = useState<SlackSettings | null>(null)
  const [inboundText, setInboundText] = useState<string | null>(null)
  // 受信の入り切りも下書きに入れ、「保存」でまとめて保存する（切り替えだけで他の入力が消えないように）
  const [receivingDraft, setReceivingDraft] = useState<boolean | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  if (!settings) return null
  const st = status.data
  const draft = edit ?? settings.slack
  const inbound = inboundText ?? settings.slack.inbound_channels.join(', ')
  const inboundIds = inbound.split(/[\s,、]+/).filter(Boolean)
  const badIds = [...Object.values(draft.channel_map).filter(Boolean), ...inboundIds].filter((id) => !SLACK_ID.test(id))
  const receiving = receivingDraft ?? settings.connectors.slack ?? false
  // 知らせる時間は、対応目安（営業時間）より短くする。空欄は 0 として送らない
  const maxRemind = Math.max(1, Math.round(settings.sla.hours * 60) - 1)
  const remindError =
    Number.isInteger(draft.reminder_before_min) && draft.reminder_before_min >= 1 && draft.reminder_before_min <= maxRemind
      ? null
      : `知らせる時間は 1〜${maxRemind} 分で入れてください`
  const dirty = edit !== null || inboundText !== null || receivingDraft !== null
  const save = () => {
    setMsg(null)
    saveSettings({
      ...settings,
      connectors: { ...settings.connectors, slack: receiving },
      slack: { ...draft, inbound_channels: inboundIds },
    })
      .then(() => {
        setEdit(null)
        setInboundText(null)
        setReceivingDraft(null)
        setError(null)
        setMsg('保存しました')
      })
      .catch((e: unknown) => setError(errorMessage(e)))
  }
  return (
    <section className="panel" data-testid="slack">
      <h2>Slack</h2>
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
          トークンが未設定です。手順: docs/slack-setup.md
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
        <input id="slack-inbound" value={inbound} placeholder="例: C0123456789" onChange={(e) => setInboundText(e.target.value)} />
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
          {settings.staff
            .filter((m) => m.active || m.id === draft.dispatcher)
            .map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
                {m.active ? '' : '（担当オフ）'}
                {m.slack_user_id ? '' : '（Slack ID なし）'}
              </option>
            ))}
        </select>
        <label htmlFor="slack-remind">担当未定の知らせ</label>
        <label className="small">
          <input id="slack-remind" type="checkbox" checked={draft.reminder} onChange={(e) => setEdit({ ...draft, reminder: e.target.checked })} /> 対応目安の{' '}
          <input
            type="number"
            className="num-input"
            aria-label="何分前に知らせるか"
            min={1}
            max={maxRemind}
            value={Number.isFinite(draft.reminder_before_min) ? draft.reminder_before_min : ''}
            onChange={(e) => setEdit({ ...draft, reminder_before_min: e.target.value === '' ? Number.NaN : Number(e.target.value) })}
          />{' '}
          分前（営業時間）に 1 回だけ
        </label>
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
      <div className="row">
        <button
          type="button"
          disabled={badIds.length > 0 || remindError !== null || !dirty}
          onClick={save}
        >
          保存
        </button>
        {badIds.length > 0 && <span className="error small">チャンネル ID の形式が違います: {badIds.join(', ')}</span>}
        {remindError && <span className="error small">{remindError}</span>}
        {msg && <span className="muted small">{msg}</span>}
      </div>
      {error && <div className="error small">{error}</div>}
    </section>
  )
}

function CsvImport() {
  const { settings, refresh } = useOps()
  const navigate = useNavigate()
  const [fileName, setFileName] = useState('')
  const [rows, setRows] = useState<string[][] | null>(null)
  const [hasHeader, setHasHeader] = useState(true)
  const [mapping, setMapping] = useState<Record<MappedField, number> | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)

  const load = (name: string, text: string) => {
    setError(null)
    setDone(null)
    try {
      const parsed = parseCsv(text)
      if (!parsed.length) throw new Error('行がありません')
      setFileName(name)
      setRows(parsed)
      setMapping(guessMapping(parsed[0] ?? []))
    } catch (e: unknown) {
      setRows(null)
      setError(`CSV を読めませんでした: ${errorMessage(e)}`)
    }
  }
  const header = rows?.[0] ?? []
  const data = rows ? (hasHeader ? rows.slice(1) : rows) : []
  const columns = Array.from({ length: Math.max(0, ...(rows ?? []).map((r) => r.length)) }, (_, i) =>
    hasHeader ? header[i] || `列${i + 1}` : `列${i + 1}`,
  )
  const cell = (r: string[], f: MappedField) => (mapping && mapping[f] >= 0 ? (r[mapping[f]] ?? '') : '')
  const mapped: ImportRow[] = data.map((r) => ({
    from_name: cell(r, 'from_name').trim(),
    from_address: cell(r, 'from_address').trim(),
    subject: cell(r, 'subject').trim(),
    body: cell(r, 'body'),
  }))
  const valid = mapped.filter((r) => r.body.trim())
  const enabled = settings?.connectors.csv ?? false

  const submit = () => {
    setBusy(true)
    setError(null)
    ops
      .importRows(fileName, valid)
      .then((res) => {
        setDone(`${res.imported} 件を取り込みました（${res.ids[0]} 〜 ${res.ids.at(-1)}）`)
        setRows(null)
        refresh()
      })
      .catch((e: unknown) => setError(errorMessage(e)))
      .finally(() => setBusy(false))
  }

  return (
    <section className="panel" data-testid="csv-import" id="csv">
      <div className="panel-head">
        <h2>CSV 取り込み</h2>
        <a className="small" href={`data:text/csv;charset=utf-8,${encodeURIComponent(`﻿${SAMPLE_CSV}`)}`} download="sample_inquiries.csv">
          サンプル CSV をダウンロード
        </a>
      </div>
      {!enabled && <div className="warn-box">CSV コネクタが切断されています。</div>}
      <label
        className="dropzone"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault()
          const f = e.dataTransfer.files[0]
          if (f) void f.text().then((t) => load(f.name, t)).catch((err: unknown) => setError(errorMessage(err)))
        }}
      >
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) void f.text().then((t) => load(f.name, t)).catch((err: unknown) => setError(errorMessage(err)))
            e.target.value = ''
          }}
        />
        <span>CSV をドロップ／クリックで選択（UTF-8・500 行まで）</span>
      </label>
      {rows && mapping && (
        <>
          <div className="row">
            <strong>{fileName}</strong>
            <span className="muted small">{data.length} 行</span>
            <label className="small">
              <input type="checkbox" checked={hasHeader} onChange={(e) => setHasHeader(e.target.checked)} /> 1 行目は見出し
            </label>
          </div>
          <h3>列の対応</h3>
          <div className="mapping">
            {MAPPED_FIELDS.map((f) => (
              <label key={f.id}>
                <span>{f.label}</span>
                <select value={mapping[f.id]} onChange={(e) => setMapping({ ...mapping, [f.id]: Number(e.target.value) })}>
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
          <h3>先頭 5 件</h3>
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>差出人</th>
                  <th>アドレス</th>
                  <th>件名</th>
                  <th>本文</th>
                </tr>
              </thead>
              <tbody>
                {mapped.slice(0, 5).map((r, i) => (
                  <tr key={i} className={r.body.trim() ? undefined : 'off'}>
                    <td>{r.from_name || '-'}</td>
                    <td>{r.from_address || '-'}</td>
                    <td>{r.subject || '-'}</td>
                    <td className="body">{r.body || '（本文なし・取り込まない）'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="row">
            <button type="button" disabled={busy || !enabled || !valid.length || valid.length > 500} onClick={submit}>
              {valid.length} 件を取り込む
            </button>
            {mapped.length !== valid.length && <span className="muted small">本文が空の {mapped.length - valid.length} 行は取り込みません</span>}
            {valid.length > 500 && <span className="error small">一度に取り込めるのは 500 件までです</span>}
          </div>
        </>
      )}
      {done && (
        <div className="done-box">
          {done}{' '}
          <button type="button" className="link-btn" onClick={() => navigate('/ops/inbox')}>
            受付箱で見る
          </button>
        </div>
      )}
      {error && <div className="error">{error}</div>}
    </section>
  )
}

export function Connectors() {
  useTitle('コネクタ')
  const origin = window.location.origin
  return (
    <Page wide crumbs={[{ label: '運用', to: '/ops' }, { label: 'コネクタ' }]}>
      <div className="panel-head">
        <h1>コネクタ</h1>
        <span className="muted small">受付箱への入り口。</span>
      </div>
      <ConnectorCards />
      <SlackPanel />
      <CsvImport />
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
