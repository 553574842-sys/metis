import { useState } from 'react'
import { Lightbulb } from 'lucide-react'
import { post } from './api'
import { useData } from './hooks'
import { Badge, ErrorBox, Status } from './components'

type Insight = { kind: string; title: string; conclusion: string; reasoning_summary: string; source_knowledge_ids: string[]; context_fact_ids: string[]; assumptions: string[]; alternative_explanations: string[]; next_action: string; success_criteria: string; verification_limit: string }
type Reflection = { id: string; status: string; error: string | null; context_stale: boolean; context_facts?: { id: string; text: string }[]; output: { summary?: string; insights?: Insight[]; open_questions?: string[]; limitations?: string[]; personal_relevance?: { explanation: string } }; sources: { id: string; work_id: string; title: string; source_url: string; quote: string; related: boolean }[] }
const names: Record<string, string> = { critique: '前提与局限', extension: '延伸规律', application: '与你的联系', experiment: '行动实验' }

export function ReflectionPanel({ workId }: { workId: string }) {
  const result = useData<{ enabled: boolean; mode?: string; reflection: Reflection | null }>(`/works/${workId}/reflection`, 8000)
  const context = useData<{ notes: string; facts: { id: string; text: string }[] }>('/learning/context')
  const [notes, setNotes] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const item = result.data?.reflection
  async function start() {
    setBusy(true); setError('')
    try { await post(`/works/${workId}/reflection`); result.reload() }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  return <section className="reflection-panel" aria-label="深入学习">
    <div className="section-header"><h3><Lightbulb size={17} />深入学习</h3>
      {item && <Status value={item.status} />}
      {(!item || item.context_stale || ['blocked', 'exhausted'].includes(item.status)) && <button className="button small" disabled={busy || !result.data?.enabled} onClick={() => void start()}>{busy ? '正在排队…' : item?.context_stale ? '结合新背景学习' : '开始深入学习'}</button>}
    </div>
    <ErrorBox error={error || result.error} />
    {!item && <p className="muted text-sm">不限主题，进一步判断前提、联系旧知识，形成可验证的行动。</p>}
    {!item && result.data?.mode === 'configured' && <p className="muted text-sm">使用已配置 AI 的文字模型，会增加文字分析调用。</p>}
    {item?.error && item.status !== 'completed' && <p role="status" className="muted text-sm">{item.error}</p>}
    {item?.output.summary && <><p>{item.output.summary}</p><Badge tone="subtle">AI 推论 · 待验证</Badge><span className="muted text-sm"> 未进行外网核实</span>
      {item.output.personal_relevance && <p><b>与你的联系：</b>{item.output.personal_relevance.explanation}</p>}
      {item.context_stale && <p className="muted text-sm">以下结果基于旧背景，可结合新背景重新学习。</p>}
      {item.output.insights?.map((insight, index) => <details className="reflection-insight" key={index}>
        <summary><span>{names[insight.kind]} · {insight.title}</span></summary>
        <p>{insight.conclusion}</p><p><b>依据与理由：</b>{insight.reasoning_summary}</p>
        {insight.assumptions.length > 0 && <p><b>适用条件：</b>{insight.assumptions.join('；')}</p>}
        <p><b>其他可能解释：</b>{insight.alternative_explanations.join('；')}</p>
        {insight.context_fact_ids.length > 0 && <p><b>采用的背景：</b>{insight.context_fact_ids.map(id => item.context_facts?.find(fact => fact.id === id)?.text ?? context.data?.facts.find(fact => fact.id === id)?.text ?? '生成时保存的背景').join('；')}</p>}
        {insight.next_action && <p><b>下一步：</b>{insight.next_action}</p>}
        {insight.success_criteria && <p><b>验收：</b>{insight.success_criteria}</p>}
        <p><b>验证边界：</b>{insight.verification_limit}</p>
        {insight.source_knowledge_ids.map(id => { const source = item.sources.find(source => source.id === id); return source ? <blockquote key={id}><a href={`/works?work=${encodeURIComponent(source.work_id)}`}>{source.related ? '关联旧知识：' : '依据：'}{source.title}</a><p>{source.quote}</p></blockquote> : null })}
      </details>)}
      {Boolean(item.output.open_questions?.length) && <details><summary>待确认的问题</summary><ul>{item.output.open_questions!.map((question, index) => <li key={index}>{question}</li>)}</ul></details>}
    </>}
    <details className="reflection-context"><summary>用于联系你的背景和目标</summary><p className="muted text-sm">每行一条已确认信息。主题不限，缺少关联时会保留通用启发。</p>
      <ErrorBox error={context.error} />
      <form onSubmit={async event => { event.preventDefault(); setBusy(true); setError(''); try { await post('/learning/context', { notes: notes ?? context.data?.notes ?? '' }); setNotes(null); context.reload(); result.reload() } catch (e) { setError((e as Error).message) } finally { setBusy(false) } }}>
        <label>已确认的背景和目标<textarea rows={4} maxLength={2400} value={notes ?? context.data?.notes ?? ''} onChange={event => setNotes(event.target.value)} /></label><button className="button small" disabled={busy}>保存背景</button>
      </form>
    </details>
  </section>
}
