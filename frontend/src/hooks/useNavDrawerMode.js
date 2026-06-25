import { useEffect, useState } from 'react'

const DESKTOP_NAV_QUERY = '(min-width: 1025px)'

function readDesktopNav() {
  if (typeof window === 'undefined') return false
  return window.matchMedia(DESKTOP_NAV_QUERY).matches
}

/** Desktop = docked sidebar; tablet/mobile = overlay drawer. */
export function useNavDrawerMode() {
  const [isDesktopNav, setIsDesktopNav] = useState(readDesktopNav)

  useEffect(() => {
    const mq = window.matchMedia(DESKTOP_NAV_QUERY)
    const onChange = (event) => setIsDesktopNav(event.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  return {
    isDesktopNav,
    isOverlayNav: !isDesktopNav,
  }
}

export function readInitialNavDrawerOpen() {
  return readDesktopNav()
}
