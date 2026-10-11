import { useState, type FormEvent } from 'react'
import { STAGE_LABELS, STAGES, api, type Video } from '../lib/api'
import { importHeadline, stageStateLabel } from '../lib/progress'

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
        placeholder="粘贴 YouTube 或 B站视频链接"
        aria-label="YouTube 或 B站视频链接"
      />
      <button type="submit" className="btn" disabled={busy || !url.trim()}>
        {busy ? '导入中…' : '导入'}
      </button>
      {error && <div className="form-error">{error}</div>}
    </form>
  )
}

export function ImportProgress({ video, onRetry }: { video: Video; onRetry: () => void }) {
  const canRetry = video.status !== 'importing'
  return (
    <div className="progress">
      <div className="progress-head">
        <span>{importHeadline(video)}</span>
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
              <span>{stageStateLabel(video, stage)}</span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
