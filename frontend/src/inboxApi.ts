export class InboxError extends Error {
  constructor(message: string, public status: number) { super(message) }
}

export async function inboxApi<T>(path: string, body?: unknown): Promise<T> {
  const cookies = new Map(document.cookie.split('; ').map(item => {
    const [name, ...value] = item.split('=')
    return [name, value.join('=')] as [string, string]
  }))
  const local = ['127.0.0.1', 'localhost', '[::1]'].includes(location.hostname) && cookies.has('atlas_csrf')
  const csrf = cookies.get(local ? 'atlas_csrf' : 'atlas_inbox_csrf') ?? ''
  const response = await fetch('/api/inbox' + path, {
    method: body === undefined ? 'GET' : 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(body === undefined ? {} : { 'X-CSRF-Token': decodeURIComponent(csrf) }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  }).catch(() => { throw new InboxError('连接不到电脑，请确认电脑已开机、服务运行且 Tailscale 已连接。', 0) })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new InboxError(data.error?.message ?? data.detail ?? `请求失败（${response.status}）`, response.status)
  return data
}

export type InboxSession = { name: string; local: boolean; expires_at: string }
export type InboxWork = {
  id: string; submission_id: string; title: string; creator: string; source_url: string; state: string; stage: string
  submitted_at: string; knowledge_count: number; capture_done: boolean; knowledge_done: boolean; brain_state: string
  error: string | null; next_retry_at: string | null
}
export type InboxDetail = InboxWork & { knowledge: { id: string; title: string; summary: string; evidence: string; claim_kind: string }[] }
