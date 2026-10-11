import { describe, expect, it } from 'vitest'
import { formatTime, linkifyTimestamps, normalizeMathDelimiters, seekSecondsFromHref } from './timestamps'

describe('formatTime', () => {
  it('formats mm:ss and h:mm:ss', () => {
    expect(formatTime(0)).toBe('00:00')
    expect(formatTime(59.9)).toBe('00:59')
    expect(formatTime(215)).toBe('03:35')
    expect(formatTime(3725)).toBe('1:02:05')
  })
})

describe('linkifyTimestamps', () => {
  it('turns [mm:ss] and [h:mm:ss] into seek links', () => {
    expect(linkifyTimestamps('see [03:35] and [1:02:05]')).toBe('see [03:35](#t=215) and [1:02:05](#t=3725)')
  })

  it('leaves existing links alone', () => {
    expect(linkifyTimestamps('[03:35](#t=215)')).toBe('[03:35](#t=215)')
  })

  it('ignores inline code and fenced code', () => {
    const md = 'run `arr[03:35]` and\n```\nx = a[1:02:05]\n```\n'
    expect(linkifyTimestamps(md)).toBe(md)
  })

  it('links time ranges like [03:02-04:02] to their start', () => {
    expect(linkifyTimestamps('见 [03:02-04:02] 和 [1:00:00–1:00:30] 与 [00:10 ~ 00:20]')).toBe(
      '见 [03:02-04:02](#t=182) 和 [1:00:00–1:00:30](#t=3600) 与 [00:10 ~ 00:20](#t=10)',
    )
  })

  it('ignores brackets that are not times', () => {
    expect(linkifyTimestamps('[1:99] [note]')).toBe('[1:99] [note]')
  })
})

describe('seekSecondsFromHref', () => {
  it('reads #t= links only', () => {
    expect(seekSecondsFromHref('#t=215')).toBe(215)
    expect(seekSecondsFromHref('#t=1.5')).toBe(1.5)
    expect(seekSecondsFromHref('https://example.com')).toBeNull()
  })
})

describe('normalizeMathDelimiters', () => {
  it('converts \\( \\) and \\[ \\] to dollar delimiters outside code', () => {
    expect(normalizeMathDelimiters('so \\(x^2\\) and \\[a+b\\]')).toBe('so $x^2$ and $$a+b$$')
    expect(normalizeMathDelimiters('`\\(x\\)`')).toBe('`\\(x\\)`')
  })
})
