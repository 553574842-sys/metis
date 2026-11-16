import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, renderHook, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import App from './App'
import { useData } from './hooks'
import { api, ApiError } from './api'

vi.mock('./api', async original => ({ ...await original<typeof import('./api')>(), api: vi.fn() }))
vi.mock('./LearningPage', () => ({ LearningPage: () => <p>学习页面已连接</p> }))

const flush = async () => { await act(async () => { await Promise.resolve() }) }
beforeEach(() => { vi.useFakeTimers(); vi.mocked(api).mockReset() })
afterEach(() => { cleanup(); vi.clearAllTimers(); vi.useRealTimers() })

describe('读取和连接自动恢复', () => {
  it('慢读取不会被五秒轮询叠加，完成后才安排下一次读取', async () => {
    let finish!: (value: unknown) => void
    vi.mocked(api).mockReturnValueOnce(new Promise(resolve => { finish = resolve }))
    const { result } = renderHook(() => useData('/learning/status', 5000))
    await act(async () => { vi.advanceTimersByTime(15000) })
    expect(api).toHaveBeenCalledTimes(1)
    await act(async () => { finish({ count: 120 }) })
    expect(result.current.data).toEqual({ count: 120 })
    vi.mocked(api).mockResolvedValue({ count: 121 })
    await act(async () => { vi.advanceTimersByTime(5000) })
    expect(api).toHaveBeenCalledTimes(2)
    expect(result.current.data).toEqual({ count: 121 })
  })

  it('一次读取失败后也会重连，保留旧数据，窗口恢复焦点时重新读取', async () => {
    vi.mocked(api).mockResolvedValueOnce({ count: 120 }).mockRejectedValueOnce(new TypeError('network offline'))
      .mockResolvedValue({ count: 121 })
    const { result } = renderHook(() => useData('/creators'))
    await flush()
    await act(async () => { window.dispatchEvent(new Event('focus')) })
    expect(result.current.data).toEqual({ count: 120 })
    expect(result.current.error).toBe('network offline')
    await act(async () => { vi.advanceTimersByTime(5000) })
    expect(result.current.data).toEqual({ count: 121 })
    expect(result.current.error).toBe('')
  })

  it('切换检索词时清除旧结果，新请求失败也不会显示旧结果', async () => {
    vi.mocked(api).mockResolvedValueOnce({ total: 4785 })
    const { result, rerender } = renderHook(({ path }) => useData(path, 0, true, false),
      { initialProps: { path: '/knowledge/search?q=AI' } })
    await flush()
    expect(result.current.data).toEqual({ total: 4785 })
    vi.mocked(api).mockRejectedValueOnce(new DOMException('Timed out', 'AbortError'))
    rerender({ path: '/knowledge/search?q=logo' })
    expect(result.current.data).toBeNull()
    await flush()
    expect(result.current.data).toBeNull()
    expect(result.current.error).toContain('读取超时')
    vi.mocked(api).mockResolvedValueOnce({ total: 93 })
    await act(async () => { vi.advanceTimersByTime(5000) })
    expect(result.current.data).toEqual({ total: 93 })
  })

  it('参数错误不自动重复请求，卸载会中止在途读取', async () => {
    vi.mocked(api).mockRejectedValueOnce(new ApiError('invalid filter', 422))
    const first = renderHook(() => useData('/works', 5000))
    await flush()
    await act(async () => { vi.advanceTimersByTime(30000) })
    expect(api).toHaveBeenCalledTimes(1)
    first.unmount()
    vi.mocked(api).mockReturnValue(new Promise(() => {}))
    const second = renderHook(() => useData('/creators'))
    const signal = vi.mocked(api).mock.calls.at(-1)![1]!.signal!
    second.unmount()
    expect(signal.aborted).toBe(true)
  })

  it('初次打开时后台短暂不可用，会自动恢复而不切到密码页', async () => {
    vi.mocked(api).mockRejectedValueOnce(new TypeError('offline')).mockResolvedValue({ name: 'local', source_mode: 'minimax', local_passwordless: true })
    render(<MemoryRouter initialEntries={['/learning']}><App /></MemoryRouter>)
    await flush()
    expect(screen.getByText('后台连接中断，正在自动重连…')).toBeInTheDocument()
    expect(screen.queryByLabelText('管理员密码')).not.toBeInTheDocument()
    await act(async () => { vi.advanceTimersByTime(5000) })
    expect(screen.getByText('学习页面已连接')).toBeInTheDocument()
  })

  it('会话失效时合并并行重连，续期后通知页面重新读取', async () => {
    const profile = { name: 'local', source_mode: 'minimax', local_passwordless: true }
    vi.mocked(api).mockResolvedValueOnce(profile)
    render(<MemoryRouter initialEntries={['/learning']}><App /></MemoryRouter>)
    await flush()
    let fail!: (reason: Error) => void
    vi.mocked(api).mockReturnValueOnce(new Promise((_resolve, reject) => { fail = reject }))
      .mockResolvedValueOnce({}).mockResolvedValueOnce(profile)
    const restored = vi.fn()
    window.addEventListener('atlas:session-restored', restored)
    await act(async () => {
      window.dispatchEvent(new Event('atlas:unauthorized'))
      window.dispatchEvent(new Event('atlas:unauthorized'))
      window.dispatchEvent(new Event('atlas:unauthorized'))
    })
    expect(api).toHaveBeenCalledTimes(2)
    await act(async () => { fail(new ApiError('expired', 401)) })
    expect(api).toHaveBeenCalledTimes(4)
    expect(restored).toHaveBeenCalledTimes(1)
    expect(screen.getByText('学习页面已连接')).toBeInTheDocument()
    window.removeEventListener('atlas:session-restored', restored)
  })
})
