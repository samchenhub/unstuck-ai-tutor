import { SSEParser, type AskEvent } from './sse'

export type { AskEvent }

export const STAGES = ['metadata', 'transcript', 'download', 'keyframes', 'vision', 'timeline', 'index', 'outline'] as const
export type Stage = (typeof STAGES)[number]
export type StageState = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export const STAGE_LABELS: Record<Stage, string> = {
  metadata: '视频信息',
  transcript: '字幕',
  download: '下载视频',
  keyframes: '关键帧',
  vision: '画面理解',
  timeline: '时间轴',
  index: '索引',
  outline: '大纲',
}

export interface Chapter {
  start: number
  title: string
  summary: string
}

export interface Video {
  id: string
  url: string
  title: string
  channel: string
  duration: number
  status: 'importing' | 'ready' | 'failed'
  ask_ready: boolean
  stage_states: Partial<Record<Stage, StageState>>
  visual_mode: 'full' | 'transcript_only'
  outline: Chapter[] | null
  error_code: string | null
  error_message: string | null
  created_at: string
  source: 'youtube' | 'bilibili'
  course_id: string | null
  part: number | null
  part_title: string
  parts_total: number
  thumbnail: string
  transcribed_until: number
  auto_retry_at: number // epoch seconds of the next automatic retry after a rate limit; 0 = none
  auto_retries: number
}

export interface Playback {
  kind: 'youtube' | 'mp4'
  url: string
  expires_at: number
  duration: number
  backup_urls: string[]
}

export interface Part {
  part: number
  title: string
  duration: number
  video_id: string
  imported: boolean
}

export interface BilibiliStatus {
  logged_in: boolean
  cookies_browser: string
}

export interface ChatMessage {
  id: number
  video_id: string
  role: 'user' | 'assistant'
  content: string
  t_at_ask: number | null
  complete: boolean
  created_at: string
}

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json()
    const detail = body?.detail
    if (typeof detail === 'string') return detail
    if (typeof detail?.message === 'string') return detail.message
  } catch {
    // body was not JSON; fall through to the generic message
  }
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    return '无法连接到后端服务，请确认后端已启动'
  }
  return `请求失败（${res.status}）`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) throw new ApiError(await readError(res), res.status)
  return (await res.json()) as T
}

export const api = {
  listVideos: () => request<Video[]>('/api/videos'),
  getVideo: (id: string) => request<Video>(`/api/videos/${id}`),
  importVideo: (url: string) =>
    request<Video>('/api/videos', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ url }) }),
  retry: (id: string) => request<Video>(`/api/videos/${id}/retry`, { method: 'POST' }),
  listChats: (id: string) => request<ChatMessage[]>(`/api/videos/${id}/chats`),
  playback: (id: string, quality = 720) => request<Playback>(`/api/videos/${id}/playback?quality=${quality}`),
  parts: (id: string) => request<Part[]>(`/api/videos/${id}/parts`),
  bilibiliStatus: () => request<BilibiliStatus>('/api/bilibili/status'),
}

/** POST a question and stream the answer. Always finishes with exactly one 'done' or 'error' event. */
export async function askStream(
  videoId: string,
  question: string,
  t: number,
  onEvent: (event: AskEvent) => void,
): Promise<void> {
  let res: Response
  try {
    res = await fetch(`/api/videos/${videoId}/ask`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ question, t }),
    })
  } catch {
    onEvent({ type: 'error', message: '无法连接到后端服务' })
    return
  }
  if (!res.ok || !res.body) {
    onEvent({ type: 'error', message: await readError(res) })
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  const parser = new SSEParser()
  let finished = false
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      for (const event of parser.push(decoder.decode(value, { stream: true }))) {
        if (event.type !== 'delta') finished = true
        onEvent(event)
      }
    }
  } catch {
    // connection dropped; reported below
  }
  if (!finished) onEvent({ type: 'error', message: '连接中断，回答没有完成' })
}
