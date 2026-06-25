import { forwardRef, useCallback, useId, useImperativeHandle, useRef, useState } from 'react'
import { useDismissOnOutside } from '../hooks/useDismissOnOutside.js'
import {
  buildEnvironmentCaptureInputAttrs,
  supportsGetUserMedia,
} from '../mobile/cameraBridge.js'
import { CameraCaptureModal } from './CameraCaptureModal.jsx'

/**
 * Dual-path camera bridge: getUserMedia rear camera modal + native file capture fallback.
 */
export const CameraCaptureInput = forwardRef(function CameraCaptureInput(
  {
    onFile,
    onError,
    disabled = false,
    mode = 'environment',
    className = '',
    triggerClassName = 'hw-capture-trigger',
    children,
    menuClassName = 'hw-capture-menu',
  },
  ref
) {
  const inputId = useId()
  const fileRef = useRef(null)
  const wrapRef = useRef(null)
  const triggerRef = useRef(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [cameraOpen, setCameraOpen] = useState(false)

  const inputAttrs = buildEnvironmentCaptureInputAttrs()

  const closeMenu = useCallback(() => setMenuOpen(false), [])

  useDismissOnOutside({
    open: menuOpen,
    onDismiss: closeMenu,
    containerRef: wrapRef,
    triggerRef,
  })

  const deliver = useCallback(
    (file) => {
      if (file) onFile?.(file)
    },
    [onFile]
  )

  const handleNativeInput = (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    closeMenu()
    deliver(file)
  }

  const openNativePicker = useCallback(() => {
    closeMenu()
    fileRef.current?.click()
  }, [closeMenu])

  const openCameraModal = useCallback(() => {
    closeMenu()
    if (supportsGetUserMedia()) {
      setCameraOpen(true)
    } else {
      openNativePicker()
    }
  }, [openNativePicker])

  const handleTrigger = (event) => {
    event?.stopPropagation?.()
    if (disabled) return
    if (supportsGetUserMedia()) {
      setMenuOpen((v) => !v)
    } else {
      openNativePicker()
    }
  }

  const openCapture = useCallback(() => {
    if (disabled) return
    if (supportsGetUserMedia()) {
      setMenuOpen(true)
    } else {
      openNativePicker()
    }
  }, [disabled, openNativePicker])

  useImperativeHandle(
    ref,
    () => ({
      open: openCapture,
      openNativePicker,
      openCameraModal,
    }),
    [openCapture, openNativePicker, openCameraModal]
  )

  return (
    <>
      <input
        id={inputId}
        ref={fileRef}
        {...inputAttrs}
        className="receipt-file-input"
        aria-hidden="true"
        tabIndex={-1}
        disabled={disabled}
        onChange={handleNativeInput}
      />
      <div ref={wrapRef} className={`hw-capture-wrap ${className}`.trim()}>
        <button
          ref={triggerRef}
          type="button"
          className={triggerClassName}
          onClick={handleTrigger}
          disabled={disabled}
          aria-haspopup={supportsGetUserMedia() ? 'menu' : undefined}
          aria-expanded={menuOpen}
        >
          {children}
        </button>
        {menuOpen ? (
          <div className={`${menuClassName} hw-capture-menu--open`.trim()} role="menu">
            <button type="button" className="hw-capture-menu__item" role="menuitem" onClick={openCameraModal}>
              Rear camera (HD)
            </button>
            <button type="button" className="hw-capture-menu__item" role="menuitem" onClick={openNativePicker}>
              Gallery / native capture
            </button>
          </div>
        ) : null}
      </div>
      <CameraCaptureModal
        open={cameraOpen}
        title={mode === 'certification' ? 'Capture certification' : 'Capture document'}
        onClose={() => setCameraOpen(false)}
        onCapture={deliver}
        onError={(err) => {
          setCameraOpen(false)
          onError?.(err)
          openNativePicker()
        }}
      />
    </>
  )
})
