import { useCallback, useEffect, useRef, useState } from 'react'
import {
  captureVideoFrameToFile,
  openRearCameraStream,
  stopMediaStream,
} from '../mobile/cameraBridge.js'

export function CameraCaptureModal({ open, title, onClose, onCapture, onError }) {
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const [ready, setReady] = useState(false)
  const [busy, setBusy] = useState(false)

  const teardown = useCallback(() => {
    stopMediaStream(streamRef.current)
    streamRef.current = null
    setReady(false)
  }, [])

  useEffect(() => {
    if (!open) {
      teardown()
      return undefined
    }

    let cancelled = false
    setBusy(true)

    openRearCameraStream()
      .then((stream) => {
        if (cancelled) {
          stopMediaStream(stream)
          return
        }
        streamRef.current = stream
        const video = videoRef.current
        if (video) {
          video.srcObject = stream
          video.play().then(() => setReady(true)).catch(() => setReady(true))
        }
      })
      .catch((err) => {
        onError?.(err)
        onClose?.()
      })
      .finally(() => setBusy(false))

    return () => {
      cancelled = true
      teardown()
    }
  }, [open, onClose, onError, teardown])

  const handleShutter = async () => {
    const video = videoRef.current
    if (!video || !ready) return
    setBusy(true)
    try {
      const file = await captureVideoFrameToFile(video, `capture-${Date.now()}.jpg`)
      onCapture?.(file)
      onClose?.()
    } catch (err) {
      onError?.(err)
    } finally {
      setBusy(false)
    }
  }

  if (!open) return null

  return (
    <div className="hw-modal-overlay" role="dialog" aria-modal="true" aria-label={title || 'Camera capture'}>
      <div className="hw-modal hw-modal--camera">
        <header className="hw-modal__header">
          <h2 className="hw-modal__title">{title || 'Rear camera capture'}</h2>
          <button type="button" className="hw-modal__close" onClick={onClose} aria-label="Close camera">
            ×
          </button>
        </header>
        <div className="hw-modal__body">
          <video
            ref={videoRef}
            className="hw-modal__video"
            playsInline
            muted
            autoPlay
          />
          {!ready && !busy ? (
            <p className="hw-modal__hint">Initializing rear camera…</p>
          ) : null}
        </div>
        <footer className="hw-modal__footer">
          <button type="button" className="hw-modal__btn hw-modal__btn--ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="hw-modal__btn hw-modal__btn--primary"
            onClick={handleShutter}
            disabled={!ready || busy}
          >
            {busy ? '…' : 'Capture photo'}
          </button>
        </footer>
      </div>
    </div>
  )
}
