import { describe, expect, it } from 'vitest'
import { SSEParser } from './sse'

describe('SSEParser', () => {
  it('parses complete events', () => {
    const parser = new SSEParser()
    expect(parser.push('data: {"type":"delta","text":"a"}\n\ndata: {"type":"done","chat_id":3}\n\n')).toEqual([
      { type: 'delta', text: 'a' },
      { type: 'done', chat_id: 3 },
    ])
  })

  it('handles events split across chunks', () => {
    const parser = new SSEParser()
    expect(parser.push('data: {"type":"del')).toEqual([])
    expect(parser.push('ta","text":"b"}\n')).toEqual([])
    expect(parser.push('\n')).toEqual([{ type: 'delta', text: 'b' }])
  })

  it('ignores comment blocks and accepts CRLF', () => {
    expect(new SSEParser().push(': ping\r\n\r\ndata: {"type":"delta","text":"c"}\r\n\r\n')).toEqual([
      { type: 'delta', text: 'c' },
    ])
  })

  it('handles a CRLF pair split across chunks', () => {
    const parser = new SSEParser()
    expect(parser.push('data: {"type":"delta","text":"x"}\r\n\r')).toEqual([])
    expect(parser.push('\n')).toEqual([{ type: 'delta', text: 'x' }])
  })

  it('skips a malformed block without dropping other events in the chunk', () => {
    expect(new SSEParser().push('data: {bad\n\ndata: {"type":"delta","text":"ok"}\n\n')).toEqual([
      { type: 'delta', text: 'ok' },
    ])
  })
})
