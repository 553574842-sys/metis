import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ArrowUpRight, FileSearch, X, AlertCircle, Info } from 'lucide-react'
import { platformName, sourceName, stateName } from './api'
import type { Evidence } from './api'

export function Badge({ children, tone = '' }: { children: ReactNode; tone?: string }) { return <span className={`badge ${tone}`}>{children}</span> }
export function Status({ value }: { value: string }) { return <Badge tone={['completed', 'healthy'].includes(value) ? 'success' : ['failed_retryable', 'exhausted', 'unavailable', 'blocked'].includes(value) ? 'warning' : ''}>{stateName[value] ?? value}</Badge> }
export function Source({ value }: { value: string }) { return <Badge tone={value === 'mock' ? 'mock' : ''}>{sourceName(value)}</Badge> }
export function SourceLink({ url, mode }: { url: string; mode?: string }) {
  if (!url || !/^https?:\/\//i.test(url)) return <span className="muted text-sm">来源待补充</span>
  return <a href={url} target="_blank" rel="noopener noreferrer" className="source-link">{mode === 'mock' ? '模拟出处（不可访问）' : '打开原作品'}<ArrowUpRight size={14} /></a>
}
export function Empty({ title, children }: { title: string; children?: ReactNode }) { return <div className="empty"><FileSearch size={32} strokeWidth={1.4} /><h3>{title}</h3><div className="muted">{children}</div></div> }
export function ErrorBox({ error }: { error: string }) { return error ? <div className="error" role="alert"><AlertCircle size={17} />{error}</div> : null }
export function Skeleton() { return <div className="skeleton" role="status" aria-label="正在加载"><span /><span /><span /></div> }
export function PageTitle({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <div className="page-title"><div className="page-title-copy"><h1>{title}</h1>
    <details className="page-help"><summary aria-label={`${title}说明`}><Info size={17} /></summary><p>{description}</p></details>
  </div>{action}</div>
}
export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { dialog.current?.showModal(); const el = dialog.current; return () => el?.close() }, [])
  return <dialog ref={dialog} onCancel={onClose} onClick={e => { if (e.target === dialog.current) onClose() }}><div className="modal-header"><h2>{title}</h2><button className="icon-button" aria-label="关闭" onClick={onClose}><X size={20} /></button></div>{children}</dialog>
}
export function EvidenceCard({ evidence }: { evidence: Evidence }) {
  const [expanded, setExpanded] = useState(false)
  const imageUrl = evidence.image_asset_id ? '/api/v1/assets/' + encodeURIComponent(evidence.image_asset_id) : ''
  return <article className="evidence-card">
    <div className="meta"><span className={`platform-dot ${evidence.platform}`} />{platformName(evidence.platform)}<span>·</span>{evidence.creator}<Source value={evidence.source_mode} /></div>
    <h3>{evidence.title}</h3>
    {Boolean(evidence.tags?.length) && <div className="knowledge-tags" aria-label="自动主题标签">{evidence.tags!.map(tag => <Badge key={tag.id} tone="subtle">{tag.label}</Badge>)}</div>}
    {imageUrl && <a className="evidence-image" href={imageUrl} target="_blank" rel="noopener noreferrer" aria-label="打开命中的原图">
      <img src={imageUrl} loading="lazy" alt={evidence.title} />
    </a>}
    <p>{evidence.summary}</p>
    <div className="card-footer">
      <Badge tone="subtle">{imageUrl ? '图片识别' : evidence.claim_kind === 'inference' ? 'AI 推论' : '原文观点'}</Badge>
      {evidence.item_type === 'reflection' && <Badge tone="subtle">待验证 · 未联网核实</Badge>}
      {evidence.analysis_mode === 'mock' && <Badge tone="mock">Mock 解析</Badge>}
      <button className="text-button" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>
        {expanded ? '收起依据' : imageUrl ? '查看识别依据' : '查看原文依据'}
      </button>
      <SourceLink url={evidence.source_url} mode={evidence.source_mode} />
    </div>
    {expanded && <blockquote>{evidence.evidence_excerpt}</blockquote>}
  </article>
}
