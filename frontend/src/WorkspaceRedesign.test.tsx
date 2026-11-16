import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AccountsPage } from './pages'
import { WorkspaceNavigation } from './WorkspaceNavigation'
import { KnowledgeBrowser } from './KnowledgeBrowser'
import { PageTitle } from './components'
import { useData } from './hooks'
import { post } from './api'
import type { Creator, Evidence } from './api'

vi.mock('./hooks', () => ({ useData: vi.fn() }))
vi.mock('./api', async importOriginal => ({ ...await importOriginal<typeof import('./api')>(), post: vi.fn() }))

const creators: Creator[] = [
  { id: 'alpha', display_name: '设计博主甲', platform: 'douyin', profile_url: 'https://www.douyin.com/user/alpha',
    source_mode: 'real', tracking_state: 'tracking', history_scan_state: 'pending', access_state: 'available', work_count: 12, next_check_at: null },
  { id: 'beta', display_name: '设计博主乙', platform: 'xiaohongshu', profile_url: '',
    source_mode: 'manual', tracking_state: 'stopped', history_scan_state: 'manual', access_state: 'available', work_count: 3, next_check_at: null },
]
const items: Evidence[] = ['甲', '乙'].map((name, index) => ({ knowledge_item_id: `k${index}`, work_id: `w${index}`, creator_id: 'alpha',
  title: `${name}的设计方法`, summary: `${name}的完整知识正文\n第二段正文保留。`, creator: `作者${name}`, platform: 'douyin',
  source_url: `https://www.douyin.com/video/1234567890${index}`, evidence_excerpt: `${name}的逐字原文证据`, relevance: 1,
  source_mode: 'real', analysis_mode: 'minimax', claim_kind: index ? 'inference' : 'source_claim', item_type: 'method' }))

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) { this.setAttribute('open', '') })
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) { this.removeAttribute('open') })
  vi.mocked(post).mockResolvedValue({})
  vi.mocked(useData).mockImplementation(path => ({ data: path === '/creators' ? { items: creators }
    : path.startsWith('/works?') ? { items: [{ id: 'work', title: path.includes('beta') ? '乙的作品' : '甲的作品', processing_state: 'completed' }], total: 1 }
    : path.startsWith('/knowledge/search?') ? { items, total: 2 } : { items: [] }, error: '', loading: false, reload: vi.fn() }))
})
afterEach(() => { cleanup(); vi.clearAllMocks() })
const accounts = () => render(<MemoryRouter><AccountsPage /></MemoryRouter>)

describe('账号列表与详情', () => {
  it('选择博主只读取对应的资料，设置默认收起', () => {
    accounts()
    expect(screen.getByRole('tab', { name: '作品' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('甲的作品')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '停止跟踪' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /设计博主乙/ }))
    expect(screen.getByText('乙的作品')).toBeInTheDocument()
    expect(useData).toHaveBeenCalledWith('/works?creator_id=beta&offset=0', 5000)
    expect(screen.getByRole('button', { name: /设计博主乙/ })).toHaveAttribute('aria-pressed', 'true')
  })

  it('知识来源保留，正文在展开后阅读', () => {
    accounts()
    fireEvent.click(screen.getByRole('tab', { name: '已学知识' }))
    const summary = screen.getByText('甲的设计方法', { selector: 'summary' })
    const disclosure = summary.closest('details')!
    expect(disclosure).not.toHaveAttribute('open')
    fireEvent.click(summary)
    expect(disclosure).toHaveAttribute('open')
    fireEvent.click(within(disclosure).getByRole('button', { name: '查看原文依据' }))
    expect(screen.getByText('甲的逐字原文证据')).toBeVisible()
  })

  it('停止跟踪保留原有确认，打开设置不会触发采集或解析', async () => {
    accounts()
    fireEvent.click(screen.getByRole('tab', { name: '设置' }))
    expect(post).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '停止跟踪' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('仅停止以后采集，历史知识继续保留和可检索。正在处理的作品会安全完成当前事务。')).toBeInTheDocument()
    expect(post).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '确认停止，保留历史' }))
    await waitFor(() => expect(post).toHaveBeenCalledExactlyOnceWith('/creators/alpha/stop'))
  })

  it('搜索无结果时可清除条件恢复列表', () => {
    accounts()
    fireEvent.change(screen.getByRole('textbox', { name: '搜索博主' }), { target: { value: '不存在的名字' } })
    expect(screen.queryByRole('button', { name: /设计博主甲/ })).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '搜索博主' }), { target: { value: '' } })
    expect(screen.getByRole('button', { name: /设计博主甲/ })).toBeInTheDocument()
  })
})

describe('知识阅读区与导航', () => {
  it('切换知识后显示对应完整正文、来源和作品链接', () => {
    render(<MemoryRouter><KnowledgeBrowser items={items} /></MemoryRouter>)
    const reader = within(screen.getByRole('region', { name: '知识详情' }))
    expect(reader.getByRole('heading', { name: '甲的设计方法' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /作者乙 乙的设计方法/ }))
    expect(reader.getByRole('heading', { name: '乙的设计方法' })).toBeInTheDocument()
    expect(reader.getByText('乙的完整知识正文 第二段正文保留。')).toBeInTheDocument()
    expect(reader.getByText('AI 推论')).toBeInTheDocument()
    fireEvent.click(reader.getByRole('button', { name: '查看原文依据' }))
    expect(reader.getByText('乙的逐字原文证据')).toBeVisible()
    expect(reader.getByRole('link', { name: '作品与解析资料' })).toHaveAttribute('href', '/works?work=w1')
  })

  it('保留全部八个导航入口，选择状态随路由变化', () => {
    const navigate = vi.fn()
    render(<MemoryRouter initialEntries={['/accounts']}><WorkspaceNavigation onNavigate={navigate} /></MemoryRouter>)
    const navigation = within(screen.getByRole('navigation', { name: '主导航' }))
    expect(navigation.getAllByRole('link')).toHaveLength(8)
    expect(navigation.getByText('学习')).toBeInTheDocument()
    expect(navigation.getByText('资料与知识')).toBeInTheDocument()
    expect(navigation.getByRole('link', { name: '账号管理' })).toHaveAttribute('aria-current', 'page')
    fireEvent.click(navigation.getByRole('link', { name: 'AI 知识中心' }))
    expect(navigate).toHaveBeenCalledOnce()
    expect(navigation.getByRole('link', { name: 'AI 知识中心' })).toHaveAttribute('aria-current', 'page')
  })

  it('页面说明保留原文并默认折叠', () => {
    render(<PageTitle title="页面标题" description="已确认的说明文字，逐字保留。" />)
    expect(screen.getByRole('heading', { name: '页面标题' })).toBeInTheDocument()
    expect(screen.getByText('已确认的说明文字，逐字保留。')).not.toBeVisible()
    fireEvent.click(screen.getByLabelText('页面标题说明'))
    expect(screen.getByText('已确认的说明文字，逐字保留。')).toBeVisible()
  })
})
