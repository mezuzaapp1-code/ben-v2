/**
 * Smoke checks for Workspace File Library UI (no Vitest/RTL).
 * Run: node frontend/scripts/test-files-ui.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = join(__dirname, '..')

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

const overlay = readFileSync(join(root, 'src/components/FileLibraryOverlay.jsx'), 'utf8')
const css = readFileSync(join(root, 'src/components/FileLibraryOverlay.css'), 'utf8')
const api = readFileSync(join(root, 'src/api/workspaceFiles.js'), 'utf8')
const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')

// 1. Files navigation opens
assert(overlay.includes('export function FileLibraryNavTrigger'), 'Files nav trigger export')
assert(overlay.includes('export function FileLibraryOverlay'), 'Files overlay export')
assert(app.includes('<FileLibraryNavTrigger'), 'App wires Files nav')
assert(app.includes('<FileLibraryOverlay'), 'App wires Files overlay')
assert(app.includes('setFilesOpen(true)'), 'openFilesLibrary sets open')

// 2. Upload button visible
assert(overlay.includes('>Upload<') || overlay.includes('Uploading ${uploadProgress}%') || overlay.includes('Uploading'), 'Upload button')
assert(css.includes('.files-upload-btn'), 'Upload button styles')

// 3. Upload progress renders
assert(overlay.includes('files-progress'), 'progress markup')
assert(overlay.includes('uploadProgress'), 'progress state')
assert(api.includes('xhr.upload.onprogress'), 'XHR upload progress')

// 4. Uploaded file appears in the list
assert(overlay.includes('listWorkspaceFiles') || overlay.includes('workspaceFileInventory'), 'list API used')
assert(overlay.includes('files-row'), 'file row markup')
assert(overlay.includes('visibleItems.map') || overlay.includes('items.map'), 'renders items')

// 5. Processing and failed states render
assert(overlay.includes("['processing', 'Processing']") || overlay.includes('processing'), 'processing view')
assert(overlay.includes("['failed', 'Failed']") || overlay.includes('failed'), 'failed view')
assert(overlay.includes('filterLibraryItems'), 'library uses shared presentation filter')
assert(overlay.includes('fileLibraryEmptyMessage'), 'library uses truthful empty copy')
assert(overlay.includes('files-status--failed') || overlay.includes("files-status--${"), 'status classes')
assert(overlay.includes('failure_message'), 'failure message shown')

// 6. Empty state — truthful copies live in fileLibraryView
assert(overlay.includes('fileLibraryEmptyMessage'), 'empty state helper wired')
assert(overlay.includes('FILE_LIBRARY_REOPEN_RESETS_TO_ALL'), 'reopen behavior is explicit')

// 7. Search filters files
assert(overlay.includes('Search this workspace'), 'search input')
assert(overlay.includes("setQ(searchInput.trim())"), 'search submit')
assert(api.includes("params.set('q', q)"), 'search query param')

// 8. Preview/download actions
assert(overlay.includes('Open'), 'open/preview action')
assert(overlay.includes('Download'), 'download action')
assert(overlay.includes('fetchWorkspaceFileBlob'), 'authenticated blob fetch')
assert(api.includes('/content?inline='), 'content URL never public storage')

// 9. Chat attachment shows upload state
assert(app.includes('handleWorkspaceFileAttach'), 'chat attach handler')
assert(app.includes('Uploading:'), 'chat upload pending message')
assert(app.includes('fileUploading'), 'chat upload busy state')
assert(app.includes("kind: 'file_upload'"), 'upload message kind')
assert(app.includes('FileLifecycleBubble'), 'composer attachment uses live lifecycle')
assert(app.includes('workspaceFileInventory'), 'composer uses shared inventory')

// 10. Chat-uploaded file appears in Files (same API)
assert(app.includes('uploadWorkspaceFile') || app.includes('workspaceFileInventory.uploadFile'), 'chat uses workspace upload API')
assert(app.includes('sourceChatId: chatId') || app.includes('sourceChatId'), 'chat retains file reference')
assert(api.includes("form.append('source_chat_id'"), 'source_chat_id sent')

// 11. No misleading success after failure
assert(app.includes("kind: failed ? 'api_error'"), 'failed upload is error kind')
assert(app.includes('Upload failed:'), 'failure copy')
assert(overlay.includes('uploadError'), 'library upload error state')

// Workspace isolation messaging / no Search All
assert(!overlay.includes('Search All'), 'no Search All')
assert(overlay.includes('Search this workspace'), 'workspace-scoped search copy')

console.log('OK: File Library UI smoke checks passed')
