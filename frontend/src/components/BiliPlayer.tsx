import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { api } from '../lib/api'
import { refreshDelayMs } from '../lib/retry'
import type { PlayerHandle } from './playerHandle'

// An expired or broken stream URL fires `error`; fetch a fresh one and resume, but not forever.
const MAX_REFRESHES = 3

export const BiliPlayer = forwardRef<PlayerHandle, { videoId: string }>(function BiliPlayer({ videoId }, ref) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const resumeAt = useRef<number | null>(null)
  const refreshes = useRef(0)
  const wasPlaying = useRef(false)
  const retryTimer = useRef<number | undefined>(undefined)
  const [src, setSrc] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(
    async (resume: number | null) => {
      try {
        const playback = await api.playback(videoId)
        resumeAt.current = resume
        setSrc(playback.url)
        setError(null)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
    },
    [videoId],
  )

  useEffect(() => {
    refreshes.current = 0
    setSrc(null)
    void load(null)
    return () => window.clearTimeout(retryTimer.current)
  }, [load])

  const onError = () => {
    if (refreshes.current >= MAX_REFRESHES) {
      setError('B站视频加载失败，请刷新页面重试')
      return
    }
    refreshes.current += 1
    // A pending resume means the last refresh hasn't loaded yet; currentTime is 0 after a src swap, so keep the saved one.
    if (resumeAt.current === null) wasPlaying.current = !(videoRef.current?.paused ?? true)
    const resume = resumeAt.current ?? videoRef.current?.currentTime ?? 0
    resumeAt.current = resume
    window.clearTimeout(retryTimer.current)
    retryTimer.current = window.setTimeout(() => void load(resume), refreshDelayMs(refreshes.current))
  }

  const onLoadedMetadata = () => {
    const video = videoRef.current
    if (video && resumeAt.current !== null) {
      video.currentTime = resumeAt.current
      resumeAt.current = null
      // The refreshed stream loaded, so the budget is for the next outage; a paused video never fires `playing`.
      refreshes.current = 0
      if (wasPlaying.current) void video.play().catch(() => {})
    }
  }

  useImperativeHandle(
    ref,
    () => ({
      getCurrentTime: () => videoRef.current?.currentTime ?? 0,
      seekTo: (seconds: number) => {
        const video = videoRef.current
        if (!video) return
        video.currentTime = seconds
        void video.play().catch(() => {})
      },
      pause: () => videoRef.current?.pause(),
    }),
    [],
  )

  return (
    <>
      <div className="player" hidden={error !== null}>
        {src && (
          <video
            ref={videoRef}
            src={src}
            controls
            playsInline
            preload="metadata"
            onError={onError}
            onLoadedMetadata={onLoadedMetadata}
            onPlaying={() => {
              refreshes.current = 0
            }}
          />
        )}
      </div>
      {error !== null && <div className="player-error">{error}</div>}
    </>
  )
})
