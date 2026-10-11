import { STAGE_LABELS, STAGES, type Stage, type StageState, type Video } from './api'
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
  if (state === 'failed' && video.auto_retry_at > 0 && video.status !== 'importing') return '等待重试'
  if (stage === 'transcript' && transcribing(video)) return `已转写 ${formatTime(video.transcribed_until)}`
  return STATE_LABELS[state]
}

function retryHeadline(video: Video, nowMs: number): string {
  const failed = STAGES.filter((stage) => video.stage_states[stage] === 'failed').map((stage) => STAGE_LABELS[stage])
  const minutes = Math.ceil((video.auto_retry_at * 1000 - nowMs) / 60_000)
  const when = minutes > 0 ? `约 ${minutes} 分钟后` : '马上'
  return `免费模型暂时限流，${when}自动重试${failed.join('、')}；现在已经可以提问`
}

export function importHeadline(video: Video, nowMs: number = Date.now()): string {
  if (video.status === 'ready' && video.auto_retry_at > 0) return retryHeadline(video, nowMs)
  if (video.status === 'failed') return '导入失败'
  if (video.status === 'ready') return '导入完成，但有部分步骤失败'
  if (!video.ask_ready) return '正在导入…'
  if (transcribing(video)) {
    return `已经可以提问了；字幕还在转写（已到 ${formatTime(video.transcribed_until)}），画面理解稍后进行`
  }
  return '字幕已就绪，可以开始提问；画面理解还在后台进行'
}
