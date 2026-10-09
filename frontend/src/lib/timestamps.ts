const TIMESTAMP_RE = /\[(?:(\d{1,2}):)?(\d{1,2}):([0-5]\d)\](?!\()/g
const CODE_RE = /(```[\s\S]*?```|`[^`\n]*`)/g

function outsideCode(md: string, transform: (text: string) => string): string {
  return md
    .split(CODE_RE)
    .map((part, i) => (i % 2 === 1 ? part : transform(part)))
    .join('')
}

function toSeconds(hours: string | undefined, minutes: string, seconds: string): number {
  return (hours ? Number(hours) * 3600 : 0) + Number(minutes) * 60 + Number(seconds)
}

export function formatTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const h = Math.floor(total / 3600)
  const mm = String(Math.floor((total % 3600) / 60)).padStart(2, '0')
  const ss = String(total % 60).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

/** Turn [mm:ss] / [h:mm:ss] into markdown links "#t=<seconds>" (code is left untouched). */
export function linkifyTimestamps(md: string): string {
  return outsideCode(md, (text) =>
    text.replace(
      TIMESTAMP_RE,
      (match: string, h: string | undefined, m: string, s: string) => `${match}(#t=${toSeconds(h, m, s)})`,
    ),
  )
}

export function seekSecondsFromHref(href: string): number | null {
  const match = /^#t=(\d+(?:\.\d+)?)$/.exec(href)
  return match ? Number(match[1]) : null
}

/** Models sometimes write \( \) and \[ \]; remark-math only understands $ and $$. */
export function normalizeMathDelimiters(md: string): string {
  return outsideCode(md, (text) =>
    text
      .replace(/\\\[([\s\S]+?)\\\]/g, (_m: string, body: string) => `$$${body}$$`)
      .replace(/\\\(([\s\S]+?)\\\)/g, (_m: string, body: string) => `$${body}$`),
  )
}
