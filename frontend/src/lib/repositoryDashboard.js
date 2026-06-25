/**
 * @typedef {Object} RepositoryRecord
 * @property {number} id
 * @property {string} name
 * @property {string} source_type
 * @property {string} status
 * @property {string} [channel_kind]
 * @property {string} [catalog_key]
 * @property {Record<string, unknown>} [source_metadata]
 * @property {string} [created_at]
 * @property {string} [updated_at]
 */

/** @typedef {'cloud' | 'local' | 'security'} RepositoryGroupId */

/**
 * @param {RepositoryRecord[]} repositories
 * @returns {{ cloud: RepositoryRecord[], local: RepositoryRecord[], security: RepositoryRecord[] }}
 */
export function groupRepositories(repositories) {
  /** @type {{ cloud: RepositoryRecord[], local: RepositoryRecord[], security: RepositoryRecord[] }} */
  const grouped = { cloud: [], local: [], security: [] }

  for (const repo of repositories || []) {
    const sourceType = String(repo.source_type || '').toLowerCase()
    const catalogKey = String(repo.catalog_key || repo.source_metadata?.catalog_key || '')

    if (sourceType === 'local' || catalogKey === 'repo-local') {
      grouped.local.push(repo)
      continue
    }
    if (sourceType === 'sovereign_sonar' || catalogKey.startsWith('sonar-')) {
      grouped.security.push(repo)
      continue
    }
    grouped.cloud.push(repo)
  }

  return grouped
}

/** @param {string} sourceType */
export function formatRepositorySourceType(sourceType) {
  const token = String(sourceType || '').toLowerCase()
  const labels = {
    local: 'Local vault',
    google_drive: 'Google Drive',
    gmail: 'Gmail',
    external_library: 'External library',
    sovereign_sonar: 'Sovereign Sonar',
  }
  return labels[token] || token.replace(/_/g, ' ') || 'Unknown'
}

/** @param {File} file */
export function isRepositoryUploadMimeAllowed(file) {
  const mime = String(file.type || '').toLowerCase()
  if (mime === 'application/pdf' || mime === 'application/epub+zip') return true
  const name = String(file.name || '').toLowerCase()
  return name.endsWith('.pdf') || name.endsWith('.epub')
}

/** @param {number} bytes */
export function formatUploadBytes(bytes) {
  const value = Number(bytes) || 0
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}
