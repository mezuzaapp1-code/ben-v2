import { useCallback, useId, useRef, useState } from 'react'
import { useDismissOnOutside } from '../../hooks/useDismissOnOutside.js'
import './BasaltSelect.css'

/**
 * Custom single-select — replaces native <select> for consistent touch + theme.
 */
export function BasaltSelect({
  value,
  onChange,
  options = [],
  disabled = false,
  label,
  placeholder = 'Select…',
  className = '',
  size = 'md',
  'aria-label': ariaLabel,
}) {
  const listboxId = useId()
  const rootRef = useRef(null)
  const triggerRef = useRef(null)
  const [open, setOpen] = useState(false)

  const close = useCallback(() => setOpen(false), [])

  useDismissOnOutside({
    open,
    onDismiss: close,
    containerRef: rootRef,
    triggerRef,
  })

  const selected = options.find((opt) => opt.type !== 'divider' && opt.value === value)
  const displayLabel = selected?.label ?? placeholder

  const toggle = (event) => {
    event.stopPropagation()
    if (disabled) return
    setOpen((prev) => !prev)
  }

  const pick = (nextValue) => {
    onChange?.(nextValue)
    close()
  }

  return (
    <div
      ref={rootRef}
      className={`basalt-select basalt-select--${size}${open ? ' basalt-select--open' : ''}${
        disabled ? ' basalt-select--disabled' : ''
      } ${className}`.trim()}
    >
      {label ? <span className="basalt-select__label">{label}</span> : null}
      <button
        ref={triggerRef}
        type="button"
        className="basalt-select__trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        aria-label={ariaLabel || label || placeholder}
        disabled={disabled}
        onClick={toggle}
      >
        <span
          className={
            selected
              ? 'basalt-select__value'
              : 'basalt-select__value basalt-select__value--placeholder'
          }
        >
          {displayLabel}
        </span>
        <span className="basalt-select__chevron" aria-hidden="true">
          ▾
        </span>
      </button>
      <ul
        id={listboxId}
        className="basalt-select__menu"
        role="listbox"
        aria-label={ariaLabel || label || 'Options'}
        hidden={!open}
      >
        {options.map((opt, index) => {
          if (opt.type === 'divider') {
            return <li key={`div-${index}`} className="basalt-select__divider" role="separator" />
          }
          const isSelected = opt.value === value
          return (
            <li key={String(opt.value)} role="none">
              <button
                type="button"
                role="option"
                aria-selected={isSelected}
                className={
                  isSelected
                    ? 'basalt-select__option basalt-select__option--selected'
                    : opt.accent
                      ? 'basalt-select__option basalt-select__option--accent'
                      : 'basalt-select__option'
                }
                onClick={() => pick(opt.value)}
              >
                {opt.label}
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
