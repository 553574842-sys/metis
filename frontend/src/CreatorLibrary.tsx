import { Link } from 'react-router-dom'
import { ArrowUpRight, ChevronDown, Video } from 'lucide-react'
import type { Evidence, Work } from './api'
import { useData } from './hooks'
import { Empty, ErrorBox, EvidenceCard, Skeleton, Status } from './components'
import { pageNumber, usePageQuery } from './navigation'

export function CreatorLibrary({ id, kind }: { id: string; kind: 'works' | 'knowledge' }) {
  const [params, update] = usePageQuery()
  const offset = pageNumber(params.get('library_offset'))
  const path = kind === 'works' ? `/works?creator_id=${encodeURIComponent(id)}&offset=${offset}`
    : `/knowledge/search?creator_id=${encodeURIComponent(id)}&offset=${offset}&limit=12`
  const { data, error } = useData<{ items: (Work & Evidence)[]; total: number }>(path, 5000)
  const size = kind === 'works' ? 30 : 12
  return <div className="creator-library"><ErrorBox error={error} />{!data ? <Skeleton /> : <>
    {data.items.length ? kind === 'works' ? <ul className="creator-works">{data.items.map(work => <li key={work.id}>
      <Video size={17} /><Link to={`/works?work=${work.id}`}><strong>{work.title}</strong></Link>
      <Status value={work.processing_state} /><ArrowUpRight size={14} />
    </li>)}</ul> : <div className="creator-knowledge">{data.items.map(item => <details key={item.knowledge_item_id}>
      <summary>{item.title}<ChevronDown size={15} /></summary><EvidenceCard evidence={item} />
    </details>)}</div> : <Empty title={kind === 'works' ? '没有匹配的作品' : '未找到相关知识'}>
      {kind === 'works' ? '调整筛选条件，或导入自己的资料。' : '尝试更具体的术语，或导入相关资料。'}
    </Empty>}
    <div className="pagination"><span>共 {data.total} 条</span><button className="button small" disabled={!offset} onClick={() => update({ library_offset: Math.max(0, offset - size) })}>上一页</button>
      <button className="button small" disabled={offset + size >= data.total} onClick={() => update({ library_offset: offset + size })}>下一页</button></div>
  </>}</div>
}
