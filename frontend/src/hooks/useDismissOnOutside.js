import { useEffect, useRef } from 'react'

/**
 * Closes overlays on the first outside pointerdown without fighting the opening tap.
 * Uses capture phase + a short defer so the gesture that opened the layer is ignored.
 */
export function useDismissOnOutside({ open, onDismiss, containerRef, triggerRef, triggerRefs = [] }) {
  const onDismissRef = useRef(onDismiss)
  onDismissRef.current = onDismiss

  useEffect(() => {
    if (!open) return undefined

    let armed = false
    const armTimer = window.setTimeout(() => {
      armed = true
    }, 0)

    const triggers = [triggerRef, ...triggerRefs]

    const onPointerDown = (event) => {
      if (!armed) return
      const target = event.target
      if (!(target instanceof Node)) return
      if (containerRef?.current?.contains(target)) return
      if (triggers.some((ref) => ref?.current?.contains(target))) return
      onDismissRef.current?.()
    }

    document.addEventListener('pointerdown', onPointerDown, true)

    return () => {
      window.clearTimeout(armTimer)
      document.removeEventListener('pointerdown', onPointerDown, true)
    }
  }, [open, containerRef, triggerRef, triggerRefs])
}
