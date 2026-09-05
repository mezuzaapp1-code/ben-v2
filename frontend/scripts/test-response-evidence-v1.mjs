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

const nineIds = Array.from({ length: 9 }, (_, i) => `aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaa${String(i + 10).padStart(2, '0')}`)
const nineSources = {
  retrieval_mode: 'prefix_fallback',
  sources: nineIds.map((id, i) => ({
    source_id: id,
    source_type: 'workspace_file',
    display_name: `S${i}.pdf`,
  })),
  evidence: [
    {
      evidence_id: `prefix:${nineIds[0]}`,
      source_id: nineIds[0],
      excerpt: 'kept',
      origin: 'ben_retrieval',
    },
  ],
}
const nineClean = sanitizeResponseEvidence(nineSources)
assert(nineClean.sources.length === 9, 'sanitizer does not drop valid source identities')
assert(sourcesCount(nineSources) === 9, 'Sources(N) counts all injected sources')
assert(nineClean.evidence.length === 1, 'evidence rows may be fewer than sources')

const pageDot = sanitizeResponseEvidence({
  retrieval_mode: 'chunks',
  sources: [{ source_id: FILE_A, source_type: 'workspace_file', display_name: 'A.pdf' }],
  evidence: [
    {
      evidence_id: `chunk:${CHUNK_A}`,
      source_id: FILE_A,
      excerpt: 'p',
      origin: 'ben_retrieval',
      chunk_id: CHUNK_A,
      page: '3.0',
    },
  ],
})
assert(!('page' in pageDot.evidence[0]), 'page "3.0" is rejected to match Python')

const emojiIds = Array.from({ length: 6 }, (_, i) => `bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb${String(i + 10).padStart(2, '0')}`)
const emojiBudget = sanitizeResponseEvidence({
  retrieval_mode: 'chunks',
  sources: emojiIds.map((id, i) => ({
    source_id: id,
    source_type: 'workspace_file',
    display_name: `E${i}.pdf`,
  })),
  evidence: emojiIds.map((id, i) => ({
    evidence_id: `prefix:${id}`,
    source_id: id,
    excerpt: '😀'.repeat(400),
    origin: 'ben_retrieval',
  })),
})
assert(emojiBudget.sources.length === 6, 'emoji budget does not drop source identity')
assert(
  emojiBudget.evidence.length === 6,
  'total excerpt budget counts Unicode code points (6×400=2400), not UTF-16 units'
)

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

assert(
  /className="sources-panel__name" dir="auto"/.test(panel),
  'source filename has dir="auto"'
)
assert(
  /className="sources-panel__excerpt" dir="auto"/.test(panel),
  'evidence excerpt has its own bidi context'
)
assert(
  /<pre className="sources-panel__excerpt" dir="auto">\{item\.excerpt\}<\/pre>/.test(panel),
  'excerpt React child is the persisted excerpt with no rewrite'
)
assert(!/split\(|reverse\(|\[\\.\\.\\.item\.excerpt\]/.test(panel), 'panel does not reorder excerpt tokens')
assert(
  /<h2 className="sources-panel__title">Sources \(\{n\}\)<\/h2>/.test(panel),
  'Sources (N) chrome stays English LTR'
)
assert(/>\s*Close\s*</.test(panel), 'Close chrome stays English LTR')
assert(!/className="sources-panel"[^>]*dir="rtl"/.test(panel), 'panel root is not forced RTL')

{
  const excerptRule = panelCss.match(/\.sources-panel__excerpt \{[\s\S]*?\n\}/)?.[0] || ''
  assert(/unicode-bidi:\s*plaintext/.test(excerptRule), 'excerpt uses unicode-bidi: plaintext')
  assert(/white-space:\s*pre-wrap/.test(excerptRule), 'excerpt keeps white-space: pre-wrap')
  assert(/text-align:\s*start/.test(excerptRule), 'excerpt uses text-align: start')
  assert(/word-break:\s*normal/.test(excerptRule), 'excerpt wrapping is word-break: normal')
  assert(/overflow-wrap:\s*anywhere/.test(excerptRule), 'excerpt wrapping is overflow-wrap: anywhere')
  assert(/font-family:\s*inherit/.test(excerptRule), 'excerpt inherits panel font, not monospace')
  assert(!/word-break:\s*break-word/.test(excerptRule), 'excerpt does not use aggressive break-word')
}
{
  const panelRule = panelCss.match(/\.sources-panel \{[\s\S]*?\n\}/)?.[0] || ''
  assert(/direction:\s*ltr/.test(panelRule), 'panel chrome stays direction: ltr')
  assert(/unicode-bidi:\s*isolate/.test(panelRule), 'panel chrome stays unicode-bidi: isolate')
}

const mixedExcerpt =
  'הצעת מחיר מספר QT-2024-1847\nלכבוד: חברת אלפא בע"מ\nדוא"ל: purchasing@alpha.co.il\nApplication Server Pro\n₪ 306,233.60'
const mixedPayload = {
  retrieval_mode: 'prefix_fallback',
  sources: [
    {
      source_id: FILE_A,
      source_type: 'workspace_file',
      display_name: 'הצעת מחיר אלפא QT-2024-1847.pdf',
    },
  ],
  evidence: [
    {
      evidence_id: `prefix:${FILE_A}`,
      source_id: FILE_A,
      excerpt: mixedExcerpt,
      origin: 'ben_retrieval',
    },
  ],
}
const mixedClean = sanitizeResponseEvidence(mixedPayload)
assert(mixedClean.evidence[0].excerpt === mixedExcerpt, 'mixed Hebrew/English/email/₪/newlines stay character-identical')
assert(mixedClean.evidence[0].excerpt.includes('\n'), 'newlines in evidence are preserved')
assert(mixedClean.evidence[0].excerpt.includes('₪ 306,233.60'), 'currency text is not reconstructed')
assert(mixedClean.sources[0].display_name.includes('הצעת מחיר'), 'Hebrew filename is not reordered')
assert(canShowSources({ role: 'assistant', kind: 'chat', response_evidence: mixedPayload }) === true, 'Sources(N) still opens for mixed evidence')
assert(
  canShowSources({
    role: 'assistant',
    kind: 'chat',
    used_files: [{ id: FILE_A, name: 'A.pdf' }],
  }) === false,
  'legacy used_files-only messages still have no Sources'
)
assert(preview.includes('fetchWorkspaceFileBlob'), 'open uses authenticated blob path')
assert(preview.includes('#page='), 'page open uses fragment')
assert(preview.includes('revokeObjectURL'), 'blob URL is revoked')
assert(app.includes('setSourcesPanel({ open: false'), 'panel closes on thread change or close')
assert(previewIframeSrc('blob:http://local/x', { kind: 'pdf', page: 3 }) === 'blob:http://local/x#page=3', 'open page target')
assert(previewIframeSrc('blob:http://local/x', { kind: 'pdf' }) === 'blob:http://local/x', 'pdf without page')
assert(previewIframeSrc('blob:http://local/x', { kind: 'image', page: 3 }) === 'blob:http://local/x', 'non-pdf ignores page')

console.log('response-evidence-v1 frontend tests: ok')
