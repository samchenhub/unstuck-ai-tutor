import 'katex/dist/katex.min.css'
import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import { linkifyTimestamps, normalizeMathDelimiters, seekSecondsFromHref } from '../lib/timestamps'

interface Props {
  text: string
  onSeek: (seconds: number) => void
}

export function Markdown({ text, onSeek }: Props) {
  const source = useMemo(() => linkifyTimestamps(normalizeMathDelimiters(text)), [text])
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          table: ({ children }) => (
            <div className="table-scroll">
              <table>{children}</table>
            </div>
          ),
          a: ({ href, children }) => {
            const seconds = href ? seekSecondsFromHref(href) : null
            if (seconds === null) {
              return (
                <a href={href} target="_blank" rel="noreferrer">
                  {children}
                </a>
              )
            }
            return (
              <a
                href={href}
                className="ts-link"
                onClick={(event) => {
                  event.preventDefault()
                  onSeek(seconds)
                }}
              >
                {children}
              </a>
            )
          },
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
