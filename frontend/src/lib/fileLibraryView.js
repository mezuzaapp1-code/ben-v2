/**
 * File Library presentation filters. These never mutate the shared inventory.
 * Empty copy must not claim the workspace is empty when inventory.rows is not.
 */
import { deriveFileStage } from './fileStatus.js'

/** Reopen always returns to All files + cleared search (behavior A). */
export const FILE_LIBRARY_REOPEN_RESETS_TO_ALL = true

export const FILE_LIBRARY_EMPTY = {
  noWorkspace: 'Select an active workspace/project to open the File Library.',
  inventoryEmpty: 'No files yet. Upload a document to start this workspace library.',
  processing: 'No files currently processing.',
  failed: 'No failed files.',
  noMatches: 'No matching files.',
}

export function filterLibraryItems(items, view = 'all', query = '') {
  let list = Array.isArray(items) ? items.slice() : []
  const needle = String(query || '').trim().toLowerCase()
  if (needle) {
    list = list.filter((item) => {
      const name = `${item.display_name || ''} ${item.original_filename || ''}`.toLowerCase()
      return name.includes(needle)
    })
  }
  if (view === 'processing') {
    list = list.filter((item) => {
      const stage = deriveFileStage(item, { upload: item.upload })
      return (
        stage === 'uploading' ||
        stage === 'queued' ||
        stage === 'extracting' ||
        stage === 'indexing'
      )
    })
  } else if (view === 'failed') {
    list = list.filter((item) => deriveFileStage(item, { upload: item.upload }) === 'failed')
  } else if (view === 'recent') {
    list = list.slice(0, 20)
  }
  return list
}

export function fileLibraryEmptyMessage({
  workspaceId = null,
  view = 'all',
  query = '',
  inventoryCount = 0,
  visibleCount = 0,
} = {}) {
  if (!workspaceId) return FILE_LIBRARY_EMPTY.noWorkspace
  if (visibleCount > 0) return null
  const needle = String(query || '').trim()
  if (needle) return FILE_LIBRARY_EMPTY.noMatches
  const count = Number(inventoryCount) || 0
  if (view === 'processing') return FILE_LIBRARY_EMPTY.processing
  if (view === 'failed') return FILE_LIBRARY_EMPTY.failed
  if (count > 0 && view === 'all') return null
  if (count > 0) return FILE_LIBRARY_EMPTY.noMatches
  return FILE_LIBRARY_EMPTY.inventoryEmpty
}
