import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ReflectionPanel } from './ReflectionPanel'
import { useData } from './hooks'
import { post } from './api'

vi.mock('./hooks', () => ({ useData: vi.fn() }))
vi.mock('./api', async () => ({ ...await vi.importActual('./api'), post: vi.fn() }))
const reload = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(post).mockResolvedValue({})
  vi.mocked(useData).mockImplementation(path => ({ data: path === '/learning/context'
    ? { notes: '当前背景', facts: [{ id: 'new', text: '当前背景' }] }
    : { enabled: true, reflection: null }, error: '', loading: false, reload }))
})

describe('深入学习', () => {
  it('针对当前作品排队，不重新解析原视频', async () => {
    render(<ReflectionPanel workId="work1" />)
    fireEvent.click(screen.getByRole('button', { name: '开始深入学习' }))
    await waitFor(() => expect(post).toHaveBeenCalledExactlyOnceWith('/works/work1/reflection'))
  })

  it('展示已保存的背景依据和验证边界', () => {
    vi.mocked(useData).mockImplementation(path => ({ data: path === '/learning/context'
      ? { notes: '当前背景', facts: [] } : { enabled: true, reflection: { id: 'r1', status: 'completed', context_stale: true,
        context_facts: [{ id: 'old', text: '生成时已确认的背景' }], sources: [{ id: 'k1', work_id: 'work1', title: '原始知识', quote: '逐字引文', source_url: '', related: false }],
        output: { summary: '候选建议仍需实际核对。', personal_relevance: { explanation: '仅依据已确认的项目背景。' }, insights: [{ kind: 'experiment', title: '先做小实验', conclusion: '候选建议', reasoning_summary: '有出处的理由', assumptions: [],
          source_knowledge_ids: ['k1'], context_fact_ids: ['old'], alternative_explanations: ['还有其他解释'], next_action: '做小实验', success_criteria: '观察可复现结果', verification_limit: '不能据此确认隐藏机制' }] } } }, error: '', loading: false, reload }))
    render(<ReflectionPanel workId="work1" />)
    expect(screen.getByText('AI 推论 · 待验证')).toBeInTheDocument()
    expect(screen.getByText('生成时已确认的背景')).toBeInTheDocument()
    expect(screen.getByText('不能据此确认隐藏机制')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '结合新背景学习' })).toBeInTheDocument()
    expect(screen.getByRole('link', { hidden: true }).getAttribute('href')).toBe('/works?work=work1')
  })
})
