export interface YTPlayer {
  getCurrentTime(): number
  seekTo(seconds: number, allowSeekAhead: boolean): void
  playVideo(): void
  pauseVideo(): void
  destroy(): void
}

interface YTPlayerOptions {
  videoId: string
  width?: string
  height?: string
  playerVars?: Record<string, string | number>
  events?: { onReady?: () => void }
}

export interface YTNamespace {
  Player: new (element: HTMLElement, options: YTPlayerOptions) => YTPlayer
}

declare global {
  interface Window {
    YT?: YTNamespace
    onYouTubeIframeAPIReady?: () => void
  }
}

const LOAD_TIMEOUT_MS = 15_000
const LOAD_ERROR_MESSAGE = 'YouTube 播放器加载失败，请检查网络后刷新页面'

let loading: Promise<YTNamespace> | null = null

/**
 * Load https://www.youtube.com/iframe_api once and resolve when it is ready.
 * Rejects on script error or after 15s; the cached promise is then cleared so a later call retries.
 */
export function loadYouTubeApi(): Promise<YTNamespace> {
  if (window.YT?.Player) return Promise.resolve(window.YT)
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const previous = window.onYouTubeIframeAPIReady
      const script = document.createElement('script')
      let timer: ReturnType<typeof setTimeout> | undefined

      const fail = () => {
        clearTimeout(timer)
        window.onYouTubeIframeAPIReady = previous
        script.remove()
        loading = null
        reject(new Error(LOAD_ERROR_MESSAGE))
      }

      window.onYouTubeIframeAPIReady = () => {
        clearTimeout(timer)
        previous?.()
        resolve(window.YT as YTNamespace)
      }
      script.onerror = fail
      script.src = 'https://www.youtube.com/iframe_api'
      timer = setTimeout(fail, LOAD_TIMEOUT_MS)
      document.head.appendChild(script)
    })
  }
  return loading
}
