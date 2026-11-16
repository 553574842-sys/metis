import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { InboxPage } from './InboxPage'
import { InboxError, inboxApi } from './inboxApi'
import type { InboxDetail } from './inboxApi'
import { api } from './api'

vi.mock('./inboxApi', async importOriginal => ({ ...await importOriginal<typeof import('./inboxApi')>(), inboxApi: vi.fn() }))
vi.mock('./api', async importOriginal => ({ ...await importOriginal<typeof import('./api')>(), api: vi.fn(), post: vi.fn() }))
afterEach(() => { cleanup(); vi.clearAllMocks() })
const session = { name: '我的 iPhone', local: false, expires_at: '2027-01-01T00:00:00Z' }
const detail: InboxDetail = { id: 'w1', submission_id: 's1', title: '科技企业字母设计', creator: '设计博主', source_url: 'https://www.douyin.com/video/12345678901',
  state: 'completed', stage: '知识已入库', submitted_at: '2026-10-19T09:00:00Z', knowledge_count: 1, capture_done: true, knowledge_done: true, brain_state: 'blocked',
  error: null, next_retry_at: null, knowledge: [{ id: 'k1', title: '笔画共用的方法', summary: '通过共用笔画构造字母。', evidence: '保留下来的逐字原文', claim_kind: 'source_claim' }] }
const mount = () => render(<MemoryRouter><InboxPage /></MemoryRouter>)

describe('手机学习收件箱', () => {
  it('首次输入配对码后进入提交页，不显示管理员连接操作', async () => {
    vi.mocked(inboxApi).mockImplementation(async path => {
      if (path === '/me') throw new InboxError('未配对', 401)
      if (path === '/pair') return session
      return { items: [], total: 0, offset: 0 }
    })
    mount()
    // The local test origin first tries the existing local login; its mocked
    // request fails, preserving the phone's pairing state for this test.
    const field = await screen.findByLabelText('8 位配对码')
    fireEvent.change(field, { target: { value: '12345678' } })
    fireEvent.click(screen.getByRole('button', { name: '配对并进入' }))
    expect(await screen.findByLabelText('抖音链接或分享文案')).toBeInTheDocument()
    expect(vi.mocked(inboxApi)).toHaveBeenCalledWith('/pair', { code: '12345678', name: '我的 iPhone' })
    expect(screen.queryByRole('tab', { name: '连接 iPhone' })).not.toBeInTheDocument()
  })

  it('提交整段分享文案一次，自动显示阶段、知识和原文依据', async () => {
    vi.mocked(inboxApi).mockImplementation(async (path, body) => {
      if (path === '/me') return session
      if (path === '/submissions' && body) return { work_id: 'w1', existing: false }
      if (path === '/submissions/w1') return detail
      return { items: [detail], total: 1, offset: 0 }
    })
    mount()
    fireEvent.change(await screen.findByLabelText('抖音链接或分享文案'), { target: { value: '分享文案 https://v.douyin.com/test/ 复制观看' } })
    fireEvent.click(screen.getByRole('button', { name: '发送并开始学习' }))
    expect(await screen.findByText('笔画共用的方法')).toBeInTheDocument()
    expect(screen.getByText('链接已收下，电脑会自动处理。')).toBeInTheDocument()
    expect(screen.getByText('知识已入库')).toBeInTheDocument()
    expect(screen.getByText('神经元：同步需处理。已入库知识可先在下方阅读。')).toBeInTheDocument()
    fireEvent.click(screen.getByText('查看原文依据'))
    expect(screen.getByText('保留下来的逐字原文')).toBeVisible()
    expect(vi.mocked(inboxApi).mock.calls.filter(([path, body]) => path === '/submissions' && body)).toHaveLength(1)
  })

  it('手机配对失效时清除作品和详情，回到配对页', async () => {
    vi.mocked(inboxApi).mockImplementation(async path => {
      if (path === '/me') return session
      throw new InboxError('配对已撤销', 401)
    })
    mount()
    expect(await screen.findByLabelText('8 位配对码')).toBeInTheDocument()
    expect(screen.getByText('配对已失效，请重新连接。')).toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: /我的学习/ })).not.toBeInTheDocument()
  })

  it('电脑未配置远程地址时明确说明状态，并禁用配对码生成', async () => {
    vi.mocked(inboxApi).mockImplementation(async path => path === '/me' ? { ...session, local: true, name: '本机' } : { items: [], total: 0, offset: 0 })
    vi.mocked(api).mockResolvedValue({ phone_url: null, items: [] })
    mount()
    fireEvent.click(await screen.findByRole('tab', { name: '连接 iPhone' }))
    await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledWith('/inbox/devices'))
    expect(screen.getByText(/手机访问地址尚未就绪/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '生成一次性配对码' })).toBeDisabled()
  })
})
