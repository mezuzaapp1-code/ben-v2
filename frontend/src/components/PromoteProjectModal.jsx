import { useState } from 'react'
import './PromoteProjectModal.css'

function slugifyProjectName(name) {
  return (
    String(name || '')
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9\-_]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 64) || 'project'
  )
}

export function PromoteProjectModal({ open, onClose, onSubmit, submitting = false, locale = 'en' }) {
  const [projectName, setProjectName] = useState('')
  const previewSlug = slugifyProjectName(projectName)

  if (!open) return null

  const title = locale === 'he' ? 'הפוך לפרויקט' : 'Promote to Project'
  const label = locale === 'he' ? 'שם הפרויקט' : 'Project name'
  const hint = locale === 'he' ? 'ייווצר תיקייה ניידת עם מסד נתונים מקומי' : 'Creates a portable folder with a local project database'
  const slugLabel = locale === 'he' ? 'מזהה תיקייה' : 'Folder slug'
  const cancel = locale === 'he' ? 'ביטול' : 'Cancel'
  const submit = submitting
    ? locale === 'he'
      ? 'מקדם…'
      : 'Promoting…'
    : locale === 'he'
      ? 'הפוך לפרויקט'
      : 'Promote'

  return (
    <div className="promote-modal-scrim" role="presentation" onClick={onClose}>
      <div
        className="promote-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        dir={locale === 'he' ? 'rtl' : 'ltr'}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="promote-modal__title">{title}</h2>
        <p className="promote-modal__hint">{hint}</p>
        <label className="promote-modal__label">
          {label}
          <input
            type="text"
            className="promote-modal__input"
            value={projectName}
            onChange={(e) => setProjectName(e.target.value)}
            placeholder={locale === 'he' ? 'לדוגמה: מגדל משרדים תל אביב' : 'e.g. Basalt HQ Refactor'}
            disabled={submitting}
            autoFocus
          />
        </label>
        <p className="promote-modal__slug">
          {slugLabel}: <code>{previewSlug}</code>
        </p>
        <div className="promote-modal__actions">
          <button type="button" className="promote-modal__cancel" onClick={onClose} disabled={submitting}>
            {cancel}
          </button>
          <button
            type="button"
            className="promote-modal__submit"
            disabled={submitting || !projectName.trim()}
            onClick={() => onSubmit(projectName.trim(), previewSlug)}
          >
            {submit}
          </button>
        </div>
      </div>
    </div>
  )
}

export { slugifyProjectName }
