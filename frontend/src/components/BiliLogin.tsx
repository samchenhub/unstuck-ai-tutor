import { useEffect, useRef, useState } from 'react'
import { api, type BilibiliStatus, type QrLoginState } from '../lib/api'
import { bilibiliLoginWarning, qrLoginText } from '../lib/library'

const POLL_MS = 2000

/** Bilibili login state on the library page, with a QR-code login (scan with the Bilibili app). */
export function BiliLogin({ status, onChange }: { status: BilibiliStatus | null; onChange: () => void }) {
  const [qr, setQr] = useState<{ key: string; image: string } | null>(null)
  const [state, setState] = useState<QrLoginState>('waiting')
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)
  const active = useRef<string | null>(null) // the QR key being polled; results for any other key are stale

  useEffect(
    () => () => {
      active.current = null
      window.clearTimeout(timer.current)
    },
    [],
  )

  const poll = (key: string) => {
    timer.current = window.setTimeout(async () => {
      try {
        const { state: next } = await api.bilibiliLoginPoll(key)
        if (active.current !== key) return
        setState(next)
        if (next === 'done') {
          active.current = null
          setQr(null)
          onChange()
        } else if (next === 'waiting' || next === 'scanned') {
          poll(key)
        }
      } catch (err) {
        if (active.current !== key) return
        setError(err instanceof Error ? err.message : String(err))
      }
    }, POLL_MS)
  }

  const start = async () => {
    if (starting) return
    window.clearTimeout(timer.current)
    active.current = null
    setStarting(true)
    setError(null)
    setState('waiting')
    try {
      const started = await api.bilibiliLoginStart()
      active.current = started.key
      setQr({ key: started.key, image: started.qr })
      poll(started.key)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setStarting(false)
    }
  }

  const logout = async () => {
    try {
      await api.bilibiliLogout()
      onChange()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const warning = bilibiliLoginWarning(status)
  if (!warning) {
    return status?.login === 'qr' ? (
      <p className="muted bili-login-ok">
        已扫码登录B站 ·{' '}
        <button type="button" className="link-button" onClick={logout}>
          退出登录
        </button>
      </p>
    ) : null
  }
  return (
    <div className="notice warning bili-login">
      <div className="bili-login-row">
        <span>{warning}</span>
        {!qr && (
          <button type="button" className="btn btn-small" onClick={start} disabled={starting}>
            {starting ? '获取中…' : '扫码登录'}
          </button>
        )}
      </div>
      {qr && (
        <div className="bili-login-qr">
          <img src={qr.image} alt="B站登录二维码" width={180} height={180} />
          <div>
            <p aria-live="polite">{qrLoginText(state)}</p>
            {(state === 'expired' || state === 'failed' || error) && (
              <button type="button" className="btn btn-small" onClick={start} disabled={starting}>
                重新获取二维码
              </button>
            )}
          </div>
        </div>
      )}
      {error && <div className="form-error">{error}</div>}
    </div>
  )
}
