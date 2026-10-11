import { useEffect, useRef, useState } from 'react'
import { api, type BilibiliStatus, type QrLoginState } from '../lib/api'
import { bilibiliLoginWarning, qrLoginText } from '../lib/library'

const POLL_MS = 2000

/** Bilibili login state on the library page, with a QR-code login (scan with the Bilibili app). */
export function BiliLogin({ status, onChange }: { status: BilibiliStatus | null; onChange: () => void }) {
  const [qr, setQr] = useState<{ key: string; image: string } | null>(null)
  const [state, setState] = useState<QrLoginState>('waiting')
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const poll = (key: string) => {
    timer.current = window.setTimeout(async () => {
      try {
        const { state: next } = await api.bilibiliLoginPoll(key)
        setState(next)
        if (next === 'done') {
          setQr(null)
          onChange()
        } else if (next === 'waiting' || next === 'scanned') {
          poll(key)
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
    }, POLL_MS)
  }

  const start = async () => {
    window.clearTimeout(timer.current)
    setError(null)
    setState('waiting')
    try {
      const started = await api.bilibiliLoginStart()
      setQr({ key: started.key, image: started.qr })
      poll(started.key)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
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
          <button type="button" className="btn btn-small" onClick={start}>
            扫码登录
          </button>
        )}
      </div>
      {qr && (
        <div className="bili-login-qr">
          <img src={qr.image} alt="B站登录二维码" width={180} height={180} />
          <div>
            <p>{qrLoginText(state)}</p>
            {(state === 'expired' || state === 'failed') && (
              <button type="button" className="btn btn-small" onClick={start}>
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
