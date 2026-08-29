/**
 * In-flow file lifecycle row: one item, no card chrome, inside .messages.
 * Run: node frontend/scripts/test-file-lifecycle-inflow.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
const bubble = readFileSync(join(root, 'src/components/FileLifecycleStatus.jsx'), 'utf8')
const css = readFileSync(join(root, 'src/components/FileLifecycleStatus.css'), 'utf8')

const attach = app.slice(app.indexOf('handleWorkspaceFileAttach'), app.indexOf('handleReceiptFile'))
assert(attach.includes("kind: 'file_upload'"), 'initial file_upload row is kept')
assert(attach.includes('updateFileUploadRow'), 'success/failure patches the same row')
assert(attach.includes('buildFileUploadResultPatch'), 'lifecycle fields are preserved on the same row')
assert(!attach.includes("kind: 'file_library'"), 'does not append a file_library sibling')
assert(!attach.includes("kind: failed ? 'api_error'"), 'does not append a lifecycle api_error surface')
assert(attach.includes('appendFileRefPart'), 'Vision appendFileRefPart is unchanged')

const scroller = app.slice(app.indexOf('className="messages"'), app.indexOf('className="composer-footer"'))
assert(scroller.includes('FileLifecycleBubble'), 'lifecycle row remains inside .messages')

assert(bubble.includes('className="file-lifecycle-bubble"'), 'lifecycle uses the in-flow row class')
assert(!/bubble \$\{message\?\.role/.test(bubble), 'lifecycle does not take message role bubble chrome')
assert(!bubble.includes('bubble user'), 'no .bubble.user on lifecycle row')
assert(!bubble.includes('bubble assistant'), 'no .bubble.assistant on lifecycle row')
assert(!/className=\{`bubble /.test(bubble), 'FileLifecycleBubble is not a chat bubble')

assert(!/position:\s*(sticky|fixed)/.test(css), 'lifecycle CSS is not sticky or fixed')
assert(!css.includes('box-shadow'), 'lifecycle CSS has no overlay shadow')

console.log('OK: in-flow file lifecycle row contract')
