import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchPlatformActiveFeatures } from '../api/platformCapabilities.js'

const EMPTY_SNAPSHOT = Object.freeze({
  org_id: null,
  catalog_keys: [],
  engines: [],
  integrations: [],
  active_features: [],
  total_active: 0,
  total_configured: 0,
})

/**
 * Hydrate org-scoped platform capabilities from system_main.db.
 * @param {(() => Promise<Record<string, string>>) | null | undefined} buildHeaders
 */
export function usePlatformActiveFeatures(buildHeaders) {
  const [snapshot, setSnapshot] = useState(EMPTY_SNAPSHOT)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const reload = useCallback(async () => {
    if (!buildHeaders) {
      setSnapshot(EMPTY_SNAPSHOT)
      setError(null)
      return EMPTY_SNAPSHOT
    }

    setLoading(true)
    setError(null)
    try {
      const headers = await buildHeaders()
      const data = await fetchPlatformActiveFeatures(headers)
      const next = {
        org_id: data.org_id ?? null,
        catalog_keys: Array.isArray(data.catalog_keys) ? data.catalog_keys : [],
        engines: Array.isArray(data.engines) ? data.engines : [],
        integrations: Array.isArray(data.integrations) ? data.integrations : [],
        active_features: Array.isArray(data.active_features) ? data.active_features : [],
        total_active: Number(data.total_active) || 0,
        total_configured: Number(data.total_configured) || 0,
      }
      setSnapshot(next)
      return next
    } catch (err) {
      setSnapshot(EMPTY_SNAPSHOT)
      setError(err?.message || 'Could not load platform capabilities')
      return EMPTY_SNAPSHOT
    } finally {
      setLoading(false)
    }
  }, [buildHeaders])

  useEffect(() => {
    void reload()
  }, [reload])

  const catalogKeySet = useMemo(
    () => new Set(snapshot.catalog_keys.map((key) => String(key || '').trim()).filter(Boolean)),
    [snapshot.catalog_keys]
  )

  return {
    snapshot,
    catalogKeys: snapshot.catalog_keys,
    catalogKeySet,
    engines: snapshot.engines,
    integrations: snapshot.integrations,
    activeFeatures: snapshot.active_features,
    loading,
    error,
    reload,
  }
}
