/**
 * Native camera bridge for iOS Safari / Android Chrome standalone PWA wrappers.
 * Prefers rear camera via getUserMedia; falls back to file input capture="environment".
 */

export const CAMERA_CAPTURE_ACCEPT = 'image/*'

export function supportsGetUserMedia() {
  return Boolean(
    typeof navigator !== 'undefined' &&
      navigator.mediaDevices &&
      typeof navigator.mediaDevices.getUserMedia === 'function'
  )
}

export function buildEnvironmentCaptureInputAttrs() {
  return {
    type: 'file',
    accept: CAMERA_CAPTURE_ACCEPT,
    capture: 'environment',
  }
}

/**
 * Open rear-facing camera stream with high-resolution preference.
 * @returns {Promise<MediaStream>}
 */
export async function openRearCameraStream() {
  if (!supportsGetUserMedia()) {
    throw new Error('Camera API unavailable on this device')
  }
  const constraints = {
    audio: false,
    video: {
      facingMode: { ideal: 'environment' },
      width: { ideal: 1920, min: 1280 },
      height: { ideal: 1080, min: 720 },
    },
  }
  return navigator.mediaDevices.getUserMedia(constraints)
}

/**
 * Capture a still frame from a video element into a JPEG File.
 * @param {HTMLVideoElement} video
 * @param {string} [filename]
 */
export async function captureVideoFrameToFile(video, filename = 'capture.jpg') {
  const w = video.videoWidth || 1280
  const h = video.videoHeight || 720
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Canvas unavailable')
  ctx.drawImage(video, 0, 0, w, h)

  const blob = await new Promise((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error('Failed to encode image'))),
      'image/jpeg',
      0.92
    )
  })

  return new File([blob], filename, { type: 'image/jpeg', lastModified: Date.now() })
}

export function stopMediaStream(stream) {
  if (!stream) return
  for (const track of stream.getTracks()) {
    track.stop()
  }
}
