import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { KnowledgePage, WorksPage } from './pages'
import { WorkspaceBack } from './navigation'
import { post } from './api'
import { useData } from './hooks'
import type { Answer, Evidence, WorkDetail } from './api'

vi.mock('./hooks', () => ({ useData: vi.fn() }))
vi.mock('./BrainSyncPanel', () => ({ BrainSyncPanel: () => null }))
vi.mock('./api', async importOriginal => ({ ...await importOriginal<typeof import('./api')>(), post: vi.fn() }))

const evidence: Evidence = { knowledge_item_id: 'k1', work_id: 'w1', creator_id: 'alpha', title: '原文里的设计方法',
  summary: '先研究使用场景，再验证识别效果。', creator: '测试博主甲', platform: 'douyin',
  source_url: 'https://www.douyin.com/video/12345678901', evidence_excerpt: '具体原文依据', relevance: 1,
  source_mode: 'real', analysis_mode: 'minimax', claim_kind: 'source_claim', item_type: 'method',
  tags: [{ id: 'game_design', label: '游戏设计' }], learned_at: '2026-10-01T00:00:00+00:00' }
const answer: Answer = { status: 'minimax', answer_zh: '根据库内证据，先验证使用场景。', citations: [evidence], limitations: [] }
const detail: WorkDetail = { id: 'w1', creator_id: 'alpha', creator: '测试博主甲', platform: 'douyin', title: '来源作品',
  body_text: '原文', hashtags: [], source_mode: 'real', analysis_mode: 'minimax', original_url: evidence.source_url,
  processing_state: 'completed', content_type: 'video', published_at: null, identity_unverified: false,
  assets: [], segments: [], jobs: [], temporary_video_present: false, analysis: null, knowledge_items: [] }

function Location() { const value = useLocation(); return <output data-testid="location">{value.pathname + value.search}</output> }
function renderWorkspace(entry: string) {
  return render(<MemoryRouter initialEntries={[entry]}><WorkspaceBack /><Location /><Routes>
    <Route path="/knowledge" element={<KnowledgePage />} /><Route path="/works" element={<WorksPage />} />
  </Routes></MemoryRouter>)
}

beforeEach(() => {
  vi.mocked(useData).mockImplementation(path => ({ data: path === '/creators' ? { items: [
    { id: 'alpha', display_name: '测试博主甲' }, { id: 'beta', display_name: '测试博主乙' },
  ] } : path === '/knowledge/tags' ? { items: [{ id: 'game_design', label: '游戏设计', count: 2 },
    { id: 'ui_design', label: 'UI设计', count: 3 }] }
    : path === '/works/w1' ? detail : path.startsWith('/knowledge/search?') ? { items: [evidence], total: 1 }
    : path.startsWith('/works?') ? { items: [{ ...detail, creator: '测试博主甲' }], total: 60 } : { items: [] },
    error: '', loading: false, reload: vi.fn() }))
  vi.mocked(post).mockResolvedValue(answer)
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('AI 知识库和返回导航', () => {
  it('标签和时间排序由服务端筛选，切换条件重置分页，后退恢复原范围', () => {
    renderWorkspace('/knowledge?tag=game_design&sort=oldest&offset=60&item=k1&q=游戏')
    expect(screen.getByRole('combobox', { name: '知识标签筛选' })).toHaveValue('game_design')
    expect(screen.getByRole('combobox', { name: '知识时间排序' })).toHaveValue('oldest')
    expect(within(screen.getByRole('region', { name: '知识详情' })).getByText('游戏设计')).toBeInTheDocument()
    expect(screen.getByText(/^入库 /)).toBeInTheDocument()
    fireEvent.change(screen.getByRole('combobox', { name: '知识时间排序' }), { target: { value: 'newest' } })
    const lastSearch = vi.mocked(useData).mock.calls.filter(([path]) => path.startsWith('/knowledge/search?')).at(-1)![0]
    const query = new URLSearchParams(lastSearch.split('?')[1])
    expect(query.get('tag')).toBe('game_design')
    expect(query.get('sort')).toBe('newest')
    expect(query.get('q')).toBe('游戏')
    expect(query.get('offset')).toBe('0')
    expect(screen.getByTestId('location')).not.toHaveTextContent('item=k1')
    fireEvent.click(screen.getByRole('button', { name: '返回上一步' }))
    expect(screen.getByTestId('location')).toHaveTextContent('sort=oldest&offset=60&item=k1')
    expect(screen.getByRole('combobox', { name: '知识时间排序' })).toHaveValue('oldest')
    expect(post).not.toHaveBeenCalled()
  })

  it('浏览超过一页的知识时可继续翻页，修改标签后回到第一页', () => {
    const original = vi.mocked(useData).getMockImplementation()!
    vi.mocked(useData).mockImplementation((path, ...args) => path.startsWith('/knowledge/search?')
      ? { data: { items: [evidence], total: 121 }, error: '', loading: false, reload: vi.fn() }
      : original(path, ...args))
    renderWorkspace('/knowledge?sort=newest')
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(screen.getByTestId('location')).toHaveTextContent('offset=60')
    fireEvent.change(screen.getByRole('combobox', { name: '知识标签筛选' }), { target: { value: 'game_design' } })
    expect(screen.getByTestId('location')).toHaveTextContent('tag=game_design')
    expect(screen.getByTestId('location')).not.toHaveTextContent('offset=')
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
    expect(post).not.toHaveBeenCalled()
  })

  it('按游戏标签提问，将标签传给检索服务且不自动调用模型', async () => {
    renderWorkspace('/knowledge?tab=ask&tag=game_design')
    expect(post).not.toHaveBeenCalled()
    fireEvent.change(screen.getByRole('textbox', { name: '你的问题' }), { target: { value: '游戏设计有哪些方法？' } })
    fireEvent.click(screen.getByRole('button', { name: '基于证据回答' }))
    await waitFor(() => expect(post).toHaveBeenCalledExactlyOnceWith('/knowledge/ask', {
      question: '游戏设计有哪些方法？', filters: { platforms: [], creator_ids: [], date_from: null, date_to: null, tags: ['game_design'] },
    }))
  })

  it('直达 AI 问答入口不会自动花费模型调用', () => {
    renderWorkspace('/knowledge?tab=ask')
    expect(screen.getByRole('heading', { name: '向你的知识库提问' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '基于证据回答' })).toBeEnabled()
    expect(post).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '返回上一步' })).toBeDisabled()
  })

  it('按所选账号提问，查看引用作品后返回保留回答和筛选且不重复调用', async () => {
    renderWorkspace('/knowledge?tab=ask&creator=alpha')
    fireEvent.change(screen.getByRole('textbox', { name: '你的问题' }), { target: { value: '如何验证科技品牌识别？' } })
    fireEvent.click(screen.getByRole('button', { name: '基于证据回答' }))
    await waitFor(() => expect(screen.getByText(answer.answer_zh)).toBeInTheDocument())
    expect(post).toHaveBeenCalledExactlyOnceWith('/knowledge/ask', { question: '如何验证科技品牌识别？',
      filters: { platforms: [], creator_ids: ['alpha'], date_from: null, date_to: null } })
    expect(screen.getByText('引用来源 · 1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: '查看作品与解析' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/works?work=w1')
    fireEvent.click(screen.getByRole('button', { name: '返回上一步' }))
    await waitFor(() => expect(screen.getByText(answer.answer_zh)).toBeInTheDocument())
    expect(screen.getByRole('textbox', { name: '你的问题' })).toHaveValue('如何验证科技品牌识别？')
    expect(screen.getByTestId('location')).toHaveTextContent('creator=alpha')
    expect(post).toHaveBeenCalledTimes(1)
  })

  it('请求过程中切换知识范围，不会把旧范围的回答放到新范围', async () => {
    let finish!: (value: Answer) => void
    vi.mocked(post).mockReturnValue(new Promise(resolve => { finish = resolve }))
    renderWorkspace('/knowledge?tab=ask&creator=alpha')
    fireEvent.change(screen.getByRole('textbox', { name: '你的问题' }), { target: { value: '范围隔离检查' } })
    fireEvent.click(screen.getByRole('button', { name: '基于证据回答' }))
    fireEvent.click(screen.getByText('筛选条件', { selector: 'summary' }))
    fireEvent.change(screen.getByRole('combobox', { name: '知识账号筛选' }), { target: { value: 'beta' } })
    finish(answer)
    await waitFor(() => expect(screen.getByRole('button', { name: '基于证据回答' })).toBeEnabled())
    expect(screen.queryByText(answer.answer_zh)).not.toBeInTheDocument()
  })

  it('作品详情关闭保留列表筛选和分页，后退可恢复原详情标签', () => {
    renderWorkspace('/works?creator=alpha&q=logo&offset=30&work=w1&work_tab=transcript')
    expect(screen.getByRole('textbox', { name: '搜索作品' })).toHaveValue('logo')
    expect(screen.getByText('此作品没有声画片段')).toBeInTheDocument()
    expect(useData).toHaveBeenCalledWith(expect.stringContaining('offset=30'), 5000)
    fireEvent.click(screen.getByRole('button', { name: '处理记录' }))
    expect(screen.getByTestId('location')).toHaveTextContent('work_tab=jobs')
    fireEvent.click(screen.getByRole('button', { name: '返回上一步' }))
    expect(screen.getByText('此作品没有声画片段')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/works?creator=alpha&q=logo&offset=30')
    expect(screen.queryByRole('region', { name: '作品与解析资料' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '返回上一步' }))
    expect(screen.getByText('此作品没有声画片段')).toBeInTheDocument()
  })

  it('检索和选中的知识可随历史返回恢复', () => {
    renderWorkspace('/knowledge?q=logo&creator=alpha&item=k1')
    const reader = within(screen.getByRole('region', { name: '知识详情' }))
    fireEvent.click(reader.getByRole('link', { name: '作品与解析资料' }))
    fireEvent.click(screen.getByRole('button', { name: '返回上一步' }))
    expect(screen.getByRole('textbox', { name: '搜索知识' })).toHaveValue('logo')
    expect(screen.getByTestId('location')).toHaveTextContent('item=k1')
    expect(screen.getByRole('region', { name: '知识详情' })).toHaveTextContent(evidence.title)
  })
})
