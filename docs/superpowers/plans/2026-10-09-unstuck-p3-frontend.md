# Unstuck Part 3 — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A React app where the learner pastes a YouTube link, watches import progress, plays the video on the left and chats with the tutor on the right; asking pauses the video and sends the current second; `[mm:ss]` in answers are clickable seek links and LaTeX renders with KaTeX.

**Architecture:** Vite + React + TypeScript single-page app with hash routing (`#/v/<id>`). Pure helpers (`lib/timestamps.ts`, `lib/sse.ts`) are unit-tested with Vitest. `lib/api.ts` wraps the Part 2 HTTP API; `askStream` reads the SSE body of a `POST` with `fetch` (EventSource cannot POST). The YouTube IFrame Player API is loaded once and wrapped in a `Player` component exposing `getCurrentTime / seekTo / pause`. The Vite dev server proxies `/api` to the backend on port 8000.

**Tech Stack:** Node 20+, Vite, React, TypeScript, react-markdown + remark-math + rehype-katex + KaTeX, Vitest.

Part 3 of 4. Requires Part 2 (`2026-10-09-unstuck-p2-tutor-api.md`) complete. Spec §3 (frontend modules) and §6 (frontend behaviour).

## Global Constraints

- Frontend: React + TypeScript + Vite; YouTube IFrame Player API; KaTeX; Markdown rendering.
- Local single user; the backend runs at `http://127.0.0.1:8000`, all API paths start with `/api`.
- Asking a question pauses the video and reads `getCurrentTime()`; each user message shows the time it was asked, e.g. "@ 03:35".
- `[mm:ss]` / `[h:mm:ss]` in answers become links that call `seekTo`.
- `transcript_only` videos show "画面理解不可用".
- A failed or interrupted answer keeps its text and shows a "重新生成" button.
- UI copy is Chinese (spec language); the tagline "Stuck on a video? Pause and ask." stays English.
- Must work with a Chinese IME: Enter while composing must not send.

## File Structure (Part 3)

```
frontend/                         (scaffolded by create-vite, then edited)
  vite.config.ts                  /api proxy + vitest config
  package.json                    + "test" script
  src/
    main.tsx                      entry
    App.tsx                       hash routing: library vs video page
    index.css                     all styles (light/dark tokens)
    lib/
      timestamps.ts (+ .test.ts)  formatTime, linkifyTimestamps, normalizeMathDelimiters, seekSecondsFromHref
      sse.ts (+ .test.ts)         SSEParser, AskEvent type
      api.ts                      types + REST calls + askStream
      youtube.ts                  IFrame API loader + minimal types
    components/
      Player.tsx                  YouTube player with imperative handle
      Markdown.tsx                markdown + KaTeX + timestamp links
      ChatPanel.tsx               history, streaming, regenerate, IME-safe input
      ImportView.tsx              ImportForm (paste link) + ImportProgress (stages)
      Library.tsx                 home: import form + imported videos
      VideoPage.tsx               player, progress, outline, chat; polls while importing
```

---

### Task 17: Scaffold and tested helpers (timestamps, SSE, API client)

**Files:**
- Create (scaffold): `frontend/` via create-vite
- Modify: `frontend/vite.config.ts`, `frontend/package.json`
- Create: `frontend/src/lib/timestamps.ts`, `frontend/src/lib/timestamps.test.ts`, `frontend/src/lib/sse.ts`, `frontend/src/lib/sse.test.ts`, `frontend/src/lib/api.ts`, `frontend/src/lib/youtube.ts`

**Interfaces:**
- Consumes: Part 2 HTTP API (JSON shapes of `Video` and `Chat` dataclasses; SSE events `{type: "delta", text} | {type: "done", chat_id} | {type: "error", message, chat_id?}`).
- Produces:
  - `formatTime(seconds: number): string`, `linkifyTimestamps(md: string): string`, `normalizeMathDelimiters(md: string): string`, `seekSecondsFromHref(href: string): number | null`
  - `type AskEvent`, `class SSEParser { push(chunk: string): AskEvent[] }`
  - `STAGES`, `type Stage`, `STAGE_LABELS`, `type StageState`, `interface Chapter`, `interface Video`, `interface ChatMessage`, `class ApiError`, `api.{listVideos, getVideo, importVideo, retry, listChats}`, `askStream(videoId, question, t, onEvent): Promise<void>` (always ends with exactly one `done` or `error` event)
  - `loadYouTubeApi(): Promise<YTNamespace>`, `interface YTPlayer`

- [ ] **Step 1: Scaffold the app**

Run from the repo root:

```bash
npm create vite@latest frontend -- --template react-ts
```

If create-vite asks extra questions, decline experimental options and decline "install and start now". Then:

```bash
cd frontend && npm install && npm install react-markdown remark-math rehype-katex katex && npm install -D vitest
```

Expected: `frontend/node_modules` exists, no errors.

- [ ] **Step 2: Configure the dev proxy, Vitest and the test script**

Replace `frontend/vite.config.ts`:

```ts
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  test: {
    environment: 'node',
  },
})
```

In `frontend/package.json`, add to `"scripts"`:

```json
"test": "vitest run"
```

- [ ] **Step 3: Write the failing tests**

`frontend/src/lib/timestamps.test.ts`:

```ts
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
```

`frontend/src/lib/sse.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { SSEParser } from './sse'

describe('SSEParser', () => {
  it('parses complete events', () => {
    const parser = new SSEParser()
    expect(parser.push('data: {"type":"delta","text":"a"}\n\ndata: {"type":"done","chat_id":3}\n\n')).toEqual([
      { type: 'delta', text: 'a' },
      { type: 'done', chat_id: 3 },
    ])
  })

  it('handles events split across chunks', () => {
    const parser = new SSEParser()
    expect(parser.push('data: {"type":"del')).toEqual([])
    expect(parser.push('ta","text":"b"}\n')).toEqual([])
    expect(parser.push('\n')).toEqual([{ type: 'delta', text: 'b' }])
  })

  it('ignores comment blocks and accepts CRLF', () => {
    expect(new SSEParser().push(': ping\r\n\r\ndata: {"type":"delta","text":"c"}\r\n\r\n')).toEqual([
      { type: 'delta', text: 'c' },
    ])
  })
})
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd frontend && npm test`
Expected: FAIL — cannot resolve `./timestamps` and `./sse`.

- [ ] **Step 5: Implement the helpers**

`frontend/src/lib/timestamps.ts`:

```ts
const TIMESTAMP_RE = /\[(?:(\d{1,2}):)?(\d{1,2}):([0-5]\d)\](?!\()/g
const CODE_RE = /(```[\s\S]*?```|`[^`\n]*`)/g

function outsideCode(md: string, transform: (text: string) => string): string {
  return md
    .split(CODE_RE)
    .map((part, i) => (i % 2 === 1 ? part : transform(part)))
    .join('')
}

function toSeconds(hours: string | undefined, minutes: string, seconds: string): number {
  return (hours ? Number(hours) * 3600 : 0) + Number(minutes) * 60 + Number(seconds)
}

export function formatTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const h = Math.floor(total / 3600)
  const mm = String(Math.floor((total % 3600) / 60)).padStart(2, '0')
  const ss = String(total % 60).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

/** Turn [mm:ss] / [h:mm:ss] into markdown links "#t=<seconds>" (code is left untouched). */
export function linkifyTimestamps(md: string): string {
  return outsideCode(md, (text) =>
    text.replace(
      TIMESTAMP_RE,
      (match: string, h: string | undefined, m: string, s: string) => `${match}(#t=${toSeconds(h, m, s)})`,
    ),
  )
}

export function seekSecondsFromHref(href: string): number | null {
  const match = /^#t=(\d+(?:\.\d+)?)$/.exec(href)
  return match ? Number(match[1]) : null
}

/** Models sometimes write \( \) and \[ \]; remark-math only understands $ and $$. */
export function normalizeMathDelimiters(md: string): string {
  return outsideCode(md, (text) =>
    text
      .replace(/\\\[([\s\S]+?)\\\]/g, (_m: string, body: string) => `$$${body}$$`)
      .replace(/\\\(([\s\S]+?)\\\)/g, (_m: string, body: string) => `$${body}$`),
  )
}
```

`frontend/src/lib/sse.ts`:

```ts
export type AskEvent =
  | { type: 'delta'; text: string }
  | { type: 'done'; chat_id: number }
  | { type: 'error'; message: string; chat_id?: number }

/** Incremental parser for `data: <json>` server-sent events. */
export class SSEParser {
  private buffer = ''

  push(chunk: string): AskEvent[] {
    this.buffer += chunk.replace(/\r\n/g, '\n')
    const events: AskEvent[] = []
    let end = this.buffer.indexOf('\n\n')
    while (end !== -1) {
      const block = this.buffer.slice(0, end)
      this.buffer = this.buffer.slice(end + 2)
      const data = block
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (data) events.push(JSON.parse(data) as AskEvent)
      end = this.buffer.indexOf('\n\n')
    }
    return events
  }
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd frontend && npm test`
Expected: all tests PASS.

- [ ] **Step 7: Implement the API client and YouTube loader**

`frontend/src/lib/api.ts`:

```ts
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
```

`frontend/src/lib/youtube.ts`:

```ts
export interface YTPlayer {
  getCurrentTime(): number
  seekTo(seconds: number, allowSeekAhead: boolean): void
  playVideo(): void
  pauseVideo(): void
  destroy(): void
}

interface YTPlayerOptions {
  videoId: string
  width?: string
  height?: string
  playerVars?: Record<string, string | number>
}

export interface YTNamespace {
  Player: new (element: HTMLElement, options: YTPlayerOptions) => YTPlayer
}

declare global {
  interface Window {
    YT?: YTNamespace
    onYouTubeIframeAPIReady?: () => void
  }
}

let loading: Promise<YTNamespace> | null = null

/** Load https://www.youtube.com/iframe_api once and resolve when it is ready. */
export function loadYouTubeApi(): Promise<YTNamespace> {
  if (window.YT?.Player) return Promise.resolve(window.YT)
  if (!loading) {
    loading = new Promise((resolve) => {
      const previous = window.onYouTubeIframeAPIReady
      window.onYouTubeIframeAPIReady = () => {
        previous?.()
        resolve(window.YT as YTNamespace)
      }
      const script = document.createElement('script')
      script.src = 'https://www.youtube.com/iframe_api'
      document.head.appendChild(script)
    })
  }
  return loading
}
```

- [ ] **Step 8: Type-check and commit**

Run: `cd frontend && npx tsc -b && npm test`
Expected: no type errors; all tests PASS.

```bash
git add frontend/
git commit -m "feat(frontend): scaffold Vite app with timestamp, SSE and API client helpers"
```

---

### Task 18: Player, Markdown and ChatPanel components

**Files:**
- Create: `frontend/src/components/Player.tsx`, `frontend/src/components/Markdown.tsx`, `frontend/src/components/ChatPanel.tsx`

**Interfaces:**
- Consumes: `loadYouTubeApi`, `YTPlayer`, `linkifyTimestamps`, `normalizeMathDelimiters`, `seekSecondsFromHref`, `formatTime`, `api.listChats`, `askStream`, `Video`, `ChatMessage`.
- Produces:
  - `Player` (forwardRef, props `{ videoId: string }`) and `interface PlayerHandle { getCurrentTime(): number; seekTo(seconds: number): void; pause(): void }`
  - `Markdown` props `{ text: string; onSeek: (seconds: number) => void }`
  - `ChatPanel` props `{ video: Video; player: RefObject<PlayerHandle | null> }`

These are UI components verified by building and by the manual run in Task 19; the logic they depend on is already unit-tested.

- [ ] **Step 1: Implement Player**

`frontend/src/components/Player.tsx`:

```tsx
import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import { loadYouTubeApi, type YTPlayer } from '../lib/youtube'

export interface PlayerHandle {
  getCurrentTime(): number
  seekTo(seconds: number): void
  pause(): void
}

export const Player = forwardRef<PlayerHandle, { videoId: string }>(function Player({ videoId }, ref) {
  const hostRef = useRef<HTMLDivElement>(null)
  const playerRef = useRef<YTPlayer | null>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    let cancelled = false
    // The IFrame API replaces its target element, so give it a node React does not own.
    const target = document.createElement('div')
    host.appendChild(target)
    void loadYouTubeApi().then((YT) => {
      if (cancelled) return
      playerRef.current = new YT.Player(target, {
        videoId,
        width: '100%',
        height: '100%',
        playerVars: { rel: 0, playsinline: 1 },
      })
    })
    return () => {
      cancelled = true
      playerRef.current?.destroy()
      playerRef.current = null
      host.replaceChildren()
    }
  }, [videoId])

  useImperativeHandle(
    ref,
    () => ({
      getCurrentTime: () => playerRef.current?.getCurrentTime?.() ?? 0,
      seekTo: (seconds: number) => {
        playerRef.current?.seekTo?.(seconds, true)
        playerRef.current?.playVideo?.()
      },
      pause: () => playerRef.current?.pauseVideo?.(),
    }),
    [],
  )

  return <div className="player" ref={hostRef} />
})
```

- [ ] **Step 2: Implement Markdown**

`frontend/src/components/Markdown.tsx`:

```tsx
import 'katex/dist/katex.min.css'
import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkMath from 'remark-math'
import { linkifyTimestamps, normalizeMathDelimiters, seekSecondsFromHref } from '../lib/timestamps'

interface Props {
  text: string
  onSeek: (seconds: number) => void
}

export function Markdown({ text, onSeek }: Props) {
  const source = useMemo(() => linkifyTimestamps(normalizeMathDelimiters(text)), [text])
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          a: ({ href, children }) => {
            const seconds = href ? seekSecondsFromHref(href) : null
            if (seconds === null) {
              return (
                <a href={href} target="_blank" rel="noreferrer">
                  {children}
                </a>
              )
            }
            return (
              <a
                href={href}
                className="ts-link"
                onClick={(event) => {
                  event.preventDefault()
                  onSeek(seconds)
                }}
              >
                {children}
              </a>
            )
          },
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
```

- [ ] **Step 3: Implement ChatPanel**

`frontend/src/components/ChatPanel.tsx`:

```tsx
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type RefObject } from 'react'
import { api, askStream, type ChatMessage, type Video } from '../lib/api'
import { formatTime } from '../lib/timestamps'
import { Markdown } from './Markdown'
import type { PlayerHandle } from './Player'

type Status = 'done' | 'streaming' | 'error'

interface UiMessage {
  key: string
  role: 'user' | 'assistant'
  content: string
  t: number | null
  status: Status
  error?: string
  question?: string
}

let keyCounter = 0
const newKey = () => `local-${++keyCounter}`

function fromHistory(chats: ChatMessage[]): UiMessage[] {
  let lastQuestion: string | undefined
  return chats.map((chat) => {
    if (chat.role === 'user') lastQuestion = chat.content
    return {
      key: `db-${chat.id}`,
      role: chat.role,
      content: chat.content,
      t: chat.t_at_ask,
      status: chat.complete ? 'done' : 'error',
      error: chat.complete ? undefined : '这条回答没有完成',
      question: chat.role === 'assistant' ? lastQuestion : undefined,
    }
  })
}

interface Props {
  video: Video
  player: RefObject<PlayerHandle | null>
}

export function ChatPanel({ video, player }: Props) {
  const [messages, setMessages] = useState<UiMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    api
      .listChats(video.id)
      .then((chats) => {
        if (!cancelled) setMessages(fromHistory(chats))
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [video.id])

  useEffect(() => {
    const list = listRef.current
    if (list) list.scrollTop = list.scrollHeight
  }, [messages])

  const update = (key: string, fn: (m: UiMessage) => UiMessage) =>
    setMessages((all) => all.map((m) => (m.key === key ? fn(m) : m)))

  const run = async (question: string, t: number, key: string) => {
    setBusy(true)
    await askStream(video.id, question, t, (event) => {
      if (event.type === 'delta') update(key, (m) => ({ ...m, content: m.content + event.text }))
      else if (event.type === 'done') update(key, (m) => ({ ...m, status: 'done' }))
      else update(key, (m) => ({ ...m, status: 'error', error: event.message }))
    })
    setBusy(false)
  }

  const submit = (event?: FormEvent) => {
    event?.preventDefault()
    const question = input.trim()
    if (!question || busy || !video.ask_ready) return
    player.current?.pause()
    const t = player.current?.getCurrentTime() ?? 0
    const key = newKey()
    setInput('')
    setMessages((all) => [
      ...all,
      { key: newKey(), role: 'user', content: question, t, status: 'done' },
      { key, role: 'assistant', content: '', t, status: 'streaming', question },
    ])
    void run(question, t, key)
  }

  const regenerate = (message: UiMessage) => {
    if (busy || !message.question || message.t === null) return
    update(message.key, (m) => ({ ...m, content: '', status: 'streaming', error: undefined }))
    void run(message.question, message.t, message.key)
  }

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

  const seek = (seconds: number) => player.current?.seekTo(seconds)

  return (
    <div className="chat">
      {video.visual_mode === 'transcript_only' && <div className="notice">画面理解不可用：这个视频只根据字幕回答</div>}
      <div className="chat-list" ref={listRef}>
        {loadError && <div className="notice error">{loadError}</div>}
        {messages.length === 0 && !loadError && (
          <div className="chat-empty">看到哪里不懂，就暂停提问。AI 知道你看到了第几秒，也能看到当前画面。</div>
        )}
        {messages.map((m) =>
          m.role === 'user' ? (
            <div key={m.key} className="msg msg-user">
              {m.t !== null && (
                <button type="button" className="msg-time" onClick={() => seek(m.t ?? 0)}>
                  @ {formatTime(m.t)}
                </button>
              )}
              <div className="msg-text">{m.content}</div>
            </div>
          ) : (
            <div key={m.key} className="msg msg-assistant">
              {m.content ? (
                <Markdown text={m.content} onSeek={seek} />
              ) : (
                m.status === 'streaming' && <div className="typing">思考中…</div>
              )}
              {m.status === 'error' && (
                <div className="msg-error">
                  <span>{m.error ?? '回答失败'}</span>
                  {m.question && (
                    <button type="button" className="btn btn-small" onClick={() => regenerate(m)} disabled={busy}>
                      重新生成
                    </button>
                  )}
                </div>
              )}
            </div>
          ),
        )}
      </div>
      <form className="chat-input" onSubmit={submit}>
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onFocus={() => player.current?.pause()}
          onKeyDown={onKeyDown}
          placeholder={video.ask_ready ? '在这里提问，回车发送，Shift+回车换行' : '字幕还在处理中，马上就可以提问…'}
          disabled={!video.ask_ready}
          rows={3}
        />
        <button type="submit" className="btn" disabled={busy || !video.ask_ready || !input.trim()}>
          提问
        </button>
      </form>
    </div>
  )
}
```

- [ ] **Step 4: Type-check and commit**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

```bash
git add frontend/
git commit -m "feat(frontend): YouTube player, markdown with KaTeX and seek links, streaming chat panel"
```

---

### Task 19: Pages, routing, styles and a manual end-to-end run

**Files:**
- Create: `frontend/src/components/ImportView.tsx`, `frontend/src/components/Library.tsx`, `frontend/src/components/VideoPage.tsx`
- Replace: `frontend/src/App.tsx`, `frontend/src/main.tsx`, `frontend/src/index.css`
- Delete: `frontend/src/App.css`

**Interfaces:**
- Consumes: everything from Tasks 17–18.
- Produces: `ImportForm({ onImported })`, `ImportProgress({ video, onRetry })`, `Library({ onOpen })`, `VideoPage({ videoId })`, default `App`.

- [ ] **Step 1: Implement ImportView**

`frontend/src/components/ImportView.tsx`:

```tsx
import { useState, type FormEvent } from 'react'
import { STAGE_LABELS, STAGES, api, type StageState, type Video } from '../lib/api'

const STATE_LABELS: Record<StageState, string> = {
  pending: '等待',
  running: '进行中',
  done: '完成',
  failed: '失败',
  skipped: '跳过',
}

export function ImportForm({ onImported }: { onImported: (id: string) => void }) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!url.trim()) return
    setBusy(true)
    setError(null)
    try {
      const video = await api.importVideo(url.trim())
      setUrl('')
      onImported(video.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="import-form" onSubmit={submit}>
      <input
        value={url}
        onChange={(event) => setUrl(event.target.value)}
        placeholder="粘贴 YouTube 视频链接"
        aria-label="YouTube 视频链接"
      />
      <button type="submit" className="btn" disabled={busy || !url.trim()}>
        {busy ? '导入中…' : '导入'}
      </button>
      {error && <div className="form-error">{error}</div>}
    </form>
  )
}

function headline(video: Video): string {
  if (video.status === 'failed') return '导入失败'
  if (video.status === 'ready') return '导入完成，但有部分步骤失败'
  return video.ask_ready ? '字幕已就绪，可以开始提问；画面理解还在后台进行' : '正在导入…'
}

export function ImportProgress({ video, onRetry }: { video: Video; onRetry: () => void }) {
  const canRetry = video.status !== 'importing'
  return (
    <div className="progress">
      <div className="progress-head">
        <span>{headline(video)}</span>
        {canRetry && (
          <button type="button" className="btn btn-small" onClick={onRetry}>
            重试
          </button>
        )}
      </div>
      {video.error_message && <div className="form-error">{video.error_message}</div>}
      <ol className="stages">
        {STAGES.map((stage) => {
          const state = video.stage_states[stage] ?? 'pending'
          return (
            <li key={stage} className={`stage stage-${state}`}>
              <span>{STAGE_LABELS[stage]}</span>
              <span>{STATE_LABELS[state]}</span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
```

- [ ] **Step 2: Implement Library**

`frontend/src/components/Library.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { api, type Video } from '../lib/api'
import { formatTime } from '../lib/timestamps'
import { ImportForm } from './ImportView'

const STATUS_LABELS: Record<Video['status'], string> = {
  importing: '导入中',
  ready: '可以学习',
  failed: '导入失败',
}

export function Library({ onOpen }: { onOpen: (id: string) => void }) {
  const [videos, setVideos] = useState<Video[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .listVideos()
      .then(setVideos)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
  }, [])

  return (
    <main className="library">
      <section className="hero">
        <h1>卡住了？暂停提问。</h1>
        <p className="muted">粘贴一个 YouTube 教学视频链接。边看边问，AI 会结合你正在看的那一秒和当前画面来讲解。</p>
        <ImportForm onImported={onOpen} />
      </section>
      <section>
        <h2>我的视频</h2>
        {error && <div className="form-error">{error}</div>}
        {videos?.length === 0 && <p className="muted">还没有导入视频。</p>}
        <ul className="video-list">
          {videos?.map((video) => (
            <li key={video.id}>
              <button type="button" className="video-card" onClick={() => onOpen(video.id)}>
                <img src={`https://i.ytimg.com/vi/${video.id}/mqdefault.jpg`} alt="" loading="lazy" />
                <span className="video-title">{video.title || video.id}</span>
                <span className="muted">
                  {[video.channel, video.duration ? formatTime(video.duration) : '', STATUS_LABELS[video.status]]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </main>
  )
}
```

- [ ] **Step 3: Implement VideoPage**

`frontend/src/components/VideoPage.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'
import { api, type Chapter, type Video } from '../lib/api'
import { formatTime } from '../lib/timestamps'
import { ChatPanel } from './ChatPanel'
import { ImportProgress } from './ImportView'
import { Player, type PlayerHandle } from './Player'

const POLL_MS = 2000

function Outline({ chapters, onSeek }: { chapters: Chapter[]; onSeek: (seconds: number) => void }) {
  return (
    <section className="outline">
      <h2>大纲</h2>
      <ol>
        {chapters.map((chapter) => (
          <li key={chapter.start}>
            <button type="button" onClick={() => onSeek(chapter.start)}>
              <span className="ts">{formatTime(chapter.start)}</span>
              {chapter.title}
            </button>
          </li>
        ))}
      </ol>
    </section>
  )
}

export function VideoPage({ videoId }: { videoId: string }) {
  const [video, setVideo] = useState<Video | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pollKey, setPollKey] = useState(0)
  const playerRef = useRef<PlayerHandle>(null)

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined
    const load = async () => {
      try {
        const next = await api.getVideo(videoId)
        if (cancelled) return
        setVideo(next)
        setError(null)
        if (next.status === 'importing') timer = window.setTimeout(load, POLL_MS)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      }
    }
    void load()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [videoId, pollKey])

  const retry = async () => {
    try {
      setVideo(await api.retry(videoId))
      setPollKey((k) => k + 1)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const hasFailure = video ? Object.values(video.stage_states).includes('failed') : false

  return (
    <main className="video-page">
      <section className="video-col">
        <Player ref={playerRef} videoId={videoId} />
        {error && <div className="form-error">{error}</div>}
        {video && (
          <>
            <h1 className="video-heading">{video.title || videoId}</h1>
            {video.channel && <p className="muted">{video.channel}</p>}
            {(video.status !== 'ready' || hasFailure) && <ImportProgress video={video} onRetry={retry} />}
            {video.outline && video.outline.length > 0 && (
              <Outline chapters={video.outline} onSeek={(seconds) => playerRef.current?.seekTo(seconds)} />
            )}
          </>
        )}
      </section>
      <section className="chat-col">
        {video ? <ChatPanel video={video} player={playerRef} /> : <div className="muted">加载中…</div>}
      </section>
    </main>
  )
}
```

- [ ] **Step 4: Replace App, main and styles**

`frontend/src/App.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { Library } from './components/Library'
import { VideoPage } from './components/VideoPage'

function readVideoId(): string | null {
  const match = /^#\/v\/([A-Za-z0-9_-]{11})$/.exec(window.location.hash)
  return match ? match[1] : null
}

export default function App() {
  const [videoId, setVideoId] = useState(readVideoId)

  useEffect(() => {
    const onHashChange = () => setVideoId(readVideoId())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const open = (id: string) => {
    window.location.hash = `#/v/${id}`
  }

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#">
          Unstuck
        </a>
        <span className="tagline">Stuck on a video? Pause and ask.</span>
      </header>
      {videoId ? <VideoPage key={videoId} videoId={videoId} /> : <Library onOpen={open} />}
    </div>
  )
}
```

`frontend/src/main.tsx`:

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

`frontend/src/index.css`:

```css
:root {
  --bg: #f7f7f5;
  --surface: #ffffff;
  --surface-2: #efeee9;
  --text: #1c1c1a;
  --muted: #6b6a65;
  --border: #e2e0da;
  --accent: #2f6fde;
  --accent-text: #ffffff;
  --danger: #c2410c;
  --ok: #15803d;
  --radius: 10px;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei',
    sans-serif;
  line-height: 1.55;
  color: var(--text);
  background: var(--bg);
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #151514;
    --surface: #1e1e1c;
    --surface-2: #2a2a27;
    --text: #ecebe6;
    --muted: #9b9a94;
    --border: #353431;
    --accent: #6b9cf0;
    --accent-text: #0b1220;
    --danger: #fb923c;
    --ok: #4ade80;
  }
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: var(--bg);
}

button {
  font: inherit;
  cursor: pointer;
}

button:disabled {
  opacity: 0.5;
  cursor: default;
}

.btn {
  padding: 10px 16px;
  border: none;
  border-radius: var(--radius);
  background: var(--accent);
  color: var(--accent-text);
  font-weight: 600;
}

.btn-small {
  padding: 3px 10px;
  font-size: 13px;
}

.muted {
  color: var(--muted);
}

.form-error {
  width: 100%;
  margin-top: 6px;
  color: var(--danger);
  font-size: 14px;
}

.notice {
  margin: 12px 12px 0;
  padding: 8px 12px;
  border-radius: var(--radius);
  background: var(--surface-2);
  color: var(--muted);
  font-size: 14px;
}

.notice.error {
  color: var(--danger);
}

/* layout */
.app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.topbar {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
}

.brand {
  font-size: 18px;
  font-weight: 700;
  color: var(--text);
  text-decoration: none;
}

.tagline {
  color: var(--muted);
  font-size: 14px;
}

/* library */
.library {
  width: 100%;
  max-width: 960px;
  margin: 0 auto;
  padding: 32px 16px;
}

.hero h1 {
  margin: 0 0 8px;
  font-size: 28px;
}

.import-form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 16px 0 32px;
}

.import-form input {
  flex: 1 1 320px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  font: inherit;
}

.video-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 16px;
  padding: 0;
  list-style: none;
}

.video-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  padding: 0 0 12px;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  text-align: left;
}

.video-card img {
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
  background: var(--surface-2);
}

.video-card > span {
  padding: 0 12px;
}

.video-title {
  font-weight: 600;
}

/* video page */
.video-page {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(320px, 2fr);
  gap: 16px;
  padding: 16px;
}

.video-col {
  min-width: 0;
}

.player {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  border-radius: var(--radius);
  background: #000;
}

.player iframe {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  border: 0;
}

.video-heading {
  margin: 12px 0 2px;
  font-size: 20px;
}

.progress {
  margin-top: 12px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
}

.progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-weight: 600;
}

.stages {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 6px;
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
}

.stage {
  display: flex;
  justify-content: space-between;
  padding: 4px 8px;
  border-radius: 6px;
  background: var(--surface-2);
  font-size: 13px;
}

.stage-pending,
.stage-skipped {
  color: var(--muted);
}

.stage-running span:last-child {
  color: var(--accent);
}

.stage-done span:last-child {
  color: var(--ok);
}

.stage-failed span:last-child {
  color: var(--danger);
}

.outline h2 {
  margin: 20px 0 6px;
  font-size: 16px;
}

.outline ol {
  margin: 0;
  padding: 0;
  list-style: none;
}

.outline button {
  padding: 4px 0;
  border: none;
  background: none;
  color: var(--text);
  text-align: left;
}

.outline .ts {
  margin-right: 10px;
  color: var(--accent);
  font-variant-numeric: tabular-nums;
}

/* chat */
.chat-col {
  min-width: 0;
}

.chat {
  position: sticky;
  top: 16px;
  display: flex;
  flex-direction: column;
  height: calc(100vh - 90px);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
}

.chat-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  overflow-y: auto;
}

.chat-empty {
  margin: auto 0;
  color: var(--muted);
  text-align: center;
}

.msg {
  max-width: 92%;
}

.msg-user {
  align-self: flex-end;
  padding: 8px 12px;
  border-radius: var(--radius);
  background: var(--surface-2);
}

.msg-assistant {
  align-self: flex-start;
}

.msg-time {
  padding: 0;
  border: none;
  background: none;
  color: var(--accent);
  font-size: 12px;
}

.msg-text {
  white-space: pre-wrap;
}

.msg-error {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  color: var(--danger);
  font-size: 14px;
}

.typing {
  color: var(--muted);
}

.chat-input {
  display: flex;
  gap: 8px;
  padding: 12px;
  border-top: 1px solid var(--border);
}

.chat-input textarea {
  flex: 1;
  padding: 8px 10px;
  resize: none;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg);
  color: var(--text);
  font: inherit;
}

/* markdown */
.markdown p {
  margin: 0 0 8px;
}

.markdown pre {
  padding: 8px;
  overflow-x: auto;
  border-radius: 6px;
  background: var(--surface-2);
}

.markdown code {
  font-family: ui-monospace, Menlo, monospace;
  font-size: 0.92em;
}

.markdown .katex-display {
  overflow-x: auto;
  overflow-y: hidden;
}

.ts-link {
  color: var(--accent);
  font-weight: 600;
  text-decoration: none;
}

@media (max-width: 900px) {
  .video-page {
    grid-template-columns: 1fr;
  }

  .chat {
    position: static;
    height: 70vh;
  }
}
```

Delete the template stylesheet:

```bash
rm frontend/src/App.css
```

- [ ] **Step 5: Build and test**

Run: `cd frontend && npm test && npm run build`
Expected: tests PASS; `vite build` finishes and writes `frontend/dist/` with no TypeScript errors.

- [ ] **Step 6: Manual end-to-end run**

Terminal 1:

```bash
cd backend && uv run uvicorn unstuck.api.app:create_app --factory --port 8000
```

Terminal 2:

```bash
cd frontend && npm run dev
```

Open http://localhost:5173 (in the Claude desktop app, use the built-in browser pane) and check each item:

1. Paste a short lecture video link → the page switches to the video, the stage list advances, and the input box unlocks once 字幕 is 完成.
2. Play the video, click into the input box → the video pauses. Ask "这里讲的是什么？" → the user bubble shows "@ mm:ss" matching the player, and the answer streams in.
3. An answer with `[mm:ss]` shows bold blue links; clicking one seeks the player there.
4. Ask for a formula ("把这个公式写出来") → it renders with KaTeX, not as raw `$...$`.
5. Reload the page → the chat history is still there.
6. Stop the backend while an answer is streaming → the message shows an error and a 重新生成 button; restart the backend and click it → the answer regenerates.
7. Type with a Chinese IME and press Enter to pick a candidate → nothing is sent until Enter is pressed again.
8. Narrow the window below 900 px → the chat stacks under the video.

Fix anything that fails before committing.

- [ ] **Step 7: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): library, import progress, video page with outline and responsive styles"
```

---

**Part 3 done when:** `npm test` and `npm run build` pass and all 8 manual checks succeed. Next: `2026-10-09-unstuck-p4-integration-eval.md`.
