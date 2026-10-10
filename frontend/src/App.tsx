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
