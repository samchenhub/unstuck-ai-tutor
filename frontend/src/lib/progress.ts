import type { Stage, StageState, Video } from './api'
import { formatTime } from './timestamps'

const STATE_LABELS: Record<StageState, string> = {
  pending: '等待',
  running: '进行中',
  done: '完成',
  failed: '失败',
  skipped: '跳过',
}

function transcribing(video: Video): boolean {
  return video.stage_states.transcript === 'running' && video.transcribed_until > 0
}

export function stageStateLabel(video: Video, stage: Stage): string {
  const state = video.stage_states[stage] ?? 'pending'
  if (stage === 'transcript' && transcribing(video)) return `已转写 ${formatTime(video.transcribed_until)}`
  return STATE_LABELS[state]
}

export function importHeadline(video: Video): string {
  if (video.status === 'failed') return '导入失败'
  if (video.status === 'ready') return '导入完成，但有部分步骤失败'
  if (!video.ask_ready) return '正在导入…'
  if (transcribing(video)) {
    return `已经可以提问了；字幕还在转写（已到 ${formatTime(video.transcribed_until)}），画面理解稍后进行`
  }
  return '字幕已就绪，可以开始提问；画面理解还在后台进行'
}
