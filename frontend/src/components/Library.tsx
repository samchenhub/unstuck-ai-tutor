import { useEffect, useState } from 'react'
import { api, type BilibiliStatus, type Video } from '../lib/api'
import { groupLibrary, thumbnailFor } from '../lib/library'
import { formatTime } from '../lib/timestamps'
import { ImportForm } from './ImportView'

const STATUS_LABELS: Record<Video['status'], string> = {
  importing: '导入中',
  ready: '可以学习',
  failed: '导入失败',
}
const SOURCE_LABELS: Record<Video['source'], string> = { youtube: 'YouTube', bilibili: 'B站' }
export const BILIBILI_LOGIN_WARNING =
  'B站未登录：画质较低、字幕将用语音转写（较慢）。在 Chrome 登录 bilibili.com 后刷新即可恢复。'

function Card({ thumbnail, source, title, meta, onClick }: {
  thumbnail: string; source: Video['source']; title: string; meta: string; onClick: () => void
}) {
  return (
    <button type="button" className="video-card" onClick={onClick}>
      <span className="thumb">
        {thumbnail ? <img src={thumbnail} alt="" loading="lazy" referrerPolicy="no-referrer" /> : null}
        <span className={`badge badge-${source}`}>{SOURCE_LABELS[source]}</span>
      </span>
      <span className="video-title">{title}</span>
      <span className="muted">{meta}</span>
    </button>
  )
}

export function Library({ onOpen }: { onOpen: (id: string) => void }) {
  const [videos, setVideos] = useState<Video[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [bili, setBili] = useState<BilibiliStatus | null>(null)

  useEffect(() => {
    api
      .listVideos()
      .then(setVideos)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
    api.bilibiliStatus().then(setBili).catch(() => setBili(null))
  }, [])

  return (
    <main className="library">
      <section className="hero">
        <h1>卡住了？暂停提问。</h1>
        <p className="muted">粘贴一个 YouTube 或 B站教学视频链接。边看边问，AI 会结合你正在看的那一秒和当前画面来讲解。</p>
        <ImportForm onImported={onOpen} />
        {bili && !bili.logged_in && <div className="notice warning">{BILIBILI_LOGIN_WARNING}</div>}
      </section>
      <section>
        <h2>我的视频</h2>
        {error && <div className="form-error">{error}</div>}
        {videos?.length === 0 && <p className="muted">还没有导入视频。</p>}
        <ul className="video-list">
          {videos &&
            groupLibrary(videos).map((item) =>
              item.kind === 'video' ? (
                <li key={item.video.id}>
                  <Card
                    thumbnail={thumbnailFor(item.video)}
                    source={item.video.source}
                    title={item.video.title || item.video.id}
                    meta={[item.video.channel, item.video.duration ? formatTime(item.video.duration) : '', STATUS_LABELS[item.video.status]]
                      .filter(Boolean)
                      .join(' · ')}
                    onClick={() => onOpen(item.video.id)}
                  />
                </li>
              ) : (
                <li key={item.courseId}>
                  <Card
                    thumbnail={item.thumbnail}
                    source="bilibili"
                    title={item.title || item.courseId}
                    meta={[item.channel, `已导入 ${item.parts.length}/${item.partsTotal} 集`].filter(Boolean).join(' · ')}
                    onClick={() => onOpen(item.latest.id)}
                  />
                </li>
              ),
            )}
        </ul>
      </section>
    </main>
  )
}
