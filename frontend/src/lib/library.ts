import type { BilibiliStatus, QrLoginState, Video } from './api'

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

/** Shown while Bilibili is not logged in (the library offers the QR-code login next to it). */
export function bilibiliLoginWarning(status: BilibiliStatus | null): string | null {
  if (!status || status.logged_in) return null
  return 'B站未登录：画质较低，没有字幕的视频要先做语音转写。用手机B站 App 扫码登录即可。'
}

const QR_TEXT: Record<QrLoginState, string> = {
  waiting: '用手机B站 App 扫一扫',
  scanned: '已扫码，请在手机上确认登录',
  done: '已登录',
  expired: '二维码已过期',
  failed: '登录没有成功，请重试',
}

export function qrLoginText(state: QrLoginState): string {
  return QR_TEXT[state]
}
