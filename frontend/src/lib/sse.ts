export type AskEvent =
  | { type: 'delta'; text: string }
  | { type: 'done'; chat_id: number }
  | { type: 'error'; message: string; chat_id?: number }

/** Incremental parser for `data: <json>` server-sent events. */
export class SSEParser {
  private buffer = ''

  push(chunk: string): AskEvent[] {
    // Normalise on the accumulated buffer so a CRLF split across chunks ("\r" | "\n") still matches.
    this.buffer = (this.buffer + chunk).replace(/\r\n/g, '\n')
    const events: AskEvent[] = []
    let end = this.buffer.indexOf('\n\n')
    while (end !== -1) {
      const block = this.buffer.slice(0, end)
      this.buffer = this.buffer.slice(end + 2)
      const data = block
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (data) {
        try {
          events.push(JSON.parse(data) as AskEvent)
        } catch {
          // Skip only this malformed block; keep the rest of the chunk's events.
        }
      }
      end = this.buffer.indexOf('\n\n')
    }
    return events
  }
}
