import { useEffect, useState } from 'react'
import { api, type Part, type Video } from '../lib/api'
import { formatTime } from '../lib/timestamps'
import { biliPart, openVideo } from '../lib/videoId'

export function PartsList({ video }: { video: Video }) {
  const [parts, setParts] = useState<Part[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .parts(video.id)
      .then((list) => {
        if (!cancelled) setParts(list)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [video.id])

  const importPart = async (part: Part) => {
    const ids = biliPart(part.video_id)
    if (!ids) return
    setBusy(part.part)
    setError(null)
    try {
      const imported = await api.importVideo(`https://www.bilibili.com/video/${ids.bvid}?p=${ids.part}`)
      openVideo(imported.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setBusy(null)
    }
  }

  return (
    <section className="parts">
      <h2>选集（共 {video.parts_total} 集）</h2>
      {error && <div className="form-error">{error}</div>}
      <ol>
        {parts?.map((part) => (
          <li key={part.part} className={part.video_id === video.id ? 'current' : undefined}>
            <span className="ts">P{part.part}</span>
            <span className="part-title">{part.title}</span>
            <span className="muted">{formatTime(part.duration)}</span>
            {part.video_id === video.id ? (
              <span className="muted">正在看</span>
            ) : part.imported ? (
              <button type="button" className="btn btn-small" onClick={() => openVideo(part.video_id)}>
                观看
              </button>
            ) : (
              <button type="button" className="btn btn-small" disabled={busy !== null} onClick={() => void importPart(part)}>
                {busy === part.part ? '导入中…' : '导入'}
              </button>
            )}
          </li>
        ))}
      </ol>
    </section>
  )
}
