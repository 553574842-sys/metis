import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { WorksPage } from './pages'
import { post } from './api'
import type { WorkDetail } from './api'
import { useData } from './hooks'

vi.mock('./hooks', () => ({ useData: vi.fn() }))
vi.mock('./api', async importOriginal => ({ ...await importOriginal<typeof import('./api')>(), post: vi.fn() }))

const reload = vi.fn()
const detail: WorkDetail = {
  id: 'failed-work', creator_id: 'owner', creator: '视频作者', platform: 'douyin', title: '待重试的视频',
  body_text: '原始正文', hashtags: [], source_mode: 'real', analysis_mode: 'minimax', original_url: '',
  processing_state: 'failed_retryable', content_type: 'video', published_at: null, identity_unverified: false,
  assets: [], segments: [], temporary_video_present: true, analysis: null, knowledge_items: [],
  jobs: [{ id: 'failed-job', work_id: 'failed-work', job_type: 'analyze', state: 'exhausted', attempt_count: 4,
    next_retry_at: null, last_error_message: '引用未通过校验', last_error_code: 'AI_REFERENCE_INVALID' }],
}

function renderWork(work = detail) {
  vi.mocked(useData).mockImplementation(path => ({ data: path === '/works/failed-work' ? work : { items: [], total: 0 },
    error: '', loading: false, reload }))
  return render(<MemoryRouter initialEntries={['/works?work=failed-work']}><WorksPage /></MemoryRouter>)
}

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) { this.setAttribute('open', '') })
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) { this.removeAttribute('open') })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('失败作品重新解析', () => {
  it('首次解析失败也能提交重解析，并在请求期间禁用按钮', async () => {
    let finish!: (value: unknown) => void
    vi.mocked(post).mockReturnValue(new Promise(resolve => { finish = resolve }))
    renderWork()
    fireEvent.click(screen.getByRole('button', { name: '重新解析' }))
    expect(screen.getByRole('button', { name: '正在提交…' })).toBeDisabled()
    expect(post).toHaveBeenCalledExactlyOnceWith('/works/failed-work/reanalyze')
    finish({ job_id: 'new-job' })
    await waitFor(() => expect(screen.getByText('已排队重新分析保存的资料。')).toBeInTheDocument())
    expect(reload).toHaveBeenCalledOnce()
  })

  it('等待自动重试的任务显示队列状态，阻止重复提交', () => {
    renderWork({ ...detail, jobs: [{ ...detail.jobs[0], state: 'waiting' }] })
    expect(screen.getByRole('button', { name: '已在解析队列' })).toBeDisabled()
    expect(post).not.toHaveBeenCalled()
  })

  it('采集尚未成功时不提供素材重新解析按钮', () => {
    renderWork({ ...detail, jobs: [{ ...detail.jobs[0], job_type: 'capture' }] })
    expect(screen.queryByRole('button', { name: '重新解析' })).not.toBeInTheDocument()
  })

  it('请求失败后保留错误说明并允许重试', async () => {
    vi.mocked(post).mockRejectedValue(new Error('本机服务暂不可用'))
    renderWork()
    fireEvent.click(screen.getByRole('button', { name: '重新解析' }))
    await waitFor(() => expect(screen.getByText('本机服务暂不可用')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '重新解析' })).toBeEnabled()
    expect(reload).not.toHaveBeenCalled()
  })
})
