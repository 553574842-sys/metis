export type Creator = { id: string; display_name: string; platform: string; profile_url: string; tracking_state: string; history_scan_state: string; access_state: string; source_mode: string; work_count: number; next_check_at: string | null }
export type KnowledgeTag = { id: string; label: string; count?: number }
export type Evidence = { knowledge_item_id: string; work_id: string; creator_id: string; title: string; summary: string; creator: string; platform: string; source_url: string; evidence_excerpt: string; relevance: number; source_mode: string; analysis_mode: string; claim_kind: string; item_type: string; tags?: KnowledgeTag[]; learned_at?: string; image_asset_id?: string }
export type Work = { id: string; creator_id: string; creator: string; platform: string; title: string; body_text: string; hashtags: string[]; source_mode: string; analysis_mode: string; original_url: string; processing_state: string; content_type: string; published_at: string | null; identity_unverified: boolean }
export type Job = { id: string; work_id: string | null; job_type: string; state: string; attempt_count: number; next_retry_at: string | null; last_error_message: string | null; last_error_code: string | null }
export type Alert = { id: string; severity: string; message: string; category: string; created_at: string; read_at: string | null; resolved_at: string | null }
export type DailyReport = { id: string; report_date: string; revision: number; status: string; generated_at: string; partial_reason: string | null; content_zh: { summary_zh: string; highlights: Evidence[]; work_count: number; knowledge_count: number; relation_count: number; topics: string[]; contains_mock: boolean } }
export type Asset = { id: string; asset_type: string; source_order: number; url: string }
export type Segment = { id: string; segment_type: string; raw_text: string; zh_text: string; start_ms: number | null; image_asset_id: string | null }
export type WorkDetail = Work & { assets: Asset[]; segments: Segment[]; jobs: Job[]; temporary_video_present: boolean; analysis: { provider: string; output: { summary_zh: string; creator_thoughts: string[]; expression_style: string[]; action_steps: string[]; limitations: string[] } } | null; knowledge_items: { id: string; title: string; content_zh: string; content_original: string; claim_kind: string }[] }
export type Answer = { answer_zh: string; status: string; citations: Evidence[]; limitations: string[] }
export type Overview = { creator_count: number; tracking_count: number; work_count: number; knowledge_count: number; today_knowledge_count: number; states: Record<string, number>; unread_alerts: number; today: string; mode: string; recent_knowledge: Evidence[]; recent_works: Work[] }
export type Relation = { id: string; source_title: string; target_title: string; relation_type: string; source_url: string; target_url: string; evidence: { reason: string; claim_kind: string; source_excerpt: string; target_excerpt: string } }
export type Health = { task_mode: string; ai: { provider: string; status: string }; services: { name: string; status: string; detail?: string; last_seen_at?: string }[]; report_schedule: { hour: number; minute: number; timezone: string } }
export type Storage = { free_bytes: number; total_bytes: number; permanent_bytes: number; asset_count: number; pending_video_cleanup: number; backup_configured: boolean }
export type Adapter = { platform: string; mode: string; real_status: string; capabilities: Record<string, string>; message: string }

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'ApiError' }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const csrf = document.cookie.split('; ').find(x => x.startsWith('atlas_csrf='))?.split('=')[1] ?? ''
  const headers = new Headers(options.headers)
  if (!(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (options.method && options.method !== 'GET') headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  const response = await fetch('/api/v1' + path, { credentials: 'include', ...options, headers })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    if (response.status === 401 && !path.startsWith('/auth')) window.dispatchEvent(new Event('atlas:unauthorized'))
    throw new ApiError(data.error?.message ?? `请求失败（${response.status}）`, response.status)
  }
  return response.json()
}

export function post<T>(path: string, body?: unknown) { return api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }) }
export const platformName = (value: string) => value === 'douyin' ? '抖音' : '小红书'
export const stateName: Record<string, string> = { pending_capture: '待采集', capturing: '采集中', pending_analysis: '待解析', analyzing: '解析中', completed: '已完成', failed_retryable: '失败待重试', queued: '排队中', running: '运行中', waiting: '等待重试', exhausted: '重试耗尽', blocked: '等待配置 / 访问受限', canceled: '已取消', partial: '部分覆盖' }
export const sourceName = (value: string) => value === 'ai_reflection' ? 'AI 深入学习' : value === 'mock' ? 'Mock 演示' : value === 'manual' ? '人工导入' : '真实平台'
export function dateTime(value: string | null) { return value ? new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(value) ? value : value + 'Z')) : '—' }
export const bytes = (value: number) => value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(1)} GB` : `${(value / 1024 ** 2).toFixed(1)} MB`
