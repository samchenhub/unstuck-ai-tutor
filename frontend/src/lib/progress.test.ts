import { describe, expect, it } from 'vitest'
import type { Video } from './api'
import { importHeadline, stageStateLabel } from './progress'

function video(over: Partial<Video>): Video {
  return {
    id: 'BV1Ex4y1G7nG_p1', url: '', title: 'T', channel: 'C', duration: 6298, status: 'importing', ask_ready: false,
    stage_states: {}, visual_mode: 'full', outline: null, error_code: null, error_message: null, created_at: '',
    source: 'bilibili', course_id: 'BV1Ex4y1G7nG', part: 1, part_title: '', parts_total: 1, thumbnail: '',
    transcribed_until: 0, auto_retry_at: 0, auto_retries: 0, ...over,
  }
}

describe('stageStateLabel', () => {
  it('shows how far local transcription has got', () => {
    const v = video({ stage_states: { transcript: 'running' }, transcribed_until: 1390 })
    expect(stageStateLabel(v, 'transcript')).toBe('已转写 23:10')
    expect(stageStateLabel(video({ stage_states: { transcript: 'running' } }), 'transcript')).toBe('进行中')
    expect(stageStateLabel(video({ stage_states: { transcript: 'done' }, transcribed_until: 6298 }), 'transcript')).toBe('完成')
    expect(stageStateLabel(video({}), 'download')).toBe('等待')
  })
})

describe('importHeadline', () => {
  it('says asking is open while transcription continues', () => {
    expect(importHeadline(video({ ask_ready: true, stage_states: { transcript: 'running' }, transcribed_until: 300 }))).toBe(
      '已经可以提问了；字幕还在转写（已到 05:00），画面理解稍后进行',
    )
    expect(importHeadline(video({ ask_ready: true, stage_states: { transcript: 'done' } }))).toBe(
      '字幕已就绪，可以开始提问；画面理解还在后台进行',
    )
    expect(importHeadline(video({}))).toBe('正在导入…')
    expect(importHeadline(video({ status: 'failed' }))).toBe('导入失败')
    expect(importHeadline(video({ status: 'ready' }))).toBe('导入完成，但有部分步骤失败')
  })
})

describe('automatic retry after a rate limit', () => {
  const limited = video({
    status: 'ready', ask_ready: true, error_code: 'MODEL_BUSY', auto_retry_at: 1_000_000 + 290,
    stage_states: { transcript: 'done', vision: 'failed', outline: 'failed', timeline: 'done' },
  })

  it('labels failed stages as waiting for the retry', () => {
    expect(stageStateLabel(limited, 'vision')).toBe('等待重试')
    expect(stageStateLabel(limited, 'timeline')).toBe('完成')
    expect(stageStateLabel({ ...limited, auto_retry_at: 0 }, 'vision')).toBe('失败')
  })

  it('says when the next attempt happens', () => {
    expect(importHeadline(limited, 1_000_000 * 1000)).toBe('免费模型暂时限流，约 5 分钟后自动重试画面理解、大纲；现在已经可以提问')
    expect(importHeadline({ ...limited, auto_retry_at: 1_000_000 - 5 }, 1_000_000 * 1000)).toBe(
      '免费模型暂时限流，马上自动重试画面理解、大纲；现在已经可以提问',
    )
  })
})
