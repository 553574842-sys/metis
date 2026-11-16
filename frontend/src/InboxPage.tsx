import { useCallback, useEffect, useRef, useState } from 'react'
import metisLogo from './metis.svg'
import { ArrowLeft, ArrowUpRight, BookOpen, Check, Clock3, Inbox, Link as LinkIcon, LogOut, RefreshCw, Send, ShieldCheck, Smartphone } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, post, stateName } from './api'
import { Badge, Empty, ErrorBox, Skeleton, SourceLink } from './components'
import { InboxError, inboxApi } from './inboxApi'
import type { InboxDetail, InboxSession, InboxWork } from './inboxApi'
import './inbox.css'

const timestamp = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Hong_Kong', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const when = (value: string) => timestamp.format(new Date(/[Zz]$|[+-]\d{2}:\d{2}$/.test(value) ? value : value + 'Z'))
const syncName: Record<string, string> = { not_started: '等待同步', queued: '同步排队中', running: '正在同步', waiting: '同步待重试', completed: '已同步', blocked: '同步需处理', exhausted: '同步需处理' }
type DeviceList = { phone_url: string | null; items: { id: string; name: string; created_at: string; expires_at: string; revoked: boolean }[] }

function MobileSetup() {
  const [devices, setDevices] = useState<DeviceList | null>(null)
  const [pairing, setPairing] = useState<{ code: string; expires_at: string; phone_url: string | null } | null>(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const refresh = useCallback(async () => {
    try { setDevices(await api<DeviceList>('/inbox/devices')); setError('') }
    catch (e) { setError((e as Error).message) }
  }, [])
  useEffect(() => { void refresh(); const timer = setInterval(() => void refresh(), 10000); return () => clearInterval(timer) }, [refresh])
  return <section className="inbox-setup" role="tabpanel" id="inbox-panel-setup" aria-labelledby="inbox-tab-setup">
    <div className="inbox-section-title"><Smartphone size={20} /><div><h2>连接你的 iPhone</h2><p>手机与电脑登录同一个 Tailscale 账号，再完成一次配对。</p></div></div>
    <ErrorBox error={error} />
    {devices?.phone_url ? <div className="inbox-phone-address"><span>在 iPhone 的 Safari 打开</span><a href={devices.phone_url} target="_blank" rel="noreferrer">{devices.phone_url}<ArrowUpRight size={15} /></a></div>
      : <div className="notice">手机访问地址尚未就绪。电脑登录 Tailscale 并启用私有连接后，这里会显示地址。</div>}
    <button className="button" disabled={busy || !devices?.phone_url} onClick={async () => {
      setBusy(true); setError('')
      try { setPairing(await post('/inbox/pairings')) } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
    }}>{busy ? '正在生成…' : pairing ? '生成新配对码' : '生成一次性配对码'}</button>
    {pairing && <div className="inbox-pair-code" role="status"><span>在手机输入这 8 位数字</span><strong>{pairing.code.slice(0, 4)} {pairing.code.slice(4)}</strong><small>有效至 {when(pairing.expires_at)}，使用一次后失效。</small></div>}
    <p className="inbox-help">配对后手机免输密码，可提交链接并查看自己提交作品的学习结果；配对有效期为 90 天。</p>
    {!!devices?.items.length && <div className="inbox-devices"><h3>已配对设备</h3>{devices.items.map(device => <div key={device.id}>
      <span><b>{device.name}</b><small>{device.revoked ? '已撤销' : `有效至 ${when(device.expires_at)}`}</small></span>
      {!device.revoked && <button className="text-button" disabled={busy} onClick={async () => {
        setBusy(true); try { await post(`/inbox/devices/${device.id}/revoke`); await refresh() } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
      }}>撤销配对</button>}</div>)}</div>}
  </section>
}

function Steps({ work }: { work: InboxWork }) {
  const steps = [
    { label: '获取素材', done: work.capture_done },
    { label: '知识入库', done: work.knowledge_done },
    { label: '神经元同步', done: work.brain_state === 'completed' },
  ]
  return <ol className="inbox-steps" aria-label="学习阶段">{steps.map((step, index) => <li key={step.label} className={step.done ? 'done' : ''}>
    <span>{step.done ? <Check size={13} /> : index + 1}</span>{step.label}</li>)}</ol>
}

export function InboxPage() {
  const [session, setSession] = useState<InboxSession | null>(null), [loading, setLoading] = useState(true)
  const [error, setError] = useState(''), [pairError, setPairError] = useState(''), [code, setCode] = useState('')
  const [busy, setBusy] = useState(false), [text, setText] = useState(''), [message, setMessage] = useState('')
  const [tab, setTab] = useState<'submit' | 'learning' | 'setup'>('submit')
  const [data, setData] = useState<{ items: InboxWork[]; total: number; offset: number } | null>(null)
  const [offset, setOffset] = useState(0), [selected, setSelected] = useState(''), [detail, setDetail] = useState<InboxDetail | null>(null)
  const [refreshing, setRefreshing] = useState(false), [updated, setUpdated] = useState('')
  const requestVersion = useRef(0)

  useEffect(() => {
    let active = true
    async function enter() {
      try {
        let profile: InboxSession
        try { profile = await inboxApi<InboxSession>('/me') }
        catch (e) {
          if (!['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname)) throw e
          await post('/auth/local'); profile = await inboxApi<InboxSession>('/me')
        }
        if (active) setSession(profile)
      } catch (e) { if (active && (!(e instanceof InboxError) || e.status !== 401)) setError((e as Error).message) }
      finally { if (active) setLoading(false) }
    }
    void enter()
    const previousTitle = document.title
    document.title = '学习收件箱 · Metis'
    const manifest = document.createElement('link'); manifest.rel = 'manifest'; manifest.href = new URL('./inbox.webmanifest?no-inline', import.meta.url).href
    document.head.appendChild(manifest)
    const meta = document.createElement('meta'); meta.name = 'apple-mobile-web-app-capable'; meta.content = 'yes'; document.head.appendChild(meta)
    return () => { active = false; manifest.remove(); meta.remove(); document.title = previousTitle }
  }, [])

  const refresh = useCallback(async () => {
    if (!session) return
    const version = ++requestVersion.current
    setRefreshing(true)
    try {
      const [list, work] = await Promise.all([
        inboxApi<{ items: InboxWork[]; total: number; offset: number }>(`/submissions?offset=${offset}`),
        selected ? inboxApi<InboxDetail>('/submissions/' + selected) : Promise.resolve(null),
      ])
      if (version !== requestVersion.current) return
      setData(list); setDetail(work); setError(''); setUpdated(new Date().toISOString())
    } catch (e) {
      if (version !== requestVersion.current) return
      if (e instanceof InboxError && e.status === 401) { setSession(null); setData(null); setDetail(null); setSelected(''); setError('配对已失效，请重新连接。') }
      else setError((e as Error).message)
    } finally { if (version === requestVersion.current) setRefreshing(false) }
  }, [session, offset, selected])
  useEffect(() => {
    if (!session) return
    const visible = () => { if (!document.hidden) void refresh() }
    void refresh(); const timer = setInterval(visible, 5000)
    document.addEventListener('visibilitychange', visible)
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', visible); requestVersion.current++ }
  }, [session, refresh])

  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setMessage(''); setError('')
    try {
      const result = await inboxApi<{ work_id: string; existing: boolean }>('/submissions', { share_text: text })
      setText(''); setOffset(0); setSelected(result.work_id); setDetail(null); setTab('learning')
      setMessage(result.existing ? '这条作品已有记录，已为你打开。没有重复解析。' : '链接已收下，电脑会自动处理。')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  return <div className="inbox-shell"><header className="inbox-header"><div className="inbox-brand"><span><img className="metis-wordmark" src={metisLogo} alt="Metis" /><small>随手收藏，留下知识</small></span></div>
    {session?.local ? <Link to="/learning" className="text-link">电脑工作台<ArrowUpRight size={14} /></Link> : session && <button className="icon-button" aria-label="断开手机配对" onClick={async () => {
      try { await inboxApi('/disconnect', {}); setSession(null); setData(null); setSelected(''); setDetail(null); setMessage(''); setCode('') } catch (e) { setError((e as Error).message) }
    }}><LogOut size={18} /></button>}</header>
    <main className="inbox-main"><div className="inbox-intro"><span className="inbox-connection"><span className={session ? 'online-dot' : 'inbox-dot-off'} />{session ? session.local ? '本机后台' : `${session.name}已连接` : '首次连接'}</span><h1>学习收件箱</h1><p>刷到值得留下的内容，把抖音链接放进来。</p></div>
      <ErrorBox error={error} />
      {loading ? <Skeleton /> : !session ? <section className="inbox-pair-panel"><div className="inbox-section-title"><ShieldCheck size={24} /><div><h2>连接这台 iPhone</h2><p>输入电脑收件箱里显示的一次性配对码。</p></div></div>
        <form onSubmit={async e => {
          e.preventDefault(); setBusy(true); setPairError('')
          try { setSession(await inboxApi<InboxSession>('/pair', { code, name: '我的 iPhone' })); setCode(''); setError('') }
          catch (e) { setPairError((e as Error).message) } finally { setBusy(false) }
        }}><label htmlFor="inbox-code">8 位配对码</label><input id="inbox-code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{8}" minLength={8} maxLength={8} required value={code} onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 8))} placeholder="输入 8 位数字" />
          <ErrorBox error={pairError} /><button className="button primary" disabled={busy || code.length !== 8}>{busy ? '正在连接…' : '配对并进入'}<ArrowUpRight size={16} /></button></form>
        <p className="inbox-help">先保持 Tailscale 已连接。完成一次配对后，可在这台手机查看自己提交的作品；电脑端可以随时撤销配对。</p></section> : <>
        <div className="inbox-tabs" role="tablist" aria-label="收件箱内容">{([['submit', '提交链接'], ['learning', '我的学习'], ...(session.local ? [['setup', '连接 iPhone']] : [])] as const).map(([id, label]) =>
          <button key={id} role="tab" id={`inbox-tab-${id}`} aria-controls={`inbox-panel-${id}`} aria-selected={tab === id} onClick={() => { setTab(id as typeof tab); setMessage('') }}>{label}{id === 'learning' && data ? <span>{data.total}</span> : null}</button>)}</div>
        {message && <div className="inbox-success" role="status"><Check size={17} />{message}</div>}
        {tab === 'submit' && <section className="inbox-compose" role="tabpanel" id="inbox-panel-submit" aria-labelledby="inbox-tab-submit"><div className="inbox-section-title"><LinkIcon size={21} /><div><h2>把链接交给电脑</h2><p>支持单条作品链接，也可以粘贴整段分享文案。</p></div></div>
          <form onSubmit={submit}><label htmlFor="inbox-share">抖音链接或分享文案</label><textarea id="inbox-share" rows={5} maxLength={8000} required value={text} onChange={e => setText(e.target.value)} placeholder="在抖音点分享 → 复制链接，然后粘贴到这里。" />
            <button className="button primary inbox-send" disabled={busy || !text.trim()}><Send size={17} />{busy ? '正在提交…' : '发送并开始学习'}</button></form>
          <p className="inbox-help">每次只处理你提交的作品。电脑保持开机、联网，登录或验证问题会在学习记录里显示；解析使用现有 MiniMax 配置。</p>
          <div className="inbox-how"><span><Inbox size={16} />收下链接</span><i /><span><RefreshCw size={16} />后台解析</span><i /><span><BookOpen size={16} />留下知识</span></div>
          <details className="inbox-iphone-help"><summary>把收件箱放到 iPhone 主屏幕</summary><p>在 Safari 打开此页，点分享按钮，再选择“添加到主屏幕”。以后复制完链接，点主屏幕图标就能提交。</p></details>
        </section>}
        {tab === 'setup' && session.local && <MobileSetup />}
        {tab === 'learning' && <section className="inbox-learning" role="tabpanel" id="inbox-panel-learning" aria-labelledby="inbox-tab-learning"><div className="inbox-list-heading"><div><h2>{selected ? '这条作品的学习情况' : '已提交的作品'}</h2><p>{updated ? `${when(updated)} 更新 · 每 5 秒刷新` : '正在读取学习记录'}</p></div><button className="icon-button" onClick={() => void refresh()} disabled={refreshing} aria-label="刷新学习记录"><RefreshCw size={18} className={refreshing ? 'inbox-spinning' : ''} /></button></div>
          {selected ? <><button className="text-button inbox-back" onClick={() => { setSelected(''); setDetail(null) }}><ArrowLeft size={15} />返回作品列表</button>{!detail ? <Skeleton /> : <article className="inbox-detail">
            <div className="inbox-work-meta"><span>{detail.creator}</span><Badge tone={detail.knowledge_done ? 'success' : detail.error ? 'warning' : ''}>{stateName[detail.state] ?? detail.state}</Badge></div>
            <h3>{detail.title}</h3><SourceLink url={detail.source_url} /><p className="inbox-stage"><Clock3 size={16} />{detail.stage}</p><Steps work={detail} />
            {detail.error && <ErrorBox error={detail.error} />}{detail.next_retry_at && <p className="inbox-help">下次重试：{when(detail.next_retry_at)}</p>}
            <p className="inbox-sync">神经元：{syncName[detail.brain_state] ?? detail.brain_state}{detail.knowledge_done && detail.brain_state !== 'completed' ? '。已入库知识可先在下方阅读。' : ''}</p>
            <div className="inbox-knowledge-heading"><BookOpen size={19} /><h2>已学到的知识</h2><span>{detail.knowledge.length} 条</span></div>
            {detail.knowledge.length ? detail.knowledge.map(item => <article className="inbox-knowledge" key={item.id}><Badge tone="subtle">{item.claim_kind === 'inference' ? 'AI 推论' : '原文观点'}</Badge><h3>{item.title}</h3><p>{item.summary}</p><details><summary>查看原文依据</summary><blockquote>{item.evidence}</blockquote></details></article>)
              : <Empty title={detail.error ? '学习暂未完成' : '等待知识入库'}>处理完成后，知识会自动出现在这里。</Empty>}
          </article>}</> : !data ? <Skeleton /> : !data.items.length ? <Empty title="还没有提交作品">先复制一条抖音链接，回到“提交链接”发送。</Empty> : <><div className="inbox-work-list">{data.items.map(work => <button className="inbox-work-row" key={work.submission_id} onClick={() => { setSelected(work.id); setDetail(null) }}>
            <div className="inbox-work-meta"><span>{work.creator}</span><Badge tone={work.knowledge_done ? 'success' : work.error ? 'warning' : ''}>{work.knowledge_done ? `${work.knowledge_count} 条知识` : work.stage}</Badge></div><h3>{work.title}</h3><span className="inbox-work-bottom">{when(work.submitted_at)}<span>{work.knowledge_done ? '阅读知识' : '查看进度'}<ArrowUpRight size={14} /></span></span></button>)}</div>
            <div className="inbox-pagination"><button className="button small" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button><span>共 {data.total} 条作品</span><button className="button small" disabled={offset + 20 >= data.total} onClick={() => setOffset(offset + 20)}>下一页</button></div></>}
        </section>}
      </>}
      <footer className="inbox-footer"><ShieldCheck size={14} />私人收件箱 · 知识保留出处</footer>
    </main></div>
}
