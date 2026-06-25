import { supportsGetUserMedia } from './cameraBridge.js'
import { buildWhatsAppUrl } from './whatsapp.js'

/**
 * Register native hardware bridge capabilities for mobile standalone (PWA) usage.
 * Called once at app bootstrap from main.jsx.
 */
export function registerHardwareBridges() {
  if (typeof window === 'undefined') return

  const standalone =
    window.matchMedia?.('(display-mode: standalone)')?.matches ||
    window.navigator.standalone === true

  window.__BEN_HW__ = {
    version: 1,
    standalone,
    camera: { getUserMedia: supportsGetUserMedia() },
    whatsapp: { scheme: 'https://wa.me/' },
    buildWhatsAppUrl,
  }
}
