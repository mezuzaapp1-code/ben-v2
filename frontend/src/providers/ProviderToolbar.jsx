import { useCallback, useEffect, useRef, useState } from 'react'

import { useDismissOnOutside } from '../hooks/useDismissOnOutside.js'

import { getSpeakingProviders } from './providerRegistry.js'

import { formatModelShortLabel, getProviderModelOptions } from './providerModelChoices.js'

import './ProviderToolbar.css'



/** Horizontal speaking-provider selector for /chat routing. */

export function ProviderToolbar({

  activeProviderId,

  onActiveProviderChange,

  onAddProviderClick,

  providerModels = {},

  onProviderModelChange,

  adhocComposeActive = false,

  disabled = false,

}) {

  const providers = getSpeakingProviders()

  const activeProvider = providers.find((p) => p.id === activeProviderId)

  const [openMenuId, setOpenMenuId] = useState(null)

  const toolbarRef = useRef(null)

  const menuRef = useRef(null)

  const activeChevronRef = useRef(null)



  const closeMenu = useCallback(() => setOpenMenuId(null), [])



  const toggleMenu = useCallback(

    (providerId, event) => {

      event.stopPropagation()

      if (disabled) return

      setOpenMenuId((prev) => (prev === providerId ? null : providerId))

    },

    [disabled]

  )



  useDismissOnOutside({

    open: Boolean(openMenuId),

    onDismiss: closeMenu,

    containerRef: menuRef,

    triggerRef: activeChevronRef,

  })



  useEffect(() => {

    if (!openMenuId) return undefined

    const onKeyDown = (event) => {

      if (event.key === 'Escape') closeMenu()

    }

    document.addEventListener('keydown', onKeyDown)

    return () => document.removeEventListener('keydown', onKeyDown)

  }, [openMenuId, closeMenu])



  return (

    <div

      ref={toolbarRef}

      className={

        adhocComposeActive

          ? 'provider-toolbar provider-toolbar--adhoc'

          : 'provider-toolbar'

      }

      role="toolbar"

      aria-label="Speaking provider"

      style={adhocComposeActive ? { '--provider-accent': activeProvider?.accent } : undefined}

    >

      <span className="provider-toolbar__label">Speaker</span>

      <div className="provider-toolbar__list" role="group" aria-label="Providers">

        {providers.map((provider) => {

          const isActive = provider.id === activeProviderId

          const modelOptions = getProviderModelOptions(provider.id)

          const selectedModel = providerModels[provider.id] ?? modelOptions[0] ?? ''

          const modelShort = formatModelShortLabel(selectedModel)

          const menuOpen = openMenuId === provider.id



          return (

            <div

              key={provider.id}

              className="provider-toolbar__pill-wrap"

              style={{ '--provider-accent': provider.accent }}

            >

              <div

                className={

                  isActive

                    ? 'provider-toolbar__pill provider-toolbar__pill--active'

                    : 'provider-toolbar__pill'

                }

              >

                <button

                  type="button"

                  className="provider-toolbar__pill-main"

                  aria-pressed={isActive}

                  aria-label={`${provider.label}${isActive ? ' (active)' : ''}, model ${selectedModel}`}

                  disabled={disabled}

                  onClick={() => {

                    closeMenu()

                    onActiveProviderChange(provider.id)

                  }}

                >

                  <span className="provider-toolbar__dot" aria-hidden="true" />

                  <span className="provider-toolbar__pill-copy">

                    <span className="provider-toolbar__pill-label">{provider.label}</span>

                    {modelShort ? (

                      <span className="provider-toolbar__pill-model">{modelShort}</span>

                    ) : null}

                  </span>

                </button>

                {modelOptions.length > 0 ? (

                  <button

                    ref={menuOpen ? activeChevronRef : undefined}

                    type="button"

                    className="provider-toolbar__pill-chevron"

                    aria-label={`Choose ${provider.label} model`}

                    aria-expanded={menuOpen}

                    aria-haspopup="listbox"

                    disabled={disabled}

                    onClick={(event) => toggleMenu(provider.id, event)}

                  >

                    <span className="provider-toolbar__chevron-icon" aria-hidden="true">

                      ▾

                    </span>

                  </button>

                ) : null}

              </div>

              {menuOpen ? (

                <ul

                  ref={menuRef}

                  className="provider-toolbar__menu provider-toolbar__menu--open"

                  role="listbox"

                  aria-label={`${provider.label} models`}

                >

                  {modelOptions.map((modelId) => {

                    const selected = modelId === selectedModel

                    return (

                      <li key={modelId} role="none">

                        <button

                          type="button"

                          role="option"

                          aria-selected={selected}

                          className={

                            selected

                              ? 'provider-toolbar__menu-item provider-toolbar__menu-item--selected'

                              : 'provider-toolbar__menu-item'

                          }

                          onClick={() => {

                            onProviderModelChange?.(provider.id, modelId)

                            closeMenu()

                          }}

                        >

                          {modelId}

                        </button>

                      </li>

                    )

                  })}

                </ul>

              ) : null}

            </div>

          )

        })}

        <button

          type="button"

          className="provider-toolbar__add"

          aria-label="הוסף חוות דעת מומחה לשיחה"

          title="הוסף מודל נוסף שיענה על השאלה האחרונה בשיחה"

          disabled={disabled}

          onClick={() => onAddProviderClick?.()}

        >

          + הוסף חוות דעת מומחה

        </button>

      </div>

    </div>

  )

}


