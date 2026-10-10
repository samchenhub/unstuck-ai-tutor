export interface PlayerHandle {
  getCurrentTime(): number
  seekTo(seconds: number): void
  pause(): void
}
