import { useState } from 'react'
import { ArrowUpRight, Network, RefreshCw } from 'lucide-react'
import { post, stateName } from './api'
import { useData } from './hooks'
import { Badge, ErrorBox } from './components'

type BrainStatus = {
  enabled: boolean; panel_url: string; eligible_knowledge_count: number
  jobs: { id: string; state: string; synced_documents: number; last_error_message: string | null }[]
}

export function BrainSyncPanel() {
  const { data, error, reload } = useData<BrainStatus>('/brain/status', 5000)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [failure, setFailure] = useState('')
  const active = data?.jobs.some(job => ['queued', 'running', 'waiting'].includes(job.state))
  async function sync() {
    setBusy(true); setFailure(''); setMessage('')
    try {
      const result = await post<{ jobs: { state: string }[] }>('/brain/sync')
      setMessage(result.jobs.some(job => ['queued', 'running', 'waiting'].includes(job.state))
        ? '同步已排队，完成后可在神经元脑图中查看。'
        : '当前知识版本已同步。')
      reload()
    } catch (reason) { setFailure((reason as Error).message) }
    finally { setBusy(false) }
  }
  return <section className="panel brain-sync-panel" aria-label="神经元知识同步">
    <div className="section-header"><h2><Network size={18} /> 神经元知识网络</h2>
      <div className="actions"><button className="button" onClick={sync} disabled={!data?.enabled || busy || active}>
        <RefreshCw size={15} />{busy || active ? '正在同步…' : '同步到神经元'}</button>
        {data && <a className="button" href={data.panel_url} target="_blank" rel="noopener noreferrer">打开神经元脑图<ArrowUpRight size={15} /></a>}
      </div>
    </div>
    <div className="brain-sync-body"><p className="muted">同步已完成的非演示知识，保留原文、出处和解析版本；由 Personal Brain 建立自己的检索索引。</p>
    {data && <div className="meta"><span>{data.eligible_knowledge_count} 条知识可同步</span>
      <span>已确认 {data.jobs.reduce((sum, job) => sum + job.synced_documents, 0)} 份来源快照</span>
      {data.jobs[0] && <Badge tone={data.jobs[0].state === 'completed' ? 'success' : ''}>{stateName[data.jobs[0].state] ?? data.jobs[0].state}</Badge>}
      {!data.enabled && <span>本机同步尚未启用</span>}</div>}
    {message && <p role="status">{message}</p>}
    <ErrorBox error={failure || error || data?.jobs.find(job => ['blocked', 'waiting', 'exhausted'].includes(job.state))?.last_error_message || ''} /></div>
  </section>
}
