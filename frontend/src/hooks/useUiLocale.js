import { useEffect, useState } from 'react'
import { getUiLocale } from '../lib/uiStrings.js'

/** Reactive locale — refreshes on lang / storage / ben-ui-locale changes. */
export function useUiLocale() {
  const [locale, setLocale] = useState(() => getUiLocale())

  useEffect(() => {
    const sync = () => setLocale(getUiLocale())
    window.addEventListener('storage', sync)
    window.addEventListener('ben-ui-locale', sync)
    const observer = new MutationObserver(sync)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['lang'] })
    return () => {
      window.removeEventListener('storage', sync)
      window.removeEventListener('ben-ui-locale', sync)
      observer.disconnect()
    }
  }, [])

  return locale
}
