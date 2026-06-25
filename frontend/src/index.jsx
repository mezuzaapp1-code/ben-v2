/**
 * Mobile hardware bridge registration — imported at bootstrap (main.jsx).
 */
export { registerHardwareBridges } from './mobile/registerHardwareBridges.js'
export { buildWhatsAppUrl, openWhatsAppDeepLink } from './mobile/whatsapp.js'
export {
  supportsGetUserMedia,
  buildEnvironmentCaptureInputAttrs,
  openRearCameraStream,
  captureVideoFrameToFile,
} from './mobile/cameraBridge.js'
