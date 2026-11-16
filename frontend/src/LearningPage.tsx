import { useState } from 'react'
import './learning.css'
import { Link } from 'react-router-dom'
import { ArrowUpRight, BookOpen, Check, ChevronDown, Clock3, Image as ImageIcon, MoreHorizontal, Pause, Play, Radio, RefreshCw, ScanEye, Search, Video } from 'lucide-react'
import type { Evidence } from './api'
import { stateName } from './api'
import { useData } from './hooks'
import { Badge, Empty, ErrorBox, EvidenceCard, PageTitle, Skeleton, SourceLink } from './components'
import { pageNumber, usePageQuery } from './navigation'

type LearningWork = {
  id?: string | null; platform_work_id: string; creator_id: string; creator: string; title: string
  content_type: string; source_url: string; state?: string; knowledge_count?: number; brain_state?: string
  next_retry_at?: string | null; error?: string | null
}
export type LearningStatus = {
  generated_at: string; refresh_seconds: number
  run: { id: string; title: string; phase: string; phase_label: string; snapshot_at: string | null; has_inventory: boolean; record_warning: string | null }
  runs: { id: string; title: string }[]
  creators: { id: string; name: string; profile_url: string; inventoried: number; videos: number; images: number; selected: number; completed: number; knowledge_count: number }[]
  counts: { inventoried: number; reviewed: number; review_total: number; pending_review: number; excluded: number; selected: number; completed: number; failed: number; knowledge: number; brain_completed: number; brain_total: number }
  activities: (LearningWork & { stage: string; live: boolean; observed_at: string; sync_documents?: number; job_id: string | null })[]
  works: LearningWork[]; pending_review: LearningWork[]
  knowledge: { items: (Evidence & { learned_at: string; work_title: string })[]; total: number; offset: number; limit: number }
}

const clock = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Hong_Kong', hour: '2-digit', minute: '2-digit', second: '2-digit' })
const when = (value: string) => clock.format(new Date(/[Zz]$|[+-]\d{2}:\d{2}$/.test(value) ? value : value + 'Z'))
const syncName: Record<string, string> = { not_started: '等待知识解析', queued: '同步排队中', running: '同步中', waiting: '同步待重试', completed: '已同步', blocked: '同步需处理', exhausted: '同步需处理' }

function waitingStage(work: LearningWork) {
  if (work.state === 'pending_capture') return '等待采集素材'
  if (work.state === 'pending_analysis') return '等待内容解析'
  if (work.state === 'failed_retryable' && work.next_retry_at) return '等待处理重试'
  if (work.brain_state === 'queued') return '等待同步到脑图'
  if (work.brain_state === 'waiting') return '等待脑图同步重试'
  return ''
}

function ProgressRow({ title, done, total, detail }: { title: string; done: number; total: number; detail: string }) {
  return <div className="learning-progress-row"><div><span>{title}</span><b>{done}<span> / {total}</span></b></div>
    <progress aria-label={title} value={Math.min(done, total)} max={Math.max(1, total)} />
    <p>{detail}</p></div>
}

function TaskQueue({ running, waiting, attention, finished }: {
  running: LearningStatus['activities']; waiting: LearningWork[]; attention: LearningWork[]; finished: LearningWork[]
}) {
  const [params, update] = usePageQuery()
  const view = ['queue', 'attention', 'done'].includes(params.get('queue') || '') ? params.get('queue')! : 'queue'
  const page = pageNumber(params.get('queue_page'))
  const queue = [...running.map(w => ({ ...w, stage: w.stage })), ...waiting.map(w => ({ ...w, stage: waitingStage(w) }))]
  const rows = view === 'queue' ? queue : view === 'attention'
    ? attention.map(w => ({ ...w, stage: ['blocked', 'exhausted'].includes(w.brain_state ?? '') ? '脑图同步需处理' : '解析需处理' }))
    : finished.map(w => ({ ...w, stage: w.brain_state === 'completed' ? '已入库 · 脑图已同步' : '知识已入库' }))
  const offset = Math.min(page * 8, Math.max(0, Math.ceil(rows.length / 8) - 1) * 8)
  return <section className="panel learning-tasks" aria-label="学习任务队列">
    <div className="section-header"><h2><Clock3 size={18} />任务队列</h2></div>
    <div className="learning-task-toolbar"><div className="tabs" role="tablist" aria-label="队列状态">
      {([['queue', `队列 ${queue.length}`], ['attention', `需处理 ${attention.length}`], ['done', `已完成 ${finished.length}`]] as const).map(([value, label]) =>
        <button key={value} id={`queue-tab-${value}`} role="tab" aria-controls="learning-task-panel" aria-selected={view === value} className={view === value ? 'selected' : ''}
          onClick={() => update({ queue: value, queue_page: 0 })}>{label}</button>)}
    </div><span className="learning-task-counts">处理中 {running.length}<span>·</span>等待 {waiting.length}</span></div>
    <div id="learning-task-panel" role="tabpanel" aria-labelledby={`queue-tab-${view}`}>
      {rows.length ? <ul className="learning-task-list">{rows.slice(offset, offset + 8).map(w => <li key={w.id ?? w.platform_work_id} className={`learning-task-row${running.some(a => a.id === w.id && a.platform_work_id === w.platform_work_id) ? ' is-running' : ''}`}>
        <span className="learning-task-icon" aria-hidden="true">{view === 'done' ? <Check size={17} /> : view === 'attention' ? <Pause size={17} /> : running.some(a => a.id === w.id && a.platform_work_id === w.platform_work_id) ? <Radio size={17} /> : <Clock3 size={17} />}</span>
        <div className="learning-task-main"><h3 title={w.title}>{w.id ? <Link to={`/works?work=${w.id}`}>{w.title}</Link> : w.title}</h3>
          <div className="meta"><span>{w.creator}</span><Badge tone={view === 'attention' ? 'warning' : view === 'done' ? 'success' : ''}>{w.stage}</Badge>
            {view === 'done' && <span>{w.knowledge_count ?? 0} 条知识</span>}
            {view === 'queue' && w.next_retry_at && <span>下次重试 {when(w.next_retry_at)}</span>}</div>
          {view === 'attention' && w.error && <details className="learning-task-error"><summary>查看原因</summary><p>{w.error}</p></details>}
        </div>{w.id ? <Link className="icon-button" aria-label={`查看作品：${w.title}`} to={`/works?work=${w.id}`}><ArrowUpRight size={16} /></Link> : <SourceLink url={w.source_url} />}
      </li>)}</ul> : <div className="learning-task-empty"><Clock3 size={22} /><strong>{view === 'queue' ? '当前没有处理或等待的任务' : view === 'attention' ? '当前没有需处理的任务' : '完成的作品会显示在这里'}</strong>
        <span>{view === 'queue' && attention.length ? `${attention.length} 条任务已暂停，可在“需处理”中查看。` : '发送新链接后，任务会自动更新。'}</span></div>}
      {rows.length > 8 && <div className="pagination"><span>{offset + 1}–{Math.min(offset + 8, rows.length)} / {rows.length}</span><button className="button small" disabled={!offset} onClick={() => update({ queue_page: Math.max(0, offset / 8 - 1) })}>上一页</button><button className="button small" disabled={offset + 8 >= rows.length} onClick={() => update({ queue_page: offset / 8 + 1 })}>下一页</button></div>}
    </div>
  </section>
}

export function LearningPage() {
  const [page, updatePage] = usePageQuery()
  const run = page.get('run') || 'all', creator = page.get('creator') || '', q = page.get('q') || ''
  const offset = pageNumber(page.get('offset'))
  const tab = ['knowledge', 'works', 'review'].includes(page.get('tab') || '') ? page.get('tab')! : 'knowledge'
  const [paused, setPaused] = useState(false)
  const params = new URLSearchParams({ run_id: run, creator_id: creator, q, offset: String(offset), limit: '12' })
  const { data, error, loading, reload } = useData<LearningStatus>('/learning/status?' + params, paused ? 0 : 5000, !paused)
  const counts = data?.counts
  const filteredWorks = data?.works.filter(w => (!creator || w.creator_id === creator) && (!q || w.title.toLowerCase().includes(q.toLowerCase()))) ?? []
  const pending = data?.pending_review.filter(w => (!creator || w.creator_id === creator) && (!q || w.title.toLowerCase().includes(q.toLowerCase()))) ?? []
  const runningIds = new Set(data?.activities.filter(a => a.live).map(a => a.id))
  const waiting = data?.works.filter(w => !runningIds.has(w.id) && waitingStage(w)) ?? []
  const attention = data?.works.filter(w => !runningIds.has(w.id) &&
    (w.state === 'failed_retryable' && !w.next_retry_at || ['blocked', 'exhausted'].includes(w.brain_state ?? ''))) ?? []
  const unfinishedIds = new Set([...waiting, ...attention].map(w => w.id))
  const finished = data?.works.filter(w => w.state === 'completed' && !runningIds.has(w.id) && !unfinishedIds.has(w.id)) ?? []
  return <div className="learning-page">
    <PageTitle title="学习进度" description="当前在处理什么，以及已经留下了哪些知识。" action={<div className="actions"><Link className="button" to="/knowledge?tab=ask"><BookOpen size={15} />问知识库</Link><Link className="button primary" to="/inbox">添加链接<ArrowUpRight size={14} /></Link>
      <details className="action-menu"><summary aria-label="学习面板操作"><MoreHorizontal size={20} /></summary><div className="action-menu-body">
      <button className="button" aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? <Play size={15} /> : <Pause size={15} />}{paused ? '恢复自动刷新' : '暂停刷新'}</button>
      <button className="button" onClick={reload} disabled={loading}><RefreshCw size={15} className={loading ? 'learning-spin' : ''} />刷新</button>
</div></details>
    </div>} />
    <ErrorBox error={error ? `${error}。${data ? '当前保留上次成功读取的记录。' : '请稍后重试。'}` : ''} />
    {paused && <div className="notice" role="status">已暂停面板刷新，后台学习任务继续运行。</div>}
    {!data || !counts ? <Skeleton /> : <>
      <div className="learning-toolbar"><label>学习任务<select aria-label="选择学习任务" value={data.run.id} onChange={e => updatePage({ run: e.target.value, creator: '', offset: 0 })}>
        {data.runs.map(r => <option value={r.id} key={r.id}>{r.title}</option>)}</select></label>
        <div className="learning-freshness"><span className={paused || error ? 'learning-status-idle' : 'online-dot'} />
          {error ? '显示上次记录' : paused ? '自动刷新已暂停' : '每 5 秒自动更新'}<span>· {when(data.generated_at)}</span></div></div>
      {data.run.record_warning && <div className="notice">{data.run.record_warning}</div>}
      <TaskQueue running={data.activities.filter(a => a.live)} waiting={waiting} attention={attention} finished={finished} />
      <details className="panel learning-disclosure" open={page.has('stats')} onToggle={e => updatePage({ stats: e.currentTarget.open ? '1' : null }, true)}><summary><span>学习统计</span><span>{counts.completed} / {counts.selected} 条已入库<ChevronDown size={16} /></span></summary>
        <section className="learning-progress" aria-label="分阶段学习进度"><div className="section-header"><h2>分阶段进度</h2><span className="muted text-sm">{counts.knowledge} 条知识已入库</span></div>
          <div className="learning-progress-body">
            {data.run.has_inventory && <ProgressRow title="视频转写核对" done={counts.reviewed} total={counts.review_total} detail={counts.pending_review ? `${counts.pending_review} 条仍待画面确认；已排除 ${counts.excluded} 条不相关内容` : `已排除 ${counts.excluded} 条不相关内容`} />}
            <ProgressRow title="作品解析入库" done={counts.completed} total={counts.selected} detail={`已确认 ${counts.selected} 条相关作品${counts.failed ? `，${counts.failed} 条等待重试或处理` : '，解析后生成可检索知识'}`} />
            <ProgressRow title="神经元同步" done={counts.brain_completed} total={counts.brain_total} detail="按当前解析版本统计已同步作品" />
            <p className="learning-progress-note">筛选确认新作品后，解析总数会增加。转写、知识入库和神经元同步分别计数。</p>
          </div>
        </section>
      {data.activities.some(a => !a.live) && <div className="learning-recent">{data.activities.filter(a => !a.live).map(a => <p key={a.platform_work_id}><Badge>最近记录</Badge> {a.title} · {a.stage}</p>)}</div>}
      {data.creators.length > 0 && <section aria-label="博主学习情况"><div className="section-header"><h2>正在学习的博主</h2><span className="muted text-sm">{data.creators.length} 位博主</span></div>
        <div className="learning-creators">{data.creators.map(c => <div className="learning-creator-row" key={c.id}>
          <span className="creator-avatar">{c.name.slice(0, 1)}</span><div className="learning-creator-name"><h3>{c.name}</h3><p>{c.inventoried ? `已读取 ${c.videos} 条视频、${c.images} 条图文目录` : '已收录的作品与知识'}</p></div>
          <div className="learning-creator-totals"><span>已解析 <b>{c.completed}/{c.selected}</b></span><span>知识 <b>{c.knowledge_count}</b> 条</span></div>
          <button className="text-button" onClick={() => updatePage({ creator: c.id, q: '', offset: 0, results: '1' })}>查看收获<ArrowUpRight size={14} /></button>
        </div>)}</div></section>}
      </details>
      <details className="panel learning-results learning-disclosure" aria-label="学习成果" open={page.has('results')} onToggle={e => updatePage({ results: e.currentTarget.open ? '1' : null }, true)}><summary><span><BookOpen size={17} />学习成果</span><span>{counts.knowledge} 条知识<ChevronDown size={16} /></span></summary>
        <div className="section-header"><p>知识保留原文依据，可回到对应作品。</p><Link className="text-link" to="/knowledge">知识中心<ArrowUpRight size={14} /></Link></div>
        <div className="learning-results-controls"><div className="tabs" role="tablist" aria-label="学习内容">
          {([['knowledge', `已生成知识 ${counts.knowledge}`], ['works', `作品清单 ${counts.selected}`], ['review', `待确认 ${counts.pending_review}`]] as const).map(([value, label]) =>
            <button key={value} id={`learning-tab-${value}`} role="tab" aria-controls={`learning-panel-${value}`} aria-selected={tab === value} className={tab === value ? 'selected' : ''} onClick={() => updatePage({ tab: value })}>{label}</button>)}
        </div><div className="filter-bar learning-results-filter"><select aria-label="筛选学习博主" value={creator} onChange={e => updatePage({ creator: e.target.value, offset: 0 })}><option value="">全部博主</option>{data.creators.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
          <label className="search-input"><Search size={16} /><input aria-label="搜索学习内容" placeholder={tab === 'knowledge' ? '搜索已学知识…' : '搜索作品标题…'} value={q} onChange={e => updatePage({ q: e.target.value, offset: 0 }, true)} /></label>
          {(creator || q) && <button className="text-button" onClick={() => updatePage({ creator: '', q: '', offset: 0 })}>清除筛选</button>}</div></div>
        <div role="tabpanel" id={`learning-panel-${tab}`} aria-labelledby={`learning-tab-${tab}`}>
          {tab === 'knowledge' && <>{data.knowledge.items.length ? <div className="learning-knowledge-grid">{data.knowledge.items.map(item => <details className="learning-knowledge-entry" key={item.knowledge_item_id}>
            <summary><span>{item.title}</span><ChevronDown size={15} /></summary><EvidenceCard evidence={item} /><div className="learning-knowledge-origin"><Link to={`/works?work=${item.work_id}`}>来自作品：{item.work_title}</Link><span><Check size={12} />{when(item.learned_at)} 已入库</span></div>
          </details>)}</div> : <Empty title={creator || q ? '没有匹配的已学知识' : '知识生成后会显示在这里'}>处理中的作品可在“作品清单”查看。</Empty>}
            <div className="pagination"><span>共 {data.knowledge.total} 条匹配知识 · 每页 12 条</span><button className="button small" disabled={!offset} onClick={() => updatePage({ offset: Math.max(0, offset - 12) })}>上一页</button><button className="button small" disabled={offset + 12 >= data.knowledge.total} onClick={() => updatePage({ offset: offset + 12 })}>下一页</button></div></>}
          {tab === 'works' && (filteredWorks.length ? <div className="learning-work-list">{filteredWorks.map(w => <article key={w.id} className="learning-work-row"><div className="learning-work-main"><div className="meta">{w.content_type === 'image' ? <ImageIcon size={14} /> : <Video size={14} />}{w.creator}</div><h3><Link to={`/works?work=${w.id}`}>{w.title}</Link></h3>
            <div className="meta"><span>{w.knowledge_count} 条知识</span><span>·</span><span>{syncName[w.brain_state ?? 'not_started'] ?? w.brain_state}</span></div>{w.error && <p className="learning-work-error">{w.error}{w.next_retry_at && ` 下次重试 ${when(w.next_retry_at)}`}</p>}</div>
            <Badge tone={w.state === 'completed' ? 'success' : w.state === 'failed_retryable' ? 'warning' : ''}>{stateName[w.state ?? ''] ?? w.state}</Badge><Link className="text-link" to={`/works?work=${w.id}`}>资料与知识<ArrowUpRight size={14} /></Link></article>)}</div> : <Empty title="没有匹配的作品">调整筛选条件后重试。</Empty>)}
          {tab === 'review' && (pending.length ? <div className="learning-work-list">{pending.map(w => <article key={w.platform_work_id} className="learning-work-row"><div className="learning-work-main"><div className="meta"><ScanEye size={14} />{w.creator}</div><h3>{w.title}</h3><p className="muted text-sm">已核对转写，仍需结合画面判断相关性。</p></div><Badge>待确认</Badge><SourceLink url={w.source_url} /></article>)}</div> : <Empty title={counts.pending_review ? '没有匹配的待确认作品' : '当前没有待确认作品'}>行业不明确的内容会单独列在这里。</Empty>)}
        </div>
      </details>
      <p className="learning-bottom-note"><Clock3 size={14} />{data.run.snapshot_at ? `任务记录更新于 ${when(data.run.snapshot_at)}；` : ''}面板仅查询状态，不会重复发起模型解析。<Link to="/system">查看错误与重试记录<ArrowUpRight size={13} /></Link></p>
    </>}
  </div>
}
