import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('api error messages', () => {
  it('explains an unreachable backend when the dev proxy answers 502', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('<html>Bad Gateway</html>', { status: 502 })),
    )
    await expect(api.getVideo('x')).rejects.toThrow('无法连接到后端服务，请确认后端已启动')
  })

  it('uses the backend message from a JSON error body', async () => {
    const body = JSON.stringify({ detail: { code: 'NOT_FOUND', message: '视频不存在' } })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(body, { status: 404, headers: { 'Content-Type': 'application/json' } }),
      ),
    )
    await expect(api.getVideo('x')).rejects.toThrow('视频不存在')
  })
})
