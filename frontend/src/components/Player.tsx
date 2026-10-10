import { forwardRef } from 'react'
import { sourceOfId } from '../lib/videoId'
import { BiliPlayer } from './BiliPlayer'
import type { PlayerHandle } from './playerHandle'
import { YouTubePlayer } from './YouTubePlayer'

export type { PlayerHandle }

export const Player = forwardRef<PlayerHandle, { videoId: string }>(function Player({ videoId }, ref) {
  return sourceOfId(videoId) === 'bilibili' ? (
    <BiliPlayer ref={ref} videoId={videoId} />
  ) : (
    <YouTubePlayer ref={ref} videoId={videoId} />
  )
})
