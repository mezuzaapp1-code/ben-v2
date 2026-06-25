import './SystemTelemetryBadge.css'

/** In-channel system telemetry while agent tools execute. */
export function SystemTelemetryBadge({ message, active = true }) {
  if (!message) return null
  return (
    <div className={`system-telemetry${active ? ' system-telemetry--active' : ''}`} role="status" aria-live="polite">
      <span className="system-telemetry__spinner" aria-hidden="true" />
      <span className="system-telemetry__text">{message}</span>
    </div>
  )
}
