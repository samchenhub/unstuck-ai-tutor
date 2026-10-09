import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { loadYouTubeApi, type YTPlayer } from '../lib/youtube'

export interface PlayerHandle {
  getCurrentTime(): number
  seekTo(seconds: number): void
  pause(): void
}

export const Player = forwardRef<PlayerHandle, { videoId: string }>(function Player({ videoId }, ref) {
  const hostRef = useRef<HTMLDivElement>(null)
  const playerRef = useRef<YTPlayer | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    let cancelled = false
    setError(null)
    // The IFrame API replaces its target element, so give it a node React does not own.
    const target = document.createElement('div')
    host.appendChild(target)
    loadYouTubeApi()
      .then((YT) => {
        if (cancelled) return
        playerRef.current = new YT.Player(target, {
          videoId,
          width: '100%',
          height: '100%',
          playerVars: { rel: 0, playsinline: 1 },
        })
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
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

  // Keep the host mounted so a later videoId change can retry; just hide it while showing the error.
  return (
    <>
      <div className="player" ref={hostRef} hidden={error !== null} />
      {error !== null && <div className="player-error">{error}</div>}
    </>
  )
})
