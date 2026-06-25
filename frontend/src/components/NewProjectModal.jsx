import { useEffect, useState } from 'react'
import PropTypes from 'prop-types'

const DEFAULT_LOCATION = 'Or Akiva'

export function NewProjectModal({ open, onClose, onSubmit, submitting, error, canSubmit = true }) {
  const [projectName, setProjectName] = useState('')
  const [softwareDescription, setSoftwareDescription] = useState('')
  const [locationBase, setLocationBase] = useState(DEFAULT_LOCATION)
  const [keyContacts, setKeyContacts] = useState('')
  const [initialTacticalTasks, setInitialTacticalTasks] = useState('')

  useEffect(() => {
    if (!open) return
    setProjectName('')
    setSoftwareDescription('')
    setLocationBase(DEFAULT_LOCATION)
    setKeyContacts('')
    setInitialTacticalTasks('')
  }, [open])

  if (!open) return null

  const handleSubmit = (event) => {
    event.preventDefault()
    const name = projectName.trim()
    const software_description = softwareDescription.trim()
    if (!name || !software_description) return
    onSubmit?.({
      name,
      software_description,
      location_base: locationBase.trim() || DEFAULT_LOCATION,
      key_contacts: keyContacts.trim(),
      initial_tactical_tasks: initialTacticalTasks.trim(),
    })
  }

  const formValid = projectName.trim().length > 0 && softwareDescription.trim().length > 0

  return (
    <div className="hw-modal-overlay" role="dialog" aria-modal="true" aria-label="Create new project">
      <form className="hw-modal hw-modal--form" onSubmit={handleSubmit}>
        <header className="hw-modal__header">
          <h2 className="hw-modal__title">+ New Project</h2>
          <button type="button" className="hw-modal__close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>
        <div className="hw-modal__body hw-modal__body--form">
          <label className="project-form__field">
            <span>Project name</span>
            <input
              className="project-form__input"
              value={projectName}
              onChange={(event) => setProjectName(event.target.value)}
              placeholder="Mission-critical data center fit-out"
              required
              maxLength={512}
              autoFocus
            />
          </label>
          <label className="project-form__field">
            <span>Software description</span>
            <textarea
              className="project-form__textarea"
              value={softwareDescription}
              onChange={(event) => setSoftwareDescription(event.target.value)}
              placeholder={
                'Describe the operational software you need.\n\nExample:\nTable: field_logs with columns id integer PRIMARY KEY, crew text, notes text'
              }
              rows={6}
              required
              maxLength={16000}
            />
            <small className="project-form__hint">
              BEN generates your JIT schema blueprint from this description (max 16,000 characters).
            </small>
          </label>
          <label className="project-form__field">
            <span>Location base</span>
            <input
              className="project-form__input"
              value={locationBase}
              onChange={(event) => setLocationBase(event.target.value)}
              placeholder="Or Akiva"
              maxLength={256}
            />
          </label>
          <label className="project-form__field">
            <span>Key contacts</span>
            <textarea
              className="project-form__textarea"
              value={keyContacts}
              onChange={(event) => setKeyContacts(event.target.value)}
              placeholder={'Foreman — Yossi Levi (052-xxx)\nSite inspector — Dana Cohen'}
              rows={3}
              maxLength={8000}
            />
          </label>
          <label className="project-form__field">
            <span>Initial tactical tasks</span>
            <textarea
              className="project-form__textarea"
              value={initialTacticalTasks}
              onChange={(event) => setInitialTacticalTasks(event.target.value)}
              placeholder={
                'Mobilize crane access lane\nConfirm height-safety roster\nSchedule electrical rough-in inspection'
              }
              rows={4}
              maxLength={8000}
            />
            <small className="project-form__hint">One deployment step or milestone per line</small>
          </label>
          {error ? <p className="project-form__error">{error}</p> : null}
        </div>
        <footer className="hw-modal__footer">
          <button type="button" className="hw-modal__btn hw-modal__btn--ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className="hw-modal__btn hw-modal__btn--primary"
            disabled={submitting || !canSubmit || !formValid}
          >
            {submitting ? 'Provisioning…' : 'Create project'}
          </button>
        </footer>
      </form>
    </div>
  )
}

NewProjectModal.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  onSubmit: PropTypes.func,
  submitting: PropTypes.bool,
  error: PropTypes.string,
  canSubmit: PropTypes.bool,
}

NewProjectModal.defaultProps = {
  onSubmit: undefined,
  submitting: false,
  error: null,
  canSubmit: true,
}
