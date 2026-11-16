import { Link } from 'react-router-dom'
import { ArrowUpRight, BookOpen } from 'lucide-react'
import { dateTime, type Evidence } from './api'
import { EvidenceCard } from './components'
import { usePageQuery } from './navigation'
import { ReflectionPanel } from './ReflectionPanel'

export function KnowledgeBrowser({ items }: { items: Evidence[] }) {
  const [params, update] = usePageQuery()
  const selected = params.get('item') || ''
  const item = items.find(entry => entry.knowledge_item_id === selected) ?? items[0]
  if (!item) return null
  return <div className="knowledge-browser">
    <section className="knowledge-index" aria-label="知识列表">
      {items.map(entry => <button className="knowledge-list-item" key={entry.knowledge_item_id}
        aria-pressed={entry.knowledge_item_id === item.knowledge_item_id} onClick={() => update({ item: entry.knowledge_item_id })}>
        <span className="knowledge-list-meta"><BookOpen size={14} />{entry.creator}{entry.image_asset_id && ' · 图片'}</span>
        <strong>{entry.title}</strong><span className="knowledge-list-summary">{entry.summary}</span>
        {Boolean(entry.tags?.length) && <span className="knowledge-tags" aria-label="自动主题标签">{entry.tags!.map(tag => <span className="badge subtle" key={tag.id}>{tag.label}</span>)}</span>}
        {entry.learned_at && <time className="knowledge-learned-at" dateTime={entry.learned_at}>入库 {dateTime(entry.learned_at)}</time>}
      </button>)}
    </section>
    <section className="knowledge-reader" aria-label="知识详情">
      <EvidenceCard key={item.knowledge_item_id} evidence={item} />
      <ReflectionPanel key={item.work_id} workId={item.work_id} />
      <Link className="text-link knowledge-work-link" to={`/works?work=${item.work_id}${item.image_asset_id ? '&work_tab=original' : ''}`}>作品与解析资料<ArrowUpRight size={14} /></Link>
    </section>
  </div>
}
