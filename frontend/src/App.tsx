import { useEffect, useState } from 'react'
import { Library } from './components/Library'
import { VideoPage } from './components/VideoPage'
import { openVideo, parseRoute } from './lib/videoId'

const readVideoId = () => parseRoute(window.location.hash)

export default function App() {
  const [videoId, setVideoId] = useState(readVideoId)

  useEffect(() => {
    const onHashChange = () => setVideoId(readVideoId())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#">
          Unstuck
        </a>
        <span className="tagline">Stuck on a video? Pause and ask.</span>
      </header>
      {videoId ? <VideoPage key={videoId} videoId={videoId} /> : <Library onOpen={openVideo} />}
    </div>
  )
}
