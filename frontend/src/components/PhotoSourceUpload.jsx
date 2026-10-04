import { useEffect, useRef, useState } from 'react'
import { validPhoto } from '../api/photoUpload.js'
import './PhotoSourceUpload.css'

export default function PhotoSourceUpload({ file, onSelect, disabled }) {
  const picker = useRef(null)
  const [preview, setPreview] = useState(null), [message, setMessage] = useState('')
  useEffect(() => {
    if (!file) return
    const url = URL.createObjectURL(file)
    let cancelled = false
    Promise.resolve().then(() => { if (!cancelled) setPreview({ file, url }) })
    return () => { cancelled = true; URL.revokeObjectURL(url) }
  }, [file])
  return <section className="ben-photo-source" aria-label="Animate my photo">
    {preview?.file === file && file && <img src={preview.url} alt="Your selected photo" />}
    <div className="ben-photo-source__info"><strong>{file ? 'Your photo is attached' : 'Animate a photo'}</strong>
      <p>{file ? file.name : 'Add a JPEG or PNG, then describe the motion below.'}</p>
      <input ref={picker} type="file" aria-label="Your photo" accept="image/jpeg,image/png,.jpg,.jpeg,.png" disabled={disabled} hidden onChange={e => {
        const next = e.target.files?.[0]
        e.target.value = ''
        if (!next) return
        if (!validPhoto(next)) { setMessage('Choose a JPEG or PNG up to 20 MiB.'); return }
        setMessage(''); onSelect(next)
      }} />
      <button type="button" disabled={disabled} onClick={() => picker.current?.click()}>{file ? 'Change photo' : 'Choose photo'}</button>
      <small>{file ? 'Uploaded when you send. Your original stays in this conversation.' : 'Up to 20 MiB. No project required.'}</small>
      {message && <p role="alert">{message}</p>}
    </div>
  </section>
}
