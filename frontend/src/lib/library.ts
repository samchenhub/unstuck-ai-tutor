import type { BilibiliStatus, Video } from './api'

export type LibraryItem =
  | { kind: 'video'; video: Video }
  | {
      kind: 'course'
      courseId: string
      title: string
      channel: string
      thumbnail: string
      partsTotal: number
      parts: Video[]
      latest: Video
    }

/** Videos arrive newest first; parts of one multi-part course become a single card at its newest part. */
export function groupLibrary(videos: Video[]): LibraryItem[] {
  const items: LibraryItem[] = []
  const courses = new Map<string, Extract<LibraryItem, { kind: 'course' }>>()
  for (const video of videos) {
    if (video.source !== 'bilibili' || !video.course_id || video.parts_total <= 1) {
      items.push({ kind: 'video', video })
      continue
    }
    const existing = courses.get(video.course_id)
    if (existing) {
      existing.parts.push(video)
      continue
    }
    const course = {
      kind: 'course' as const,
      courseId: video.course_id,
      title: video.title,
      channel: video.channel,
      thumbnail: video.thumbnail,
      partsTotal: video.parts_total,
      parts: [video],
      latest: video,
    }
    courses.set(video.course_id, course)
    items.push(course)
  }
  for (const course of courses.values()) course.parts.sort((a, b) => (a.part ?? 0) - (b.part ?? 0))
  return items
}

export function thumbnailFor(video: Video): string {
  return video.source === 'youtube' ? `https://i.ytimg.com/vi/${video.id}/mqdefault.jpg` : video.thumbnail
}

const BROWSER_NAMES: Record<string, string> = { chrome: 'Chrome', safari: 'Safari', edge: 'Edge', firefox: 'Firefox' }

/** Shown when Bilibili login is configured but not active; an empty browser means login was turned off on purpose. */
export function bilibiliLoginWarning(status: BilibiliStatus | null): string | null {
  if (!status || status.logged_in || !status.cookies_browser) return null
  const browser = BROWSER_NAMES[status.cookies_browser] ?? status.cookies_browser
  return `B站未登录：画质较低、字幕将用语音转写（较慢）。在 ${browser} 登录 bilibili.com 后刷新即可恢复。`
}
