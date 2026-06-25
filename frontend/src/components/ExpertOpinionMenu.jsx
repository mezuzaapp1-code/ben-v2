import { useCallback, useRef, useState } from 'react'
import { getSpeakingProviders } from '../providers/providerRegistry.js'
import { useDismissOnOutside } from '../hooks/useDismissOnOutside.js'
import './ExpertOpinionMenu.css'

/**
 * Popover to request a guest expert opinion anchored to a message row.
 */
export function ExpertOpinionMenu({
  disabled = false,
  anchorMessageId = null,
  onRequest,
}) {
  const [open, setOpen] = useState(false)
  const [providerId, setProviderId] = useState('claude')
  const wrapRef = useRef(null)
  const triggerRef = useRef(null)
  const providers = getSpeakingProviders()

  const close = useCallback(() => setOpen(false), [])

  useDismissOnOutside({
    open,
    onDismiss: close,
    containerRef: wrapRef,
    triggerRef,
  })

  const canRequest = Boolean(anchorMessageId) && !disabled

  const run = (opinionMode) => {
    if (!canRequest) return
    close()
    onRequest?.({ providerId, opinionMode })
  }

  return (
    <div ref={wrapRef} className="expert-opinion-menu">
      <button
        ref={triggerRef}
        type="button"
        className="expert-opinion-menu__trigger"
        disabled={!canRequest}
        aria-label="Request expert opinion"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => {
          if (!canRequest) return
          setOpen((value) => !value)
        }}
      >
        <span className="expert-opinion-menu__icon" aria-hidden="true">
          ❖
        </span>
        <span>בקש חוות דעת</span>
      </button>
      {open ? (
        <div className="expert-opinion-menu__popover" role="menu" aria-label="Expert opinion">
          <span className="expert-opinion-menu__title">Guest engine</span>
          <div className="expert-opinion-menu__providers" role="group" aria-label="Provider">
            {providers.map((provider) => (
              <button
                key={provider.id}
                type="button"
                role="menuitemradio"
                aria-checked={providerId === provider.id}
                className={`expert-opinion-menu__provider${providerId === provider.id ? ' expert-opinion-menu__provider--active' : ''}`}
                style={{ '--engine-accent': provider.accent }}
                onClick={() => setProviderId(provider.id)}
              >
                {provider.shortLabel ?? provider.label}
              </button>
            ))}
          </div>
          <div className="expert-opinion-menu__actions">
            <button
              type="button"
              role="menuitem"
              className="expert-opinion-menu__action"
              onClick={() => run('single')}
            >
              Single opinion
            </button>
            <button
              type="button"
              role="menuitem"
              className="expert-opinion-menu__action expert-opinion-menu__action--panel"
              onClick={() => run('panel')}
            >
              Start panel discussion
            </button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
