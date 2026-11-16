import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'
import { EvidenceCard, SourceLink, Status, ErrorBox } from './components'
import type { Evidence } from './api'

afterEach(cleanup)
const evidence: Evidence = { knowledge_item_id: 'k1', work_id: 'w1', creator_id: 'c1', title: '测试知识', summary: '保留原文出处', creator: '测试作者', platform: 'douyin', source_url: 'https://example.invalid/mock/work', source_mode: 'mock', analysis_mode: 'mock', claim_kind: 'source_claim', item_type: 'method', evidence_excerpt: '可核验原文', relevance: 1 }
describe('来源与状态', () => {
  it('仅在展开后显示原文，并明确标记模拟出处', () => { render(<EvidenceCard evidence={evidence} />); expect(screen.queryByText('可核验原文')).not.toBeInTheDocument(); fireEvent.click(screen.getByRole('button', { name: '查看原文依据' })); expect(screen.getByText('可核验原文')).toBeInTheDocument(); expect(screen.getByText('Mock 解析')).toBeInTheDocument(); expect(screen.getByRole('link').getAttribute('href')).toBe(evidence.source_url) })
  it('阻止危险协议与没有来源的伪链接', () => { render(<SourceLink url="javascript:alert(1)" />); expect(screen.queryByRole('link')).not.toBeInTheDocument(); expect(screen.getByText('来源待补充')).toBeInTheDocument() })
  it('失败状态不显示完成', () => { render(<Status value="failed_retryable" />); expect(screen.getByText('失败待重试')).toBeInTheDocument(); expect(screen.queryByText('已完成')).not.toBeInTheDocument() })
  it('错误通过 alert 语义向辅助技术展示', () => { render(<ErrorBox error="访问受限" />); expect(screen.getByRole('alert')).toHaveTextContent('访问受限') })
})
