import { useCallback, useLayoutEffect, useRef } from 'react'

/**
 * Keeps a textarea height in sync with content up to maxRows without layout jumps.
 */
export function useAutoResizeTextarea(value, { minRows = 1, maxRows = 6 } = {}) {
  const ref = useRef(null)

  const syncHeight = useCallback(() => {
    const el = ref.current
    if (!el) return
    el.style.height = '0px'
    const styles = window.getComputedStyle(el)
    const lineHeight = Number.parseFloat(styles.lineHeight) || 20
    const padding =
      Number.parseFloat(styles.paddingTop) + Number.parseFloat(styles.paddingBottom)
    const minHeight = lineHeight * minRows + padding
    const maxHeight = lineHeight * maxRows + padding
    const next = Math.min(Math.max(el.scrollHeight, minHeight), maxHeight)
    el.style.height = `${next}px`
    el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden'
  }, [minRows, maxRows])

  useLayoutEffect(() => {
    syncHeight()
  }, [value, syncHeight])

  return { ref, syncHeight }
}
