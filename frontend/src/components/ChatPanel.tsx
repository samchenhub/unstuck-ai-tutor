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
