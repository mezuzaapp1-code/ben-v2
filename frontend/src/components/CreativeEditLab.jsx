import { useEffect, useRef, useState } from 'react'
import { BEN_API_BASE } from '../config.js'
import { mediaRequest } from '../api/media.js'

/** Gate 1 only. Workspace Files retains original ownership and upload. */
export default function CreativeEditLab({ workspaceId, buildHeaders }) {
  const [source, setSource] = useState(null)
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const originalUrl = useRef(null)
  const controller = useRef(null)
  const busyRef = useRef(false)
  useEffect(() => () => {
    controller.current?.abort()
    if (originalUrl.current) URL.revokeObjectURL(originalUrl.current)
  }, [])

  async function loadCanonical(fileId, signal) {
    return mediaRequest(`/creative-lab/canonical?workspace_id=${encodeURIComponent(workspaceId)}&file_id=${encodeURIComponent(fileId)}`,
      await buildHeaders(), { signal })
  }

  async function upload(file) {
    if (!file || busyRef.current) return
    if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 20 * 1024 * 1024) {
      setError('Choose a static RGB PNG or JPEG up to 20 MB.'); return
    }
    busyRef.current = true
    controller.current = new AbortController()
    const signal = controller.current.signal
    setBusy(true); setError(''); setPreview(null); setSource(null)
    if (originalUrl.current) URL.revokeObjectURL(originalUrl.current)
    originalUrl.current = null
    try {
      const form = new FormData()
      form.append('file', file)
      const headers = new Headers(await buildHeaders())
      headers.delete('Content-Type')
      const response = await fetch(`${BEN_API_BASE}/api/workspaces/${encodeURIComponent(workspaceId)}/files`,
        { method: 'POST', headers, body: form, signal })
      if (!response.ok) throw new Error(`Authorized upload failed (${response.status}).`)
      const uploaded = await response.json()
      if (signal.aborted) return
      originalUrl.current = URL.createObjectURL(file)
      setSource({ id: uploaded.id, url: originalUrl.current })
      const result = await loadCanonical(uploaded.id, signal)
      if (!signal.aborted) setPreview(result)
    } catch (e) {
      if (!signal.aborted) setError(e.message || 'Canonical preview unavailable.')
    } finally {
      busyRef.current = false
      if (!signal.aborted) setBusy(false)
    }
  }

  async function reload() {
    if (!source || busyRef.current) return
    busyRef.current = true
    controller.current = new AbortController()
    const signal = controller.current.signal
    setBusy(true); setError('')
    try {
      const result = await loadCanonical(source.id, signal)
      if (signal.aborted) return
      if (preview && result.metadata.canonical_pixel_sha256 !== preview.metadata.canonical_pixel_sha256) {
        throw new Error('FAIL: canonical pixel identity changed on reload.')
      }
      setPreview(result)
    } catch (e) {
      if (!signal.aborted) { setPreview(null); setError(e.message || 'Reload failed.') }
    } finally {
      busyRef.current = false
      if (!signal.aborted) setBusy(false)
    }
  }

  return <section aria-label="Creative Edit Lab Gate 1">
    <h2>Creative Edit Lab · canonical input</h2>
    <p>Internal experiment. No editing or provider calls. Original bytes stay in the existing Workspace File Library.</p>
    {!workspaceId && <p>Select a workspace to upload an image.</p>}
    <label>Source PNG / JPEG <input type="file" accept="image/png,image/jpeg"
      disabled={!workspaceId || busy} onChange={e => { void upload(e.target.files?.[0]); e.target.value = '' }} /></label>
    {busy && <p role="status">Loading and verifying canonical pixels…</p>}
    {error && <p role="alert">{error}</p>}
    {source && <div>
      <p>Source file: {source.id}</p>
      <button type="button" disabled={busy} onClick={() => { void reload() }}>Reload canonical pixels</button>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16 }}>
        <figure style={{ margin: 0, flex: '1 1 280px' }}><figcaption>Original upload (browser rendering)</figcaption>
          <img src={source.url} alt="Original uploaded image" style={{ maxWidth: '100%', maxHeight: 480 }} /></figure>
        {preview && <figure style={{ margin: 0, flex: '1 1 280px' }}><figcaption>Canonical sRGB raster</figcaption>
          <img src={`data:image/png;base64,${preview.canonical_png_base64}`} alt="Canonical image"
            style={{ maxWidth: '100%', maxHeight: 480 }} /></figure>}
      </div>
    </div>}
    {preview && <div>
      <p>Lossless reload pixel identity: {preview.metadata.reload_pixel_identity ? 'PASS' : 'FAIL'}.
        This is canonicalization evidence, not edit-preservation or semantic-quality evidence.</p>
      <p>Untagged images are assumed sRGB. Browser rendering of the original may differ.
        Alpha, animation and non-RGB input are outside this gate.</p>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(preview.metadata, null, 2)}</pre>
    </div>}
  </section>
}
