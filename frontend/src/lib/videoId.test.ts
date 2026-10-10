import { describe, expect, it } from 'vitest'
import { biliPart, parseRoute, sourceOfId } from './videoId'

describe('sourceOfId', () => {
  it('recognises both id formats', () => {
    expect(sourceOfId('dQw4w9WgXcQ')).toBe('youtube')
    expect(sourceOfId('BV1Ex4y1G7nG_p3')).toBe('bilibili')
    expect(sourceOfId('BV1Ex4y1G7nG')).toBeNull()
    expect(sourceOfId('nope')).toBeNull()
  })
})

describe('parseRoute', () => {
  it('reads #/v/<id> for both sources', () => {
    expect(parseRoute('#/v/dQw4w9WgXcQ')).toBe('dQw4w9WgXcQ')
    expect(parseRoute('#/v/BV1Ex4y1G7nG_p12')).toBe('BV1Ex4y1G7nG_p12')
    expect(parseRoute('#/v/BV1Ex4y1G7nG_p0')).toBeNull()
    expect(parseRoute('#/')).toBeNull()
  })
})

describe('biliPart', () => {
  it('splits a Bilibili id', () => {
    expect(biliPart('BV1Ex4y1G7nG_p3')).toEqual({ bvid: 'BV1Ex4y1G7nG', part: 3 })
    expect(biliPart('dQw4w9WgXcQ')).toBeNull()
  })
})
