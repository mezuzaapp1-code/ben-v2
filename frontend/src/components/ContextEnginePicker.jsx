import { useCallback, useRef, useState } from 'react'
import { useDismissOnOutside } from '../hooks/useDismissOnOutside.js'
import { getSpeakingProviders } from '../providers/providerRegistry.js'
import './ContextEnginePicker.css'

/**
 * Inline project + Tier 1 engine picker anchored to the composer context chip.
 */
export function ContextEnginePicker({
  projectName,
  activeProjectId,
  projectOptions = [],
  onProjectChange,
  newProjectOption,
  onNewProject,
  activeProviderId,
  onProviderChange,
  disabled = false,
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)
  const triggerRef = useRef(null)
  const providers = getSpeakingProviders()

  const close = useCallback(() => setOpen(false), [])

  useDismissOnOutside({
    open,
    onDismiss: close,
    containerRef: rootRef,
    triggerRef,
  })

  const activeProvider = providers.find((p) => p.id === activeProviderId)
  const summaryLabel = `${projectName || 'No project'} · ${activeProvider?.shortLabel ?? activeProvider?.label ?? 'GPT'}`

  const pickProject = (value) => {
    if (value === newProjectOption) {
      onNewProject?.()
      close()
      return
    }
    onProjectChange?.(value || null)
    close()
  }

  const pickProvider = (providerId) => {
    onProviderChange?.(providerId)
    close()
  }

  return (
    <div ref={rootRef} className={`context-picker${open ? ' context-picker--open' : ''}`}>
      <button
        ref={triggerRef}
        type="button"
        className="composer-capsule__chip context-picker__trigger"
        aria-expanded={open}
        aria-haspopup="dialog"
        disabled={disabled}
        onClick={(event) => {
          event.stopPropagation()
          setOpen((value) => !value)
        }}
      >
        <span className="composer-capsule__chip-label">{summaryLabel}</span>
        <span className="context-picker__chevron" aria-hidden="true">
          ▾
        </span>
      </button>
      {open ? (
        <div className="context-picker__menu" role="dialog" aria-label="Project and engine">
          <div className="context-picker__section">
            <span className="context-picker__section-title">Project</span>
            <div className="context-picker__list" role="listbox" aria-label="Projects">
              <button
                type="button"
                role="option"
                aria-selected={!activeProjectId}
                className={!activeProjectId ? 'context-picker__option context-picker__option--active' : 'context-picker__option'}
                onClick={() => pickProject('')}
              >
                {projectOptions.length ? 'No project selected' : 'No projects'}
              </button>
              {projectOptions.map((project) => (
                <button
                  key={project.id}
                  type="button"
                  role="option"
                  aria-selected={project.id === activeProjectId}
                  className={
                    project.id === activeProjectId
                      ? 'context-picker__option context-picker__option--active'
                      : 'context-picker__option'
                  }
                  onClick={() => pickProject(project.id)}
                >
                  {project.name}
                </button>
              ))}
              <button
                type="button"
                className="context-picker__option context-picker__option--accent"
                onClick={() => pickProject(newProjectOption)}
              >
                + New project
              </button>
            </div>
          </div>
          <div className="context-picker__section">
            <span className="context-picker__section-title">Engine</span>
            <div className="context-picker__engines" role="radiogroup" aria-label="Tier 1 engine">
              {providers.map((provider) => {
                const isActive = provider.id === activeProviderId
                return (
                  <button
                    key={provider.id}
                    type="button"
                    role="radio"
                    aria-checked={isActive}
                    className={
                      isActive
                        ? 'context-picker__engine context-picker__engine--active'
                        : 'context-picker__engine'
                    }
                    style={{ '--engine-accent': provider.accent }}
                    onClick={() => pickProvider(provider.id)}
                  >
                    <span className="context-picker__engine-dot" aria-hidden="true" />
                    {provider.label}
                  </button>
                )
              })}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
