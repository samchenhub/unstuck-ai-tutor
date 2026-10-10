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
