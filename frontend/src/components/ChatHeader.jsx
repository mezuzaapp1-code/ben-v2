import { useState } from 'react'
import {
  deleteConversationAriaLabel,
  deleteConversationLabel,
  deleteConversationTitle,
  deletingConversationLabel,
  promoteToProjectLabel,
} from '../lib/uiStrings.js'
import { useUiLocale } from '../hooks/useUiLocale.js'
import { publicConversationTitle } from '../lib/largePaste.js'
import { PromoteProjectModal } from './PromoteProjectModal.jsx'
import './ChatHeader.css'

export function ChatHeader({
  title,
  sessionType = 'chat',
  canPromote = false,
  onDelete,
  onPromote,
  deleting = false,
  promoting = false,
  visible = true,
}) {
  const locale = useUiLocale()
  const [promoteOpen, setPromoteOpen] = useState(false)
  const isProjectWorkspace = sessionType === 'project_setup'

  if (!visible) return null

  const displayTitle = publicConversationTitle(title)

  return (
    <>
      <div className="chat-header" aria-label="Conversation header">
        <h1 className="chat-header__title" title={displayTitle}>
          {displayTitle}
        </h1>
        <div className="chat-header__actions">
          {canPromote && !isProjectWorkspace ? (
            <button
              type="button"
              className="chat-header__promote"
              onClick={() => setPromoteOpen(true)}
              disabled={promoting || deleting}
              aria-label={promoteToProjectLabel(locale)}
              title={promoteToProjectLabel(locale)}
            >
              <span className="chat-header__promote-icon" aria-hidden="true">
                🚀
              </span>
              <span className="chat-header__promote-label">
                {promoting ? (locale === 'he' ? 'מקדם…' : 'Promoting…') : promoteToProjectLabel(locale)}
              </span>
            </button>
          ) : null}
          <button
            type="button"
            className="chat-header__delete"
            onClick={onDelete}
            disabled={deleting || promoting}
            aria-label={deleteConversationAriaLabel(locale)}
            title={deleteConversationTitle(locale)}
          >
            <span className="chat-header__delete-icon" aria-hidden="true">
              🗑️
            </span>
            <span className="chat-header__delete-label">
              {deleting ? deletingConversationLabel(locale) : deleteConversationLabel(locale)}
            </span>
          </button>
        </div>
      </div>
      <PromoteProjectModal
        open={promoteOpen}
        locale={locale}
        submitting={promoting}
        onClose={() => setPromoteOpen(false)}
        onSubmit={async (projectName, slug) => {
          if (!onPromote) return
          await onPromote({ projectName, projectSlug: slug })
          setPromoteOpen(false)
        }}
      />
    </>
  )
}
