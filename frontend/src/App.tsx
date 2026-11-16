import { useEffect, useState } from 'react'
import { Route, Routes, Link, useLocation } from 'react-router-dom'
import metisLogo from './metis.svg'
import { LogOut, ArrowRight, Command, Bell, Search, Menu, X } from 'lucide-react'
import { api, ApiError, post } from './api'
import { LearningPage } from './LearningPage'
import { InboxPage } from './InboxPage'
import { ErrorBox, Skeleton } from './components'
import { OverviewPage, AccountsPage, WorksPage, KnowledgePage, ReportsPage, SystemPage } from './pages'

import { WorkspaceNavigation } from './WorkspaceNavigation'
import { WorkspaceBack } from './navigation'

export default function App() {
  return useLocation().pathname === '/inbox' ? <InboxPage /> : <WorkspaceApp />
}

function WorkspaceApp() {
  const [user, setUser] = useState<{ name: string; source_mode: string; local_passwordless: boolean } | null>(null)
  const [initializing, setInitializing] = useState(true)
  const [navigationOpen, setNavigationOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [connectionRevision, setConnectionRevision] = useState(0)
  useEffect(() => {
    let active = true, connecting = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let controller: AbortController | undefined
    async function enter() {
      if (!active || connecting) return
      connecting = true
      clearTimeout(timer)
      controller = new AbortController()
      const timeout = setTimeout(() => controller?.abort(), 15000)
      try {
        let profile
        let renewed = false
        const readProfile = () => api<{ name: string; source_mode: string; local_passwordless: boolean }>('/auth/me', { signal: controller!.signal })
        try { profile = await readProfile() }
        catch (e) {
          if (!(e instanceof ApiError) || e.status !== 401) throw e
          await api('/auth/local', { method: 'POST', signal: controller.signal })
          profile = await readProfile(); renewed = true
        }
        if (active) {
          setUser(profile); setError(''); setInitializing(false)
          if (renewed) window.dispatchEvent(new Event('atlas:session-restored'))
        }
      } catch (e) {
        if (active) {
          if (e instanceof ApiError && [401, 403].includes(e.status)) {
            setUser(null); setInitializing(false) // Preserve the existing login flow for remote or password-protected access.
          } else {
            setError('后台连接中断，正在自动重连…')
            timer = setTimeout(() => void enter(), 5000)
          }
        }
      } finally { clearTimeout(timeout); connecting = false }
    }
    void enter()
    const reconnect = () => { if (document.visibilityState !== 'hidden') void enter() }
    window.addEventListener('atlas:unauthorized', reconnect)
    window.addEventListener('online', reconnect)
    window.addEventListener('focus', reconnect)
    return () => {
      active = false; clearTimeout(timer); controller?.abort()
      window.removeEventListener('atlas:unauthorized', reconnect)
      window.removeEventListener('online', reconnect)
      window.removeEventListener('focus', reconnect)
    }
  }, [connectionRevision])
  async function login(e: React.SubmitEvent<HTMLFormElement>) {
    e.preventDefault(); setBusy(true); setError('')
    try { await post('/auth/login', { password }); setUser(await api('/auth/me')); setPassword('') }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  if (initializing) return <div className="boot"><Skeleton /><ErrorBox error={error} />{error && <button className="button" onClick={() => setConnectionRevision(x => x + 1)}>立即重连</button>}</div>
  if (!user) return <div className="login-page"><section className="login-story"><div className="brand"><img className="metis-wordmark metis-wordmark-inverse" src={metisLogo} alt="Metis" /></div><div><p className="login-kicker">你的个人知识工作台</p><h1>留下证据。<br />让知识持续积累。</h1><p>从创作者的文字、图像与声画中整理知识，<br />每一个结论，都有可以回溯的出处。</p></div><small>抖音 / 小红书 · 个人私有知识库</small></section><section className="login-form"><div><h2>进入知识工作台</h2><p className="muted">使用本机初始化时生成的管理员密码。</p><form onSubmit={login}><label htmlFor="password">管理员密码</label><input id="password" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required autoFocus /><ErrorBox error={error} /><button className="button primary w-full" disabled={busy}>{busy ? '正在登录…' : '登录'}<ArrowRight size={17} /></button></form><p className="login-note">首次运行：执行 scripts/init.ps1；密码保存在项目的 .env 文件中。默认真实采集与付费 API 调用关闭。</p></div></section></div>
  return <div className={`app-shell${navigationOpen ? ' navigation-open' : ''}`}>
    <aside className="sidebar">
      <div className="sidebar-heading"><Link to="/learning" className="brand"><span><img className="metis-wordmark" src={metisLogo} alt="Metis" /><small>创作者知识库</small></span></Link>
        <button className="icon-button mobile-nav-toggle" aria-label="主导航菜单" aria-expanded={navigationOpen} aria-controls="workspace-navigation" onClick={() => setNavigationOpen(!navigationOpen)}>{navigationOpen ? <X size={21} /> : <Menu size={21} />}</button>
      </div>
      <div id="workspace-navigation" className="sidebar-content">
        <Link to="/knowledge" className="header-search" onClick={() => setNavigationOpen(false)}><Search size={16} /><span>搜索知识与观点</span></Link>
        <WorkspaceNavigation onNavigate={() => setNavigationOpen(false)} />
        <div className="sidebar-bottom"><div className="workspace-label"><Command size={14} /><span>个人私有工作空间</span><Link to="/system" aria-label="查看后台通知" className="icon-button" onClick={() => setNavigationOpen(false)}><Bell size={17} /></Link></div>
          <details className="mode-banner"><summary>{user.source_mode === 'mock' ? 'Mock 演示模式' : user.source_mode === 'local' ? '本机 AI 模式' : '云端 AI 模式'}</summary><p>{user.source_mode === 'mock' ? '模拟资料及解析明确标记 · 真实平台能力待验证 · 未调用付费模型' : '真实平台能力待验证 · 人工导入内容按配置处理'}</p><Link to="/system">查看接入状态<ArrowRight size={14} /></Link></details>
          <details className="workspace-about"><summary>工作空间</summary><p>只跟踪指定账号<br />保留知识与原文证据</p><p>个人工作空间 / 知识管理</p><p>Metis · 私人资料，持续积累</p><p>所有知识保留出处与解析记录</p></details>
          {!user.local_passwordless && <button onClick={async () => { try { await post('/auth/logout'); setUser(null) } catch(e) { setError((e as Error).message) } }}><LogOut size={16} />退出登录</button>}
        </div>
      </div>
    </aside>
    <div className="workspace"><main><WorkspaceBack /><ErrorBox error={error} /><Routes>
      <Route path="/" element={<OverviewPage />} /><Route path="/accounts" element={<AccountsPage />} /><Route path="/works" element={<WorksPage />} />
      <Route path="/learning" element={<LearningPage />} /><Route path="/knowledge" element={<KnowledgePage />} /><Route path="/reports" element={<ReportsPage />} /><Route path="/system" element={<SystemPage />} />
      <Route path="*" element={<div className="empty"><h1>页面不存在</h1><Link to="/">返回总览</Link></div>} />
    </Routes></main></div>
  </div>
}
