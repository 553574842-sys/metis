import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { LearningPage } from './LearningPage'
import type { LearningStatus } from './LearningPage'
import { useData } from './hooks'
import { WorkspaceBack } from './navigation'

vi.mock('./hooks', () => ({ useData: vi.fn() }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
const work = { id: 'w1', platform_work_id: '12345678901', creator_id: 'c1', creator: '设计博主', title: '科技企业字母设计案例', content_type: 'video', source_url: 'https://www.douyin.com/video/12345678901', state: 'completed', knowledge_count: 1, brain_state: 'running' }
const data: LearningStatus = {
  generated_at: '2026-10-18T07:00:00Z', refresh_seconds: 5,
  run: { id: 'topic', title: '科技 LOGO 学习', phase: 'visual_review', phase_label: '核对视频画面', snapshot_at: null, has_inventory: true, record_warning: null },
  runs: [{ id: 'topic', title: '科技 LOGO 学习' }],
  creators: [{ id: 'c1', name: '设计博主', profile_url: '', inventoried: 126, videos: 126, images: 0, selected: 13, completed: 11, knowledge_count: 81 }],
  counts: { inventoried: 126, reviewed: 126, review_total: 126, pending_review: 20, excluded: 104, selected: 13, completed: 11, failed: 1, knowledge: 81, brain_completed: 2, brain_total: 11 },
  activities: [{ ...work, stage: '同步神经元知识', live: true, observed_at: '2026-10-18T07:00:00Z', job_id: 'job1' },
    { ...work, id: null, platform_work_id: '12345678902', stage: '最近核对视频画面', live: false, observed_at: '2026-10-18T06:59:00Z', job_id: null }],
  works: [work], pending_review: [{ ...work, id: null, platform_work_id: '12345678902' }],
  knowledge: { total: 1, offset: 0, limit: 12, items: [{ knowledge_item_id: 'k1', work_id: 'w1', creator_id: 'c1', creator: '设计博主', platform: 'douyin',
    title: '字母共笔的方法', summary: '保留科技品牌识别，合并相同笔画。', source_url: work.source_url, evidence_excerpt: '实际原文依据',
    relevance: 1, source_mode: 'real', analysis_mode: 'minimax', claim_kind: 'inference', item_type: 'method', learned_at: '2026-10-18T07:00:00Z', work_title: work.title }] }
}

function renderPage() {
  vi.mocked(useData).mockReturnValue({ data, error: '', loading: false, reload: vi.fn() })
  return render(<MemoryRouter><LearningPage /></MemoryRouter>)
}

function openDisclosure(title: string) {
  fireEvent.click(screen.getByText(title, { selector: 'summary span' }).closest('summary')!)
}

describe('学习面板', () => {
  it('查看作品后返回保留博主、搜索、分页、队列标签和展开状态，并提供 AI 提问入口', () => {
    vi.mocked(useData).mockReturnValue({ data, error: '', loading: false, reload: vi.fn() })
    render(<MemoryRouter initialEntries={['/learning?creator=c1&tab=works&results=1&stats=1&offset=12&q=科技&queue=done']}>
      <WorkspaceBack /><Routes><Route path="/learning" element={<LearningPage />} />
        <Route path="/works" element={<p>作品详情测试页</p>} /></Routes></MemoryRouter>)
    expect(screen.getByRole('link', { name: '问知识库' })).toHaveAttribute('href', '/knowledge?tab=ask')
    fireEvent.click(screen.getByRole('link', { name: '资料与知识' }))
    expect(screen.getByText('作品详情测试页')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '返回上一步' }))
    expect(screen.getByRole('combobox', { name: '筛选学习博主' })).toHaveValue('c1')
    expect(screen.getByRole('textbox', { name: '搜索学习内容' })).toHaveValue('科技')
    expect(screen.getByRole('tab', { name: '作品清单 13' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: '已完成 0' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('学习统计', { selector: 'summary span' }).closest('details')).toHaveAttribute('open')
    expect(screen.getByText('学习成果', { selector: 'summary span' }).closest('details')).toHaveAttribute('open')
    expect(vi.mocked(useData).mock.calls.at(-1)?.[0]).toContain('offset=12')
  })
  it('默认读取全部学习任务，让专题外的新作品可见', () => {
    renderPage()
    expect(vi.mocked(useData).mock.calls[0][0]).toContain('run_id=all&')
  })
  it('显示采集、解析、脑图同步的等待队列及重试时间，运行和阻塞任务分开呈现', () => {
    vi.mocked(useData).mockReturnValue({ data: { ...data, works: [work,
      { ...work, id: 'capture', title: '待采集作品', state: 'pending_capture', brain_state: 'not_started' },
      { ...work, id: 'analyze', title: '待解析作品', state: 'pending_analysis', brain_state: 'not_started' },
      { ...work, id: 'retry', title: '失败后重试作品', state: 'failed_retryable', brain_state: 'not_started', next_retry_at: '2026-10-22T16:41:50Z', error: '等待服务恢复' },
      { ...work, id: 'sync', title: '待同步作品', brain_state: 'queued' },
      { ...work, id: 'blocked', title: '已暂停作品', brain_state: 'blocked', error: '等待确认已导入的片段' },
      { ...work, id: 'done', title: '已完成作品', brain_state: 'completed' },
    ] }, error: '', loading: false, reload: vi.fn() })
    render(<MemoryRouter><LearningPage /></MemoryRouter>)
    const queue = within(screen.getByRole('region', { name: '学习任务队列' }))
    expect(queue.getByRole('tab', { name: '队列 5' })).toHaveAttribute('aria-selected', 'true')
    expect(queue.getByText('等待采集素材')).toBeInTheDocument()
    expect(queue.getByText('等待内容解析')).toBeInTheDocument()
    expect(queue.getByText('等待同步到脑图')).toBeInTheDocument()
    expect(queue.getByText('下次重试 00:41:50')).toBeInTheDocument()
    expect(queue.getByRole('link', { name: '失败后重试作品' })).toHaveAttribute('href', '/works?work=retry')
    expect(queue.queryByText('已暂停作品')).not.toBeInTheDocument()
    expect(queue.queryByText('已完成作品')).not.toBeInTheDocument()
    expect(queue.getByRole('link', { name: work.title })).toBeInTheDocument()
    fireEvent.click(queue.getByRole('tab', { name: '需处理 1' }))
    expect(queue.getByRole('link', { name: '已暂停作品' })).toBeInTheDocument()
    expect(queue.getByText('等待确认已导入的片段')).not.toBeVisible()
    fireEvent.click(queue.getByText('查看原因'))
    expect(queue.getByText('等待确认已导入的片段')).toBeVisible()
    fireEvent.click(queue.getByRole('tab', { name: '已完成 1' }))
    expect(queue.getByRole('link', { name: '已完成作品' })).toBeInTheDocument()
    expect(queue.queryByText('已暂停作品')).not.toBeInTheDocument()
  })
  it('分别呈现筛选、知识入库、神经元进度，并区分实时与最近活动', () => {
    renderPage()
    expect(screen.getByRole('progressbar', { name: '视频转写核对' })).not.toBeVisible()
    openDisclosure('学习统计')
    expect(screen.getByRole('progressbar', { name: '视频转写核对' })).toHaveAttribute('value', '126')
    expect(screen.getByRole('progressbar', { name: '作品解析入库' })).toHaveAttribute('max', '13')
    expect(screen.getByRole('progressbar', { name: '神经元同步' })).toHaveAttribute('value', '2')
    expect(screen.getByText('同步神经元知识')).toBeInTheDocument()
    expect(screen.getByText('最近记录')).toBeInTheDocument()
    expect(screen.getByText('20 条仍待画面确认；已排除 104 条不相关内容')).toBeInTheDocument()
  })
  it('能展开知识原文、查看所属作品，并暂停面板刷新', () => {
    renderPage()
    expect(screen.getByText('保留科技品牌识别，合并相同笔画。')).not.toBeVisible()
    openDisclosure('学习成果')
    expect(screen.getByText('保留科技品牌识别，合并相同笔画。')).not.toBeVisible()
    openDisclosure('字母共笔的方法')
    expect(screen.getByText('保留科技品牌识别，合并相同笔画。')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '查看原文依据' }))
    expect(screen.getByText('实际原文依据')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /来自作品/ })).toHaveAttribute('href', '/works?work=w1')
    fireEvent.click(screen.getByRole('button', { name: '暂停刷新' }))
    expect(screen.getByText('已暂停面板刷新，后台学习任务继续运行。')).toBeInTheDocument()
    expect(vi.mocked(useData).mock.calls.at(-1)?.[1]).toBe(0)
  })
  it('切换作品和待确认列表，搜索只查询面板', () => {
    renderPage()
    openDisclosure('学习成果')
    fireEvent.click(screen.getByRole('tab', { name: '作品清单 13' }))
    expect(screen.getByRole('link', { name: '资料与知识' })).toHaveAttribute('href', '/works?work=w1')
    fireEvent.click(screen.getByRole('tab', { name: '待确认 20' }))
    expect(screen.getByText('已核对转写，仍需结合画面判断相关性。')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '搜索学习内容' }), { target: { value: '芯片' } })
    expect(vi.mocked(useData).mock.calls.at(-1)?.[0]).toContain('q=%E8%8A%AF%E7%89%87')
  })
  it('暂停任务单独列出，每页最多8条，首次打开保持正文和统计折叠', () => {
    vi.mocked(useData).mockReturnValue({ data: { ...data, activities: [], works: Array.from({ length: 10 }, (_, i) =>
      ({ ...work, id: `blocked-${i}`, title: `暂停作品${i + 1}`, brain_state: 'blocked' })) }, error: '', loading: false, reload: vi.fn() })
    render(<MemoryRouter><LearningPage /></MemoryRouter>)
    expect(screen.getByText('当前没有处理或等待的任务')).toBeVisible()
    expect(screen.getByText('学习成果', { selector: 'summary span' }).closest('details')).not.toHaveAttribute('open')
    expect(screen.getByText('学习统计', { selector: 'summary span' }).closest('details')).not.toHaveAttribute('open')
    const queue = within(screen.getByRole('region', { name: '学习任务队列' }))
    fireEvent.click(queue.getByRole('tab', { name: '需处理 10' }))
    expect(queue.getAllByRole('listitem')).toHaveLength(8)
    expect(queue.getByText('1–8 / 10')).toBeInTheDocument()
    fireEvent.click(queue.getByRole('button', { name: '下一页' }))
    expect(queue.getAllByRole('listitem')).toHaveLength(2)
    expect(queue.getByRole('link', { name: '暂停作品10' })).toBeInTheDocument()
  })
})
