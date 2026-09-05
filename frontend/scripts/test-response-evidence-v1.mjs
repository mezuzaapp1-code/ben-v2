/**
 * SOURCE COMPLETENESS & EVIDENCE V1 — frontend data + panel contract.
 * Run: node frontend/scripts/test-response-evidence-v1.mjs
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { applyOwnedAssistantDone, createOwnedAssistant } from '../src/lib/chatStreamOwnership.js'
import {
  FILE_INITIAL_READ_EVENT,
  canShowSources,
  responseEvidenceFromDoneEvent,
  sanitizeResponseEvidence,
  sourcesCount,
} from '../src/lib/fileStatus.js'
import { previewIframeSrc } from '../src/lib/workspaceFilePreview.js'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function assert(cond, msg) {
  if (!cond) {
    console.error('FAIL:', msg)
    process.exit(1)
  }
}

const FILE_A = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'
const FILE_B = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2'
const CHUNK_A = '11111111-1111-1111-1111-111111111111'

const sample = {
  retrieval_mode: 'chunks',
  sources: [{ source_id: FILE_A, source_type: 'workspace_file', display_name: 'A.pdf' }],
  evidence: [
    {
      evidence_id: `chunk:${CHUNK_A}`,
      source_id: FILE_A,
      excerpt: 'injected A',
      origin: 'ben_retrieval',
      chunk_id: CHUNK_A,
      page: 2,
    },
  ],
}

assert(sourcesCount(sample) === 1, 'Sources count is distinct used sources')
assert(
  canShowSources({ role: 'assistant', kind: 'chat', response_evidence: sample }) === true,
  'standard chat with evidence can show Sources'
)
assert(
  canShowSources({
    role: 'assistant',
    kind: 'chat',
    response_evidence: sample,
    source_event: FILE_INITIAL_READ_EVENT,
  }) === false,
  'Initial Read never gets Sources'
)
assert(
  canShowSources({ role: 'assistant', kind: 'adhoc_expert', response_evidence: sample }) === false,
  'Add Opinion never gets Sources'
)
assert(canShowSources({ role: 'assistant', kind: 'chat' }) === false, 'no evidence → no Sources')
assert(
  canShowSources({
    role: 'assistant',
    kind: 'chat',
    response_evidence: sample,
    provider_id: 'grok',
    model_used: 'grok-4',
  }) === true,
  'provider/model do not affect Sources gate'
)

const dirty = {
  retrieval_mode: 'mixed',
  sources: [
    { source_id: FILE_A, source_type: 'workspace_file', display_name: 'A.pdf' },
    { source_id: FILE_B, source_type: 'workspace_file', display_name: 'B.pdf' },
  ],
  evidence: [
    {
      evidence_id: `prefix:${FILE_B}`,
      source_id: FILE_B,
      excerpt: 'prefix only',
      origin: 'ben_retrieval',
      page: 9,
    },
    {
      evidence_id: 'x',
      source_id: 'not-a-uuid',
      excerpt: 'bad',
      origin: 'ben_retrieval',
    },
  ],
}
const cleaned = sanitizeResponseEvidence(dirty)
assert(cleaned.sources.length === 2, 'mixed sources kept')
assert(!('page' in cleaned.evidence[0]), 'prefix evidence never keeps page')
assert(!('chunk_id' in cleaned.evidence[0]), 'prefix evidence never keeps chunk_id')
assert(sanitizeResponseEvidence({ retrieval_mode: 'off', sources: [] }) === null, 'bad mode fails closed')
assert(sanitizeResponseEvidence(undefined) === null, 'missing evidence is null')

const live = responseEvidenceFromDoneEvent({ response_evidence: sample })
const history = sanitizeResponseEvidence(sample)
assert(JSON.stringify(live) === JSON.stringify(history), 'live/history same shape')
assert(
  readFileSync(join(root, 'src/api/threads.js'), 'utf8').includes(
    'response_evidence: sanitizeResponseEvidence(m.response_evidence)'
  ),
  'mapApiMessage uses the same sanitizer'
)

const owned = applyOwnedAssistantDone(
  [createOwnedAssistant({ sendNonce: 'n1', providerId: 'gpt' })],
  'n1',
  { response: 'ok', used_files: [{ id: FILE_A, name: 'A.pdf' }], response_evidence: sample }
)
assert(
  JSON.stringify(owned[0].response_evidence) === JSON.stringify(sample),
  'done event stores sanitized response_evidence'
)

const app = readFileSync(join(root, 'src/App.jsx'), 'utf8')
const panel = readFileSync(join(root, 'src/components/SourcesPanel.jsx'), 'utf8')
const panelCss = readFileSync(join(root, 'src/components/SourcesPanel.css'), 'utf8')
const preview = readFileSync(join(root, 'src/lib/workspaceFilePreview.js'), 'utf8')

assert(app.includes('canShowSources'), 'App uses canShowSources')
assert(app.includes('SourcesPanel'), 'App mounts SourcesPanel')
assert(app.includes('sourcesPanel'), 'panel state exists')
assert(app.includes('messageKey'), 'panel is bound to a response')
assert(app.includes('!canShowSources(m) && m.used_files'), 'historical Used files remain when no evidence')
assert(app.includes('Sources ({sourcesN})'), 'Sources (N) label')
assert(panel.includes('Open source') && panel.includes('Open page'), 'panel has open actions')
assert(panel.includes('item.page != null'), 'page label is conditional')
assert(panelCss.includes('.sources-panel'), 'panel has dedicated CSS')
assert(!panel.includes('FileLibraryOverlay'), 'SourcesPanel is not File Library')
assert(preview.includes('fetchWorkspaceFileBlob'), 'open uses authenticated blob path')
assert(preview.includes('#page='), 'page open uses fragment')
assert(preview.includes('revokeObjectURL'), 'blob URL is revoked')
assert(app.includes('setSourcesPanel({ open: false'), 'panel closes on thread change or close')
assert(previewIframeSrc('blob:http://local/x', { kind: 'pdf', page: 3 }) === 'blob:http://local/x#page=3', 'open page target')
assert(previewIframeSrc('blob:http://local/x', { kind: 'pdf' }) === 'blob:http://local/x', 'pdf without page')
assert(previewIframeSrc('blob:http://local/x', { kind: 'image', page: 3 }) === 'blob:http://local/x', 'non-pdf ignores page')

console.log('response-evidence-v1 frontend tests: ok')
