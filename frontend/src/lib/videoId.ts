export type VideoSource = 'youtube' | 'bilibili'

const YOUTUBE_ID = /^[A-Za-z0-9_-]{11}$/
const BILIBILI_ID = /^(BV[0-9A-Za-z]{10})_p([1-9][0-9]{0,3})$/
const ROUTE = /^#\/v\/([^/]+)$/

export function sourceOfId(id: string): VideoSource | null {
  if (BILIBILI_ID.test(id)) return 'bilibili'
  if (YOUTUBE_ID.test(id)) return 'youtube'
  return null
}

export function parseRoute(hash: string): string | null {
  const match = ROUTE.exec(hash)
  return match && sourceOfId(match[1]) ? match[1] : null
}

export function biliPart(id: string): { bvid: string; part: number } | null {
  const match = BILIBILI_ID.exec(id)
  return match ? { bvid: match[1], part: Number(match[2]) } : null
}

export function openVideo(id: string): void {
  window.location.hash = `#/v/${id}`
}
