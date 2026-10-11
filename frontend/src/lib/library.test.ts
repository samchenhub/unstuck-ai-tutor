import { describe, expect, it } from 'vitest'
import type { Video } from './api'
import { bilibiliLoginWarning, groupLibrary, thumbnailFor } from './library'

function video(over: Partial<Video>): Video {
  return {
    id: 'dQw4w9WgXcQ', url: '', title: 'T', channel: 'C', duration: 60, status: 'ready', ask_ready: true,
    stage_states: {}, visual_mode: 'full', outline: null, error_code: null, error_message: null, created_at: '',
    source: 'youtube', course_id: null, part: null, part_title: '', parts_total: 1, thumbnail: '', transcribed_until: 0, auto_retry_at: 0, auto_retries: 0,
    ...over,
  }
}

describe('groupLibrary', () => {
  it('groups parts of one course into a card placed where its newest part is', () => {
    const p3 = video({ id: 'BV1Ex4y1G7nG_p3', source: 'bilibili', course_id: 'BV1Ex4y1G7nG', part: 3, parts_total: 7, title: '导数' })
    const yt = video({ id: 'aaaaaaaaaaa' })
    const p1 = video({ id: 'BV1Ex4y1G7nG_p1', source: 'bilibili', course_id: 'BV1Ex4y1G7nG', part: 1, parts_total: 7, title: '导数' })
    const single = video({ id: 'BV1im411d7Eu_p1', source: 'bilibili', course_id: 'BV1im411d7Eu', part: 1, parts_total: 1 })

    const items = groupLibrary([p3, yt, p1, single]) // newest first, as the API returns them

    expect(items.map((i) => i.kind)).toEqual(['course', 'video', 'video'])
    const course = items[0]
    if (course.kind !== 'course') throw new Error('expected course')
    expect(course.parts.map((v) => v.part)).toEqual([1, 3])
    expect(course.latest.id).toBe('BV1Ex4y1G7nG_p3')
    expect(course.partsTotal).toBe(7)
  })
})

describe('thumbnailFor', () => {
  it('uses YouTube thumbnails or the stored Bilibili cover', () => {
    expect(thumbnailFor(video({ id: 'aaaaaaaaaaa' }))).toBe('https://i.ytimg.com/vi/aaaaaaaaaaa/mqdefault.jpg')
    expect(thumbnailFor(video({ source: 'bilibili', thumbnail: 'https://i0.hdslb.com/a.jpg' }))).toBe('https://i0.hdslb.com/a.jpg')
  })
})

describe('bilibiliLoginWarning', () => {
  it('names the configured browser when logged out', () => {
    expect(bilibiliLoginWarning({ logged_in: false, cookies_browser: 'safari' })).toBe(
      'B站未登录：画质较低、字幕将用语音转写（较慢）。在 Safari 登录 bilibili.com 后刷新即可恢复。',
    )
    expect(bilibiliLoginWarning({ logged_in: false, cookies_browser: 'chrome' })).toContain('在 Chrome 登录')
  })

  it('stays quiet when logged in, unknown, or login is turned off', () => {
    expect(bilibiliLoginWarning({ logged_in: true, cookies_browser: 'chrome' })).toBeNull()
    expect(bilibiliLoginWarning(null)).toBeNull()
    expect(bilibiliLoginWarning({ logged_in: false, cookies_browser: '' })).toBeNull()
  })
})
