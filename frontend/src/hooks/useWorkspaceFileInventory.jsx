import { useSyncExternalStore } from 'react'
import {
  listWorkspaceFiles,
  uploadWorkspaceFile,
} from '../api/workspaceFiles.js'
import { createWorkspaceFileInventory } from '../lib/workspaceFileInventory.js'

export const workspaceFileInventory = createWorkspaceFileInventory({
  listFiles: listWorkspaceFiles,
  uploadFile: uploadWorkspaceFile,
})

export function useWorkspaceFileInventory() {
  return useSyncExternalStore(
    workspaceFileInventory.subscribe,
    workspaceFileInventory.getSnapshot,
    workspaceFileInventory.getSnapshot
  )
}
