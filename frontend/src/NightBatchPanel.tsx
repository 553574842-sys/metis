import { useState } from 'react'
import { Moon, Square } from 'lucide-react'
import { Link } from 'react-router-dom'
import { post } from './api'
import { useData } from './hooks'
import { Badge, ErrorBox } from './components'

type Batch = {
  id: string; creator: string; phase: string; maximum: number; selected: number; completed: number
  knowledge_count: number; failed: number; brain_completed: number; brain_pending: number
  deadline: string; sleep_prevention_active: boolean; last_error_message: string | null
}

export function NightBatchPanel() {
  const { data, error, reload } = useData<{ items: Batch[] }>('/night-batches', 5000)
  const [busy, setBusy] = useState(false), [failure, setFailure] = useState('')
  const batch = data?.items[0]
  if (!batch) return error ? <ErrorBox error={error} /> : null
  const active = !['stopped', 'time_limit'].includes(batch.phase) && (batch.phase !== 'finished' || batch.brain_pending > 0)
  const phase = batch.phase === 'finished' && batch.brain_pending > 0 ? '知识已生成，脑图同步中' : ({ selecting: '正在选取视频', processing: '正在生成知识', paused: '需要处理后继续', stopped: '已停止', time_limit: '达到运行时限', finished: '视频处理完成' } as Record<string, string>)[batch.phase]
  async function stop() {
    setBusy(true); setFailure('')
    try { await post('/night-batches/' + batch!.id + '/stop'); reload() }
    catch (reason) { setFailure((reason as Error).message) }
    finally { setBusy(false) }
  }
  return <section className="panel brain-sync-panel" aria-label="夜间视频批次">
    <div className="section-header"><h2><Moon size={18} />夜间采集 · {batch.creator}</h2>
      <div className="actions"><Badge tone={batch.phase === 'finished' ? 'success' : ''}>{phase}</Badge>
        {active && <button className="button" disabled={busy} onClick={stop}><Square size={14} />停止本批次</button>}</div>
    </div>
    <div className="brain-sync-body"><p className="muted">本批次最多 {batch.maximum} 条新视频，已选 {batch.selected} 条；失败不补选第 {batch.maximum + 1} 条。</p>
      <div className="meta"><span>已完成 {batch.completed}/{batch.selected}</span><span>已生成 {batch.knowledge_count} 条知识</span>
        <span>失败 / 暂停 {batch.failed}</span><span>脑图已同步 {batch.brain_completed} 个作品</span></div>
      <p className="muted">运行截止：{new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Hong_Kong', dateStyle: 'short', timeStyle: 'short' }).format(new Date(batch.deadline))}
        {batch.sleep_prevention_active && ' · 运行期间防止自动休眠'}</p>
      {active && <p className="muted">停止会取消排队任务，当前处理中的任务可能会完成。保持电脑插电、联网，避免合盖或关机。</p>}
      <Link className="text-button" to="/system">查看任务与错误记录</Link>
      <ErrorBox error={failure || error || batch.last_error_message || ''} />
    </div>
  </section>
}
