import { NavLink } from 'react-router-dom'
import { Activity, BrainCircuit, FileText, GraduationCap, Library, PanelsTopLeft, Smartphone, UsersRound } from 'lucide-react'

const groups = [
  { label: '学习', items: [
    { path: '/learning', label: '学习进度', icon: GraduationCap },
    { path: '/inbox', label: '学习收件箱', icon: Smartphone },
  ] },
  { label: '资料与知识', items: [
    { path: '/accounts', label: '账号管理', icon: UsersRound },
    { path: '/works', label: '作品资料库', icon: Library },
    { path: '/knowledge', label: 'AI 知识中心', icon: BrainCircuit },
    { path: '/reports', label: '每日分析报告', icon: FileText },
  ] },
  { label: '辅助入口', items: [
    { path: '/', label: '总览看板', icon: PanelsTopLeft },
    { path: '/system', label: '系统运行监控', icon: Activity },
  ] },
]

export function WorkspaceNavigation({ onNavigate }: { onNavigate: () => void }) {
  return <nav aria-label="主导航">{groups.map(group => <div className="nav-group" key={group.label}>
    <div className="nav-label">{group.label}</div>
    {group.items.map(({ path, label, icon: Icon }) => <NavLink key={path} to={path} end={path === '/'} onClick={onNavigate}>
      <Icon size={18} strokeWidth={1.7} /><span>{label}</span>
    </NavLink>)}
  </div>)}</nav>
}
