import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'

export function pageNumber(value: string | null) {
  const number = Number(value)
  return Number.isSafeInteger(number) && number >= 0 && number <= 1_000_000 ? number : 0
}

export function usePageQuery() {
  const [params, setParams] = useSearchParams()
  function update(values: Record<string, string | number | null>, replace = false) {
    const next = new URLSearchParams(params)
    for (const [key, value] of Object.entries(values)) {
      if (value === null || value === '' || value === 0) next.delete(key)
      else next.set(key, String(value))
    }
    if (next.toString() !== params.toString()) setParams(next, { replace })
  }
  return [params, update] as const
}

export function WorkspaceBack() {
  const location = useLocation(), navigate = useNavigate()
  const index = window.history.state?.idx
  const available = typeof index === 'number' ? index > 0 : location.key !== 'default'
  return <div className="workspace-history"><button className="text-button" disabled={!available}
    onClick={() => navigate(-1)}><ArrowLeft size={15} />返回上一步</button></div>
}
