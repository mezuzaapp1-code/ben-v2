import { useEffect, useState } from 'react'

export function NewProjectModal({ open, onClose, onSubmit, submitting, error, canSubmit = true }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  useEffect(() => { if (open) { setName(''); setDescription('') } }, [open])
  if (!open) return null
  return <div className="hw-modal-overlay" role="dialog" aria-modal="true" aria-label="Create new project">
    <form className="hw-modal hw-modal--form" onSubmit={event => {
      event.preventDefault()
      if (name.trim() && !submitting && canSubmit) onSubmit({ name: name.trim(), description: description.trim() || undefined })
    }}>
      <header className="hw-modal__header"><h2 className="hw-modal__title">New project</h2>
        <button className="hw-modal__close" type="button" onClick={onClose} disabled={submitting} aria-label="Close">×</button></header>
      <div className="hw-modal__body hw-modal__body--form">
        <label className="project-form__field">Project name
          <input autoFocus required maxLength={512} value={name} disabled={submitting}
            className="project-form__input" onChange={e => setName(e.target.value)} placeholder="My project" /></label>
        <label className="project-form__field">Description (optional)
          <textarea maxLength={8000} value={description} disabled={submitting} rows={3}
            className="project-form__textarea" onChange={e => setDescription(e.target.value)} /></label>
        {error && <p className="project-form__error" role="alert">{error}</p>}
      </div>
      <footer className="hw-modal__footer">
        <button className="hw-modal__btn hw-modal__btn--ghost" type="button" onClick={onClose} disabled={submitting}>Cancel</button>
        <button type="submit" disabled={!name.trim() || submitting || !canSubmit} className="hw-modal__btn hw-modal__btn--primary">
          {submitting ? 'Creating…' : 'Create project'}</button>
      </footer>
    </form>
  </div>
}
