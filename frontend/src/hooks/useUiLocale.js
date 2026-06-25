import { useEffect, useState } from 'react'
import { getUiLocale } from '../lib/uiStrings.js'

/** Reactive locale — refreshes on `lang` attribute or `ben-ui-locale` storage changes. */
export function useUiLocale() {
  const [locale, setLocale] = useState(() => getUiLocale())

  useEffect(() => {
    const sync = () => setLocale(getUiLocale())
    window.addEventListener('storage', sync)
    const observer = new MutationObserver(sync)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['lang'] })
    return () => {
      window.removeEventListener('storage', sync)
      observer.disconnect()
    }
  }, [])

  return locale
}
