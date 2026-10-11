import { useEffect, useRef, useState } from 'react'
import { api, type Chapter, type Video } from '../lib/api'
import { formatTime } from '../lib/timestamps'
import { ChatPanel } from './ChatPanel'
import { ImportProgress } from './ImportView'
import { PartsList } from './PartsList'
import { Player, type PlayerHandle } from './Player'

const POLL_MS = 2000
const RETRY_POLL_MS = 15000

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
    let last: Video | null = null
    const load = async () => {
      try {
        const next = await api.getVideo(videoId)
        if (cancelled) return
        last = next
        setVideo(next)
        setError(null)
        if (next.status === 'importing') timer = window.setTimeout(load, POLL_MS)
        // A rate-limited import is retried by the backend later; keep watching, but slowly.
        else if (next.auto_retry_at > 0) timer = window.setTimeout(load, RETRY_POLL_MS)
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
        // A transient failure must not end polling while the import is running, a retry is planned,
        // or nothing has loaded yet.
        if (last === null || last.status === 'importing') timer = window.setTimeout(load, POLL_MS)
        else if (last.auto_retry_at > 0) timer = window.setTimeout(load, RETRY_POLL_MS)
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
            {video.parts_total > 1 && (
              <p className="muted">
                P{video.part} · {video.part_title}
              </p>
            )}
            {video.channel && <p className="muted">{video.channel}</p>}
            {(video.status !== 'ready' || hasFailure) && <ImportProgress video={video} onRetry={retry} />}
            {video.outline && video.outline.length > 0 && (
              <Outline chapters={video.outline} onSeek={(seconds) => playerRef.current?.seekTo(seconds)} />
            )}
            {video.source === 'bilibili' && video.parts_total > 1 && <PartsList video={video} />}
          </>
        )}
      </section>
      <section className="chat-col">
        {video ? (
          <ChatPanel video={video} player={playerRef} />
        ) : (
          !error && <div className="muted">加载中…</div>
        )}
      </section>
    </main>
  )
}
