import { describe, expect, it } from 'vitest'
import { refreshDelayMs } from './retry'

describe('refreshDelayMs', () => {
  it('backs off 1s, 2s, 4s and caps at 8s', () => {
    expect([1, 2, 3, 4, 5].map(refreshDelayMs)).toEqual([1000, 2000, 4000, 8000, 8000])
  })
})
