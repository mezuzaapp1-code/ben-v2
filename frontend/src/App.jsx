import { OrganizationSwitcher, SignInButton, SignOutButton, useAuth } from '@clerk/clerk-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { acquirePersistentHeaders, buildBenHeaders, isAuthTokenUnavailable } from './api/benHeaders.js'
import { CLERK_ORG_REQUIRED, parseBenErrorResponse } from './api/benErrors.js'
import { postAdhocExpertStream } from './api/adhoc.js'
import { humanizeChatFetchError, postChatStream } from './api/chat.js'
import {
  COUNCIL_CLIENT_TIMEOUT_MS,
  councilResponseToMessages,
  humanizeCouncilFetchError,
  humanizeCouncilHttpError,
  postCouncil,
  postCouncilStream,
} from './api/council.js'
import { ADHOC_SYNTHESIS_PIPELINE } from './api/adhoc.js'
import {
  createProjectWorkspace,
  deleteThread,
  fetchThreadDetail,
  fetchThreadList,
  mapApiMessage,
  mapThreadFromList,
  promoteThread,
} from './api/threads.js'
import { logCouncilLifecycle } from './councilLifecycleLog.js'
import {
  canSubmitCouncil,
  markCouncilSubmitFinished,
  markCouncilSubmitStarted,
} from './loadGovernance.js'
import {
  clearCouncilPending,
  createClientRequestId,
  markCouncilPending,
  recoverStaleCouncilUi,
} from './runtimeRecovery.js'
import { useBenAuthContext } from './auth/BenAuthContext.jsx'
import {
  isClerkPersistentSessionReady,
  shouldShowClerkSignIn,
} from './auth/clerkPersistentAccess.js'
import { useBetaSession } from './auth/BetaSessionContext.jsx'
import {
  DRAFT_PREFIX,
  getStoredActiveThreadId,
  isDraftThreadId,
  isPersistedThreadId,
  serverThreadIdForApi,
  setStoredActiveThreadId,
} from './threadStorage.js'
import {
  captureCreditMemo,
  captureInvoice,
  conversationalProjectInit,
  executeNativeTool,
  fetchProjects,
} from './api/projects.js'
import { ActionCard } from './components/ActionCard.jsx'
import { ProjectSuccessToast } from './components/ProjectSuccessToast.jsx'
import { SystemTelemetryBadge } from './components/SystemTelemetryBadge.jsx'
import { CameraCaptureInput } from './components/CameraCaptureInput.jsx'
import { ComposerCapsule } from './components/ComposerCapsule.jsx'
import { BasaltSelect } from './components/ui/BasaltSelect.jsx'
import { AppTopBar } from './components/AppTopBar.jsx'
import { ChatHeader } from './components/ChatHeader.jsx'
import { NavDrawer, NavDrawerHistory } from './components/NavDrawer.jsx'
import { ChatMarkdown } from './components/ChatMarkdown.jsx'
import { KnowledgeBasesPanel } from './components/KnowledgeBasesPanel.jsx'
import { KnowledgeSidebar } from './components/KnowledgeSidebar.jsx'
import { NewProjectModal } from './components/NewProjectModal.jsx'
import { CapabilityCatalogTrigger, DiscoveryCenterOverlay } from './components/DiscoveryCenter.jsx'
import { NewsNavTrigger, NewsOverlay } from './components/NewsOverlay.jsx'
import { PROJECT_LIBRARY_DEFAULT_LIMIT } from './lib/projectLibrary.js'
import {
  FileLibraryNavTrigger,
  FileLibraryOverlay,
} from './components/FileLibraryOverlay.jsx'
import {
  ProjectLibraryNavTrigger,
  ProjectLibraryOverlay,
} from './components/ProjectLibraryOverlay.jsx'
import { FileLifecycleBubble } from './components/FileLifecycleStatus.jsx'
import { workspaceFileInventory } from './hooks/useWorkspaceFileInventory.jsx'
import { parseNewsLocation, newsFeedPath, newsTopicPath } from './lib/newsRoutes.js'
import { ProjectRepositoriesDashboard } from './components/ProjectRepositoriesDashboard.jsx'
import { usePlatformActiveFeatures } from './hooks/usePlatformActiveFeatures.js'
import { useProjectCreatePrivilege } from './hooks/useProjectCreatePrivilege.jsx'
import {
  buildConversationalInitPayload,
  parseConversationalInitResponse,
} from './lib/conversationalInitPayload.js'
import { normalizeProjectSlug } from './lib/threadWorkspace.js'
import { ExpertOpinionMenu } from './components/ExpertOpinionMenu.jsx'
import { useDismissOnOutside } from './hooks/useDismissOnOutside.js'
import { readInitialNavDrawerOpen, useNavDrawerMode } from './hooks/useNavDrawerMode.js'
import { formatChatAssistantMeta } from './providers/formatChatMeta.js'
import { DEFAULT_SPEAKING_PROVIDER_ID, getSpeakingProviderById, getSpeakingProviders } from './providers/providerRegistry.js'
import { DEFAULT_PROVIDER_MODELS, coerceRegisteredModel, getTier1Model } from './providers/providerModelChoices.js'
import { getMessageTextDirection } from './lib/markdownDirection.js'
import { singleDeleteConfirmMessage } from './lib/uiStrings.js'
import {
  isStandardChatAssistant,
  unavailableChatNote,
  usedFilesFromDoneEvent,
} from './lib/fileStatus.js'
import './App.css'

const HAS_CLERK_UI = Boolean(import.meta.env.VITE_CLERK_PUBLISHABLE_KEY?.trim())
const USE_COUNCIL_STREAM = true
const CODEBASE_TRIGGER = /@codebase/i

const COUNCIL_LABEL = {
  'Legal Advisor': '⚖️ Legal Advisor',
  'Business Advisor': '💼 Business Advisor',
  'Strategy Advisor': '🎯 Strategy Advisor',
  'Local Codebase Expert': '🧩 Local Codebase Expert',
}

const SYNTHESIS_REASONING_SECTIONS = [
  ['shared_recommendation', 'Shared recommendation'],
  ['disagreement_points', 'Disagreement & rationale'],
  ['legal_reasoning', 'Legal reasoning'],
  ['operational_reasoning', 'Operational reasoning'],
  ['strategic_reasoning', 'Strategic reasoning'],
  ['infrastructure_reasoning', 'Infrastructure reasoning'],
  ['minority_or_unique_views', 'Minority or unique views'],
]

const SYNTHESIS_REASONING_SECTIONS_HE = [
  ['shared_recommendation', 'המלצה משותפת'],
  ['disagreement_points', 'מחלוקות והנמקות'],
  ['legal_reasoning', 'היגיון משפטי'],
  ['operational_reasoning', 'היגיון תפעולי'],
  ['strategic_reasoning', 'היגיון אסטרטגי'],
  ['infrastructure_reasoning', 'היגיון תשתית'],
  ['minority_or_unique_views', 'דעות מיעוט / ייחודיות'],
]

function SynthesisReasoningExtras({ synthesis, outputLocale, synthesisPipeline }) {
  const locale =
    synthesisPipeline === ADHOC_SYNTHESIS_PIPELINE && (outputLocale || synthesis?.output_locale) === 'he'
      ? 'he'
      : outputLocale || synthesis?.output_locale
  const sections = locale === 'he' ? SYNTHESIS_REASONING_SECTIONS_HE : SYNTHESIS_REASONING_SECTIONS
  const blocks = sections.map(([key, label]) => {
    const v = synthesis[key]
    if (v == null || String(v).trim() === '') return null
    return (
      <details key={key} className="synthesis-detail">
        <summary>{label}</summary>
        <div className="synthesis-detail-body">{String(v)}</div>
      </details>
    )
  }).filter(Boolean)
  if (blocks.length === 0) return null
  return <div className="synthesis-reasoning-extras">{blocks}</div>
}

function isAdhocV2HebrewMessage(m) {
  const pipeline = m.synthesis_pipeline || m.synthesis?.synthesis_pipeline
  const locale = m.output_locale || m.synthesis?.output_locale
  return (
    m.kind === 'adhoc_synthesis' &&
    pipeline === ADHOC_SYNTHESIS_PIPELINE &&
    locale === 'he'
  )
}

function adhocSynthesisBubbleText(m) {
  const s = m.synthesis ?? {}
  const ae = s.agreement_estimate ?? 'לא ידוע'
  const rec = s.recommendation ?? ''
  const cons = s.consensus_points ?? ''
  const disagree =
    s.main_disagreement != null && String(s.main_disagreement).trim() !== ''
      ? String(s.main_disagreement)
      : 'אין'
  const footer = 'זוהי שכבת ניתוח מובנית, לא תשובה סופית.'
  const available = Number(s.available_experts ?? 0)
  if (available >= 2) {
    const consBlock = cons ? `✅ נקודות הסכמה: ${cons}\n` : ''
    return `🧠 סינתזת BEN (${ae})\n${rec}\n\n${consBlock}⚡ נקודות מחלוקת: ${disagree}\n\n${footer}`
  }
  return `🧠 סינתזת BEN (${ae})\n${rec}\n\n${footer}`
}

function synthesisBubbleContent(m) {
  if (isAdhocV2HebrewMessage(m)) return adhocSynthesisBubbleText(m)
  return m.content
}

function messageTextForDirection(m) {
  if (m.kind === 'council_synthesis' || m.kind === 'adhoc_synthesis') {
    return synthesisBubbleContent(m)
  }
  if (m.kind === 'action_card') {
    const payload = m.action_payload ?? m.payload
    if (typeof payload === 'string') return payload
    try {
      return JSON.stringify(payload ?? '')
    } catch {
      return ''
    }
  }
  return String(m.content ?? '')
}

function shouldRenderAssistantMarkdown(message) {
  if (message?.role !== 'assistant') return false
  const kind = message?.kind
  return kind !== 'council_error' && kind !== 'api_error'
}

function councilSynthesisBubbleText(s, anyExpertFailed) {
  const artifact = String(s.deliverable_artifact ?? '').trim()
  if (artifact) {
    const playbook = Array.isArray(s.operational_playbook) ? s.operational_playbook : []
    const lines = playbook.slice(0, 3).map((step, i) => {
      const cmd = String(step.command ?? '').trim()
      if (!cmd) return null
      const purpose = String(step.purpose ?? step.traceable_to ?? '').trim()
      return purpose ? `${i + 1}. \`${cmd}\` — ${purpose}` : `${i + 1}. \`${cmd}\``
    }).filter(Boolean)
    const playbookBlock = lines.length ? `\n\n🚀 Operational Playbook\n\n${lines.join('\n')}` : ''
    return `📦 Deliverable\n\n${artifact}${playbookBlock}`
  }
  const rec = s.recommendation ?? ''
  const prefix = anyExpertFailed ? 'Based on available expert inputs.\n\n' : ''
  return `${prefix}📦 Deliverable\n\n${rec}`
}

function OrgRecoveryBanner({ banner, onDismiss }) {
  if (!banner) return null
  return (
    <div className="org-recovery-banner" role="alert">
      <p className="org-recovery-title">{banner.message}</p>
      {banner.hint ? <p className="org-recovery-hint">{banner.hint}</p> : null}
      {onDismiss ? (
        <button type="button" className="org-recovery-dismiss" onClick={onDismiss}>
          Dismiss
        </button>
      ) : null}
    </div>
  )
}

function ClerkAuthControlsInner({ variant = 'settings' }) {
  const { isSignedIn } = useAuth()
  if (variant === 'shell' && isSignedIn) return null
  return (
    <div className={`auth-controls${variant === 'shell' ? ' auth-controls--shell' : ''}`}>
      {isSignedIn ? (
        <>
          <OrganizationSwitcher hidePersonal />
          <SignOutButton>
            <button type="button" className="auth-btn">
              Sign out
            </button>
          </SignOutButton>
        </>
      ) : (
        <SignInButton mode="modal">
          <button type="button" className="auth-btn auth-btn--signin">
            Sign in
          </button>
        </SignInButton>
      )}
    </div>
  )
}

function ClerkSignInBanner() {
  return (
    <div className="clerk-signin-banner" role="status">
      <p className="clerk-signin-banner__title">Sign in to use BEN</p>
      <p className="clerk-signin-banner__hint">
        Projects, conversations, and files require a Clerk account. Beta access is not a customer
        session.
      </p>
      <SignInButton mode="modal">
        <button type="button" className="auth-btn auth-btn--signin">
          Sign in
        </button>
      </SignInButton>
    </div>
  )
}

function ClerkAuthControls({ variant = 'settings' }) {
  if (!HAS_CLERK_UI) return null
  return <ClerkAuthControlsInner variant={variant} />
}

function CopyIcon({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  )
}

function EditIcon({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
    </svg>
  )
}

function resolveSqliteMessageId(message) {
  if (message?.sqlite_message_id != null) return Number(message.sqlite_message_id)
  const parsed = Number.parseInt(String(message?.id ?? ''), 10)
  return Number.isFinite(parsed) ? parsed : null
}

const messageActionBtnStyle = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: '0.35rem',
  padding: '0.2rem 0.35rem',
  fontSize: '0.75rem',
  lineHeight: 1.2,
  background: 'transparent',
  border: 'none',
  borderRadius: '4px',
  color: '#888',
  cursor: 'pointer',
}

function MessageActionBar({ role, content, onEditRequest, sqliteMessageId, onExpertOpinion, expertDisabled = false }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef(null)

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(String(content ?? ''))
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard unavailable */
    }
  }

  return (
    <div className="message-action-bar">
      {role === 'user' && onEditRequest ? (
        <button
          type="button"
          style={messageActionBtnStyle}
          onClick={() => onEditRequest(String(content ?? ''))}
          aria-label="Edit request"
        >
          <EditIcon />
          <span>עריכת בקשה</span>
        </button>
      ) : null}
      <ExpertOpinionMenu
        disabled={expertDisabled || sqliteMessageId == null}
        anchorMessageId={sqliteMessageId}
        onRequest={onExpertOpinion}
      />
      <button
        type="button"
        style={{
          ...messageActionBtnStyle,
          color: copied ? '#6fcf97' : '#888',
        }}
        onClick={handleCopy}
        aria-label={copied ? 'Copied' : 'Copy message'}
      >
        {copied ? (
          <>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#6fcf97" strokeWidth="2.5">
              <polyline points="20 6 9 17 4 12" />
            </svg>
            <span>הועתק</span>
          </>
        ) : (
          <>
            <CopyIcon />
            <span>העתקה</span>
          </>
        )}
      </button>
    </div>
  )
}

function conversationCopyLabel(m) {
  if (m.role === 'user') return 'User'
  if (m.kind === 'council_synthesis') return 'BEN Synthesis'
  const providerLabel = getSpeakingProviderById(m.provider_id)?.label
  if (providerLabel) return providerLabel
  const content = String(m.content ?? '')
  for (const [, head] of Object.entries(COUNCIL_LABEL)) {
    if (content.startsWith(`${head}: `)) return head
  }
  return 'Assistant'
}

function conversationCopyBody(m) {
  const content = String(m.content ?? '')
  if (m.role === 'user' || m.kind === 'council_synthesis') return content
  for (const head of Object.values(COUNCIL_LABEL)) {
    const prefix = `${head}: `
    if (content.startsWith(prefix)) return content.slice(prefix.length)
  }
  return content
}

function countAdhocSessionVoices(messages, sessionId) {
  if (!sessionId) return 0
  const voices = new Set()
  let windowOpen = true
  for (const m of messages ?? []) {
    if (m.kind === 'adhoc_synthesis' && m.adhoc_session_id === sessionId) {
      voices.clear()
      windowOpen = true
      continue
    }
    if (!windowOpen) continue
    if (m.role !== 'assistant') continue
    if (m.kind === 'adhoc_expert') {
      if (m.adhoc_session_id !== sessionId) continue
    } else if (m.kind === 'council_synthesis' || m.kind === 'council_error') {
      continue
    }
    if (m.provider_id) voices.add(m.provider_id)
  }
  return voices.size
}

function formatConversationForCopy(messages) {
  return (messages ?? [])
    .map((m) => {
      const body = conversationCopyBody(m).trim()
      if (!body) return null
      return `${conversationCopyLabel(m)}: ${body}`
    })
    .filter(Boolean)
    .join('\n\n')
}

function CopyConversationButton({ messages }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef(null)

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(formatConversationForCopy(messages))
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 2000)
    } catch {
      /* clipboard unavailable */
    }
  }

  return (
    <button
      type="button"
      className="copy-conversation-btn"
      onClick={handleCopy}
      aria-label={copied ? 'Conversation copied' : 'Copy conversation'}
      style={{
        alignSelf: 'flex-start',
        margin: '0 0 0.5rem',
        padding: '0.35rem 0.65rem',
        fontSize: '0.8rem',
        background: 'transparent',
        border: '1px solid #333',
        borderRadius: '6px',
        color: copied ? '#6fcf97' : '#aaa',
        cursor: 'pointer',
      }}
    >
      {copied ? 'Copied ✓' : 'Copy conversation'}
    </button>
  )
}

function App() {
  const { getToken, clerkEnabled, isLoaded, isSignedIn } = useBenAuthContext()
  const persistentReady = isClerkPersistentSessionReady({
    clerkEnabled,
    isLoaded,
    isSignedIn,
  })
  const showClerkSignIn = shouldShowClerkSignIn({ clerkEnabled, isLoaded, isSignedIn })
  const betaSession = useBetaSession()
  const [threads, setThreads] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [input, setInput] = useState('')
  const [tier, setTier] = useState('free')
  const [loading, setLoading] = useState(false)
  const [hydrating, setHydrating] = useState(true)
  const [orgBanner, setOrgBanner] = useState(null)
  const [activeSpeakingProviderId, setActiveSpeakingProviderId] = useState(
    DEFAULT_SPEAKING_PROVIDER_ID
  )
  const [selectedGptModel, setSelectedGptModel] = useState(() =>
    coerceRegisteredModel('gpt', DEFAULT_PROVIDER_MODELS.gpt)
  )
  const [selectedClaudeModel, setSelectedClaudeModel] = useState(() =>
    coerceRegisteredModel('claude', DEFAULT_PROVIDER_MODELS.claude)
  )
  const [selectedGeminiModel, setSelectedGeminiModel] = useState(() =>
    coerceRegisteredModel('gemini', DEFAULT_PROVIDER_MODELS.gemini)
  )
  const [activeProjectId, setActiveProjectId] = useState(null)
  const [projectOptions, setProjectOptions] = useState([])
  const [projectToast, setProjectToast] = useState(null)
  const [toolTelemetry, setToolTelemetry] = useState(null)
  const [deletingThread, setDeletingThread] = useState(false)
  const [promotingThread, setPromotingThread] = useState(false)
  const [receiptCapturing, setReceiptCapturing] = useState(false)
  const [filesOpen, setFilesOpen] = useState(false)
  const [projectsOpen, setProjectsOpen] = useState(false)
  const [fileUploading, setFileUploading] = useState(false)
  const [attentionFocusRequest, setAttentionFocusRequest] = useState(null)
  const [newProjectModalOpen, setNewProjectModalOpen] = useState(false)
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [newsRoute, setNewsRoute] = useState(() => parseNewsLocation(window.location.pathname))
  const [creatingProject, setCreatingProject] = useState(false)
  const [newProjectError, setNewProjectError] = useState(null)
  const { isOverlayNav } = useNavDrawerMode()
  const { canCreate: canCreateProject, reason: createProjectReason } = useProjectCreatePrivilege()
  const [navDrawerOpen, setNavDrawerOpen] = useState(readInitialNavDrawerOpen)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const messagesScrollRef = useRef(null)
  const messagesEndRef = useRef(null)
  const composerFooterRef = useRef(null)
  const navDrawerRef = useRef(null)
  const navMenuButtonRef = useRef(null)
  const settingsButtonRef = useRef(null)
  const settingsPanelRef = useRef(null)
  const invoiceCaptureRef = useRef(null)
  const creditCaptureRef = useRef(null)
  const attachFileRef = useRef(null)

  const activeProjectName = useMemo(
    () => projectOptions.find((p) => p.id === activeProjectId)?.name ?? '',
    [projectOptions, activeProjectId]
  )

  const closeNavDrawer = useCallback(() => setNavDrawerOpen(false), [])
  const closeNavDrawerIfOverlay = useCallback(() => {
    if (isOverlayNav) setNavDrawerOpen(false)
  }, [isOverlayNav])
  const closeSettings = useCallback(() => setSettingsOpen(false), [])

  const openNewsFeed = useCallback(() => {
    closeSettings()
    closeNavDrawerIfOverlay()
    window.history.pushState({ benNews: true }, '', newsFeedPath())
    setNewsRoute({ view: 'feed', eventId: null })
  }, [closeNavDrawerIfOverlay, closeSettings])

  const openNewsTopic = useCallback(
    (eventId) => {
      const id = String(eventId || '').trim()
      if (!id) return
      closeSettings()
      closeNavDrawerIfOverlay()
      window.history.pushState({ benNews: true }, '', newsTopicPath(id))
      setNewsRoute({ view: 'detail', eventId: id })
    },
    [closeNavDrawerIfOverlay, closeSettings]
  )

  const closeNews = useCallback(() => {
    const next = parseNewsLocation(window.location.pathname)
    if (next) {
      window.history.pushState({}, '', '/')
    }
    setNewsRoute(null)
  }, [])

  useEffect(() => {
    const onPopState = () => {
      setNewsRoute(parseNewsLocation(window.location.pathname))
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  useEffect(() => {
    setNavDrawerOpen(!isOverlayNav)
  }, [isOverlayNav])

  useDismissOnOutside({
    open: navDrawerOpen && isOverlayNav,
    onDismiss: closeNavDrawer,
    containerRef: navDrawerRef,
    triggerRef: navMenuButtonRef,
  })

  const buildAppHeaders = useCallback(
    (extraHeaders = {}) =>
      buildBenHeaders(getToken, extraHeaders, { projectName: activeProjectName }, betaSession.getSessionHeaders({
        projectName: activeProjectName,
      })),
    [getToken, activeProjectName, betaSession]
  )
  const buildAppHeadersRef = useRef(buildAppHeaders)
  buildAppHeadersRef.current = buildAppHeaders
  const persistentHeaders = useCallback(
    (extraHeaders = {}) => buildAppHeadersRef.current(extraHeaders),
    []
  )

  const COMPOSER_SCROLL_GUTTER_PX = 24

  const updateComposerScrollPadding = useCallback(() => {
    const scroller = messagesScrollRef.current
    const footer = composerFooterRef.current
    if (!scroller || !footer) return
    const shell = footer.querySelector('.composer-shell')
    const shellHeight = Math.ceil(shell?.getBoundingClientRect().height ?? 0)
    const footerHeight = Math.ceil(footer.getBoundingClientRect().height)
    const stackHeight = shellHeight || footerHeight
    const clearance = Math.max(footerHeight, stackHeight + COMPOSER_SCROLL_GUTTER_PX)
    scroller.style.setProperty('--composer-stack-height', `${stackHeight}px`)
    scroller.style.setProperty('--composer-scroll-clearance', `${clearance}px`)
  }, [])

  useEffect(() => {
    updateComposerScrollPadding()
  }, [navDrawerOpen, settingsOpen, updateComposerScrollPadding])

  const scrollToLatest = useCallback((behavior = 'smooth') => {
    requestAnimationFrame(() => {
      updateComposerScrollPadding()
      const end = messagesEndRef.current
      if (end) {
        end.scrollIntoView({ behavior, block: 'end' })
        return
      }
      const scroller = messagesScrollRef.current
      if (scroller) {
        scroller.scrollTo({ top: scroller.scrollHeight, behavior })
      }
    })
  }, [updateComposerScrollPadding])

  const active = useMemo(
    () => threads.find((t) => t.id === activeId) ?? null,
    [threads, activeId]
  )

  const platformFeatures = usePlatformActiveFeatures(persistentReady ? persistentHeaders : null)

  const [catalogKeysOverride, setCatalogKeysOverride] = useState(null)

  useEffect(() => {
    setCatalogKeysOverride(null)
  }, [platformFeatures.catalogKeys])

  const liveCatalogKeys = catalogKeysOverride ?? platformFeatures.catalogKeys

  const handleWorkspaceFeaturesChange = useCallback((payload) => {
    setCatalogKeysOverride(
      Array.isArray(payload?.catalogKeys) ? payload.catalogKeys : []
    )
  }, [])

  const apiThreadId = useMemo(() => serverThreadIdForApi(activeId), [activeId])

  // Phase 1: composer/provider select must not depend on Switchboard activations.
  const canSendComposer = useMemo(() => {
    if (loading || !persistentReady) return false
    return Boolean(input.trim())
  }, [loading, persistentReady, input])

  const handleEngineSelect = useCallback((providerId) => {
    setActiveSpeakingProviderId(providerId)
    const tier1 = getTier1Model(providerId)
    if (providerId === 'gpt') setSelectedGptModel(tier1)
    else if (providerId === 'claude') setSelectedClaudeModel(tier1)
    else if (providerId === 'gemini') setSelectedGeminiModel(tier1)
  }, [])

  const activeSpeakingProvider = useMemo(
    () => getSpeakingProviderById(activeSpeakingProviderId),
    [activeSpeakingProviderId]
  )

  const providerModels = useMemo(
    () => ({
      gpt: selectedGptModel,
      claude: selectedClaudeModel,
      gemini: selectedGeminiModel,
    }),
    [selectedGptModel, selectedClaudeModel, selectedGeminiModel]
  )

  const activeModelOverride = useMemo(() => {
    const raw = providerModels[activeSpeakingProviderId] ?? ''
    return coerceRegisteredModel(activeSpeakingProviderId, raw)
  }, [providerModels, activeSpeakingProviderId])

  const handleProviderModelChange = useCallback((providerId, modelId) => {
    if (providerId === 'gpt') setSelectedGptModel(modelId)
    else if (providerId === 'claude') setSelectedClaudeModel(modelId)
    else if (providerId === 'gemini') setSelectedGeminiModel(modelId)
  }, [])

  const attachMenuItems = useMemo(
    () => [
      {
        id: 'attach',
        label: 'Attach file',
        icon: '📎',
        disabled: loading || receiptCapturing || fileUploading || !persistentReady,
        onClick: () => {
          // Defer so the attach menu can close without cancelling the native picker.
          window.setTimeout(() => attachFileRef.current?.click(), 0)
        },
      },
      {
        id: 'invoice',
        label: 'Capture invoice',
        icon: '🧾',
        disabled: loading || receiptCapturing || fileUploading || !persistentReady,
        onClick: () => invoiceCaptureRef.current?.open(),
      },
      {
        id: 'credit',
        label: 'Credit memo',
        icon: '↩',
        disabled: loading || receiptCapturing || fileUploading || !persistentReady,
        onClick: () => creditCaptureRef.current?.open(),
      },
    ],
    [loading, receiptCapturing, fileUploading, persistentReady]
  )

  const openFilesLibrary = useCallback(() => {
    if (!persistentReady) return
    closeNavDrawerIfOverlay()
    setProjectsOpen(false)
    setFilesOpen(true)
  }, [closeNavDrawerIfOverlay, persistentReady])

  const closeFilesLibrary = useCallback(() => setFilesOpen(false), [])

  const openProjectsLibrary = useCallback(() => {
    if (!persistentReady) return
    closeNavDrawerIfOverlay()
    setFilesOpen(false)
    setProjectsOpen(true)
  }, [closeNavDrawerIfOverlay, persistentReady])

  const closeProjectsLibrary = useCallback(() => setProjectsOpen(false), [])

  const handleOpenProject = useCallback((project) => {
    const id = String(project?.id || '').trim()
    if (!id) return
    setActiveProjectId(id)
    setProjectOptions((prev) => {
      if (prev.some((row) => row.id === id)) return prev
      return [
        {
          id,
          name: project.name || 'Project',
          status: project.status || 'active',
        },
        ...prev,
      ]
    })
    setProjectsOpen(false)
  }, [])

  const shellAccent = activeSpeakingProvider?.accent ?? '#5b8cff'

  const composerPlaceholder = 'Message BEN…'
  const composerAriaLabel = 'Message'

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      if (!persistentReady) {
        if (!cancelled) setProjectOptions([])
        return
      }
      try {
        const headers = await acquirePersistentHeaders(persistentHeaders)
        const data = await fetchProjects(headers, { limit: PROJECT_LIBRARY_DEFAULT_LIMIT })
        if (cancelled) return
        const list = data.items || data.projects || []
        setProjectOptions(list)
        if (!activeProjectId && list[0]?.id) setActiveProjectId(list[0].id)
      } catch (err) {
        if (isAuthTokenUnavailable(err)) return
        if (!cancelled) setProjectOptions([])
      }
    })()
    return () => {
      cancelled = true
    }
  }, [persistentReady, persistentHeaders, activeProjectId])

  useEffect(() => {
    workspaceFileInventory.configure({
      workspaceId: persistentReady ? activeProjectId || null : null,
      buildHeaders: persistentReady ? persistentHeaders : null,
    })
  }, [persistentReady, activeProjectId, persistentHeaders])

  useEffect(() => {
    return () => {
      workspaceFileInventory.configure({ workspaceId: null, buildHeaders: null })
    }
  }, [])

  useEffect(() => {
    const footer = composerFooterRef.current
    const scroller = messagesScrollRef.current
    if (!footer || !scroller) return

    updateComposerScrollPadding()

    const ro = new ResizeObserver(() => {
      updateComposerScrollPadding()
    })
    ro.observe(footer)
    const shell = footer.querySelector('.composer-shell')
    if (shell) ro.observe(shell)

    window.addEventListener('resize', updateComposerScrollPadding)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', updateComposerScrollPadding)
    }
  }, [updateComposerScrollPadding, active?.messages?.length])

  useEffect(() => {
    scrollToLatest(loading ? 'auto' : 'smooth')
  }, [
    active?.messages,
    activeId,
    loading,
    scrollToLatest,
  ])

  const loadThreadMessages = useCallback(
    async (threadId) => {
      if (!isPersistedThreadId(threadId)) return
      const headers = await buildAppHeaders()
      const data = await fetchThreadDetail(threadId, headers)
      const messages = (data.messages || []).map(mapApiMessage)
      setThreads((prev) =>
        prev.map((t) =>
          t.id === threadId
            ? { ...t, title: data.thread?.title || t.title, messages, loaded: true }
            : t
        )
      )
    },
    [getToken]
  )

  const selectThread = useCallback(
    async (threadId) => {
      setActiveId(threadId)
      if (isOverlayNav) setNavDrawerOpen(false)
      if (isPersistedThreadId(threadId)) setStoredActiveThreadId(threadId)
      const t = threads.find((x) => x.id === threadId)
      if (t && isPersistedThreadId(threadId) && !t.loaded) {
        try {
          await loadThreadMessages(threadId)
        } catch {
          /* keep partial UI */
        }
      }
    },
    [threads, loadThreadMessages, isOverlayNav]
  )

  useEffect(() => {
    if (recoverStaleCouncilUi()) {
      setLoading(false)
      logCouncilLifecycle('stale_runtime_state_recovered')
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      if (!persistentReady) {
        setHydrating(false)
        return
      }
      setHydrating(true)
      try {
        const headers = await buildAppHeaders()
        const data = await fetchThreadList(headers)
        if (cancelled) return
        const serverThreads = (data.threads || []).map(mapThreadFromList)
        const stored = getStoredActiveThreadId()
        const active =
          stored && serverThreads.some((t) => t.id === stored)
            ? stored
            : serverThreads[0]?.id ?? null
        setThreads(serverThreads)
        setActiveId(active)
        if (active && isPersistedThreadId(active)) {
          await loadThreadMessages(active)
        }
      } catch (e) {
        if (!cancelled) {
          if (e.parsed?.code === CLERK_ORG_REQUIRED) {
            setOrgBanner({ message: e.parsed.message, hint: e.parsed.hint })
            const stored = getStoredActiveThreadId()
            if (stored) {
              setActiveId(stored)
              setThreads((prev) => {
                if (prev.some((t) => t.id === stored)) return prev
                return [{ id: stored, title: 'Conversation', messages: [], loaded: false }, ...prev]
              })
            }
          } else {
            const stored = getStoredActiveThreadId()
            if (stored && isPersistedThreadId(stored)) {
              setActiveId(stored)
              setThreads((prev) => {
                if (prev.some((t) => t.id === stored)) return prev
                return [{ id: stored, title: 'Conversation', messages: [], loaded: false }, ...prev]
              })
              try {
                await loadThreadMessages(stored)
              } catch (inner) {
                if (inner.parsed?.code === CLERK_ORG_REQUIRED) {
                  setOrgBanner({ message: inner.parsed.message, hint: inner.parsed.hint })
                }
              }
            }
          }
        }
      } finally {
        if (!cancelled) setHydrating(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [persistentReady, getToken, loadThreadMessages, buildAppHeaders])

  const newThread = useCallback(() => {
    const id = `${DRAFT_PREFIX}${crypto.randomUUID()}`
    const t = { id, title: 'New conversation', messages: [], loaded: true, isDraft: true }
    setThreads((prev) => [t, ...prev])
    setActiveId(id)
    setStoredActiveThreadId(null)
    return id
  }, [])

  const handleEditRequest = useCallback((text) => {
    setInput(text)
  }, [])

  const pushThreadAssistantError = useCallback((tid, content, kind = 'api_error') => {
    setThreads((prev) =>
      prev.map((t) =>
        t.id === tid
          ? {
              ...t,
              messages: [
                ...t.messages,
                { role: 'assistant', kind, content, model_used: '', cost_usd: 0 },
              ],
            }
          : t
      )
    )
  }, [])

  const handleDeleteActiveConversation = useCallback(async () => {
    if (!activeId || deletingThread) return
    const confirmed = window.confirm(singleDeleteConfirmMessage())
    if (!confirmed) return

    const tid = activeId
    const persisted = isPersistedThreadId(tid)
    const threadSnapshot = threads.find((t) => t.id === tid)
    const previousStoredId = getStoredActiveThreadId()

    setThreads((prev) => prev.filter((t) => t.id !== tid))
    if (previousStoredId === tid) setStoredActiveThreadId(null)
    newThread()
    closeNavDrawerIfOverlay()

    if (!persisted) return

    setDeletingThread(true)
    try {
      const headers = await buildAppHeaders()
      await deleteThread(tid, headers)
    } catch (e) {
      if (threadSnapshot) {
        setThreads((prev) => {
          if (prev.some((t) => t.id === threadSnapshot.id)) return prev
          return [threadSnapshot, ...prev]
        })
        setActiveId(tid)
        if (previousStoredId === tid) setStoredActiveThreadId(tid)
      }
      setProjectToast(e.message || 'Could not delete conversation.')
      window.setTimeout(() => setProjectToast(null), 5000)
    } finally {
      setDeletingThread(false)
    }
  }, [
    activeId,
    buildAppHeaders,
    closeNavDrawerIfOverlay,
    deletingThread,
    newThread,
    threads,
  ])

  const handlePromoteActiveConversation = useCallback(
    async ({ projectName, projectSlug }) => {
      if (!activeId || promotingThread || !isPersistedThreadId(activeId)) return
      setPromotingThread(true)
      try {
        const headers = await buildAppHeaders()
        const data = await promoteThread(activeId, projectSlug, headers)
        const thread = data.thread || {}
        setThreads((prev) =>
          prev.map((t) =>
            t.id === activeId
              ? {
                  ...t,
                  title: projectName || t.title,
                  sessionType: thread.session_type || 'project_setup',
                  projectSlug: thread.project_slug || projectSlug,
                }
              : t
          )
        )
        setProjectToast('Promoted to project workspace — filesystem tools are now active.')
        window.setTimeout(() => setProjectToast(null), 4500)
      } catch (e) {
        const parsed = parseBenErrorResponse(e.status, e.data)
        pushThreadAssistantError(
          activeId,
          parsed?.message || e.message || 'Could not promote conversation.',
          'api_error'
        )
      } finally {
        setPromotingThread(false)
      }
    },
    [activeId, buildAppHeaders, promotingThread, pushThreadAssistantError]
  )

  const handleBulkDeleteThreads = useCallback(
    async (threadIds) => {
      if (!threadIds?.length) return

      const snapshots = threads.filter((t) => threadIds.includes(t.id))
      const previousActiveId = activeId
      const previousStoredId = getStoredActiveThreadId()
      const persistedIds = threadIds.filter((id) => isPersistedThreadId(id))

      setThreads((prev) => prev.filter((t) => !threadIds.includes(t.id)))
      if (previousStoredId && threadIds.includes(previousStoredId)) {
        setStoredActiveThreadId(null)
      }
      if (previousActiveId && threadIds.includes(previousActiveId)) {
        newThread()
      }

      if (!persistedIds.length) return

      const headers = await buildAppHeaders()
      const results = await Promise.all(
        persistedIds.map(async (tid) => {
          try {
            await deleteThread(tid, headers)
            return { tid, ok: true }
          } catch {
            return { tid, ok: false }
          }
        })
      )
      const failed = results.filter((row) => !row.ok).map((row) => row.tid)
      if (!failed.length) return

      const restoreById = new Map(snapshots.map((thread) => [thread.id, thread]))
      setThreads((prev) => {
        const next = [...prev]
        for (const tid of failed) {
          const snapshot = restoreById.get(tid)
          if (snapshot && !next.some((t) => t.id === tid)) {
            next.unshift(snapshot)
          }
        }
        return next
      })

      if (failed.includes(previousActiveId)) {
        setActiveId(previousActiveId)
        if (isPersistedThreadId(previousActiveId)) {
          setStoredActiveThreadId(previousActiveId)
        }
      }

      setProjectToast(
        failed.length === threadIds.length
          ? 'Could not delete selected conversations.'
          : `Deleted ${threadIds.length - failed.length} conversation(s); ${failed.length} could not be removed.`
      )
      window.setTimeout(() => setProjectToast(null), 5000)
    },
    [activeId, buildAppHeaders, newThread, threads]
  )

  const send = useCallback(async () => {
    if (loading) return
    const text = input.trim()
    let tid = activeId
    if (!tid || !threads.some((x) => x.id === tid)) tid = newThread()

    if (!text) return
    setLoading(true)
    try {
      const headers = await acquirePersistentHeaders(persistentHeaders)
      const userMsg = { role: 'user', content: text }
      setInput('')
      setThreads((prev) =>
        prev.map((t) =>
          t.id === tid ? { ...t, title: text.slice(0, 48) || t.title, messages: [...t.messages, userMsg] } : t
        )
      )
      const apiThreadId = serverThreadIdForApi(tid)
      const threadProjectSlug = threads.find((x) => x.id === tid)?.projectSlug
      if (threadProjectSlug && text) {
        setAttentionFocusRequest({
          key: `${Date.now()}-${tid}`,
          query: text,
          threadId: apiThreadId || tid,
        })
      }
      const clientRequestId = createClientRequestId()
      setThreads((prev) =>
        prev.map((t) =>
          t.id === tid
            ? {
                ...t,
                messages: [
                  ...t.messages,
                  {
                    role: 'assistant',
                    content: '',
                    model_used: '',
                    provider_id: activeSpeakingProviderId,
                    provider_used: '',
                    cost_usd: 0,
                  },
                ],
                loaded: true,
              }
            : t
        )
      )

      let streamOk = false
      let serverTid = apiThreadId || tid
      for await (const event of postChatStream({
        message: text,
        threadId: apiThreadId,
        projectId: activeProjectId || undefined,
        tier,
        providerId: activeSpeakingProviderId,
        modelOverride: activeModelOverride,
        clientRequestId,
        headers,
      })) {
        if (event.type === 'meta' && event.thread_id) {
          serverTid = event.thread_id
          if (event.provider_id) {
            setActiveSpeakingProviderId(event.provider_id)
          }
        } else if (event.type === 'mutated_state') {
          setThreads((prev) =>
            prev.map((t) => {
              if (t.id !== tid && t.id !== serverTid) return t
              return {
                ...t,
                messages: [
                  ...t.messages,
                  {
                    role: 'assistant',
                    kind: 'action_card',
                    card_type: event.card_type,
                    action_payload: event.payload,
                    content: '',
                    model_used: '',
                    cost_usd: 0,
                  },
                ],
              }
            })
          )
        } else if (event.type === 'tool_active') {
          setToolTelemetry(event.message || `⚙️ System: Running ${event.tool || 'tool'}...`)
        } else if (event.type === 'tool_done') {
          setToolTelemetry(null)
        } else if (event.type === 'chunk') {
          setToolTelemetry(null)
          streamOk = true
          const chunk = event.content ?? ''
          setThreads((prev) =>
            prev.map((t) => {
              if (t.id !== tid && t.id !== serverTid) return t
              const msgs = [...t.messages]
              const last = msgs[msgs.length - 1]
              if (last?.role === 'assistant') {
                msgs[msgs.length - 1] = { ...last, content: `${last.content || ''}${chunk}` }
              }
              const nextId = serverTid && isDraftThreadId(t.id) ? serverTid : t.id
              return { ...t, id: nextId, messages: msgs, isDraft: false }
            })
          )
        } else if (event.type === 'done') {
          streamOk = true
          serverTid = event.thread_id || serverTid
          if (event.provider_id) {
            setActiveSpeakingProviderId(event.provider_id)
          }
          setOrgBanner(null)
          setThreads((prev) => {
            const nextList = prev.map((t) => {
              if (t.id !== tid && t.id !== serverTid) return t
              const msgs = [...t.messages]
              const last = msgs[msgs.length - 1]
              if (last?.role === 'assistant') {
                msgs[msgs.length - 1] = {
                  ...last,
                  content: event.response ?? last.content ?? '',
                  model_used: event.model_used ?? '',
                  provider_id: event.provider_id ?? activeSpeakingProviderId,
                  provider_used: event.provider_used ?? '',
                  cost_usd: event.cost_usd ?? 0,
                  ttft_ms: event.ttft_ms ?? null,
                  tps: event.tps ?? null,
                  sqlite_message_id: event.sqlite_assistant_id ?? last.sqlite_message_id ?? null,
                  used_files: usedFilesFromDoneEvent(event),
                  workspace_files_unavailable_note: unavailableChatNote(
                    event.workspace_files_unavailable_count
                  ),
                }
              }
              if (event.sqlite_user_id != null && msgs.length >= 2) {
                const userIdx = msgs.length - 2
                if (msgs[userIdx]?.role === 'user') {
                  msgs[userIdx] = {
                    ...msgs[userIdx],
                    sqlite_message_id: event.sqlite_user_id,
                  }
                }
              }
              const nextId = serverTid && (isDraftThreadId(t.id) || t.id === tid) ? serverTid : t.id
              return { ...t, id: nextId, messages: msgs, loaded: true, isDraft: false }
            })
            if (serverTid && !nextList.some((t) => t.id === serverTid)) {
              const src = nextList.find((t) => t.id === tid)
              if (src) nextList.unshift({ ...src, id: serverTid })
            }
            return nextList
          })
          if (serverTid) {
            setActiveId(serverTid)
            setStoredActiveThreadId(serverTid)
          }
        } else if (event.type === 'error') {
          throw new Error(event.message || 'Chat stream failed.')
        }
      }
      if (!streamOk) {
        throw new Error('Chat returned no content.')
      }
    } catch (e) {
      const parsed = parseBenErrorResponse(e.status, e.data)
      if (parsed?.code === CLERK_ORG_REQUIRED) {
        setOrgBanner({ message: parsed.message, hint: parsed.hint })
        return
      }
      const msg = isAuthTokenUnavailable(e)
        ? e.message || 'Sign in required.'
        : parsed?.message || humanizeChatFetchError(e)
      setThreads((prev) =>
        prev.map((t) =>
          t.id === tid
            ? {
                ...t,
                messages: [
                  ...t.messages.filter((m, i, arr) => !(i === arr.length - 1 && m.role === 'assistant' && !m.content)),
                  { role: 'assistant', kind: 'api_error', content: msg, model_used: '', cost_usd: 0 },
                ],
              }
            : t
        )
      )
    } finally {
      setLoading(false)
      setToolTelemetry(null)
    }
  }, [
    input,
    loading,
    activeId,
    threads,
    tier,
    activeSpeakingProviderId,
    activeModelOverride,
    newThread,
    persistentHeaders,
    activeProjectId,
  ])

  const applyCouncilMessages = useCallback((tid, extras, resolvedId) => {
    setThreads((prev) => {
      let next = prev.map((t) =>
        t.id === tid
          ? {
              ...t,
              messages: [...t.messages, ...extras],
              loaded: true,
              isDraft: false,
              ...(resolvedId ? { id: resolvedId } : {}),
            }
          : t
      )
      if (resolvedId && !next.some((t) => t.id === resolvedId)) {
        const src = next.find((t) => t.id === tid || t.id === resolvedId)
        if (src) next = [{ ...src, id: resolvedId }, ...next.filter((t) => t.id !== tid)]
      }
      return next
    })
    if (resolvedId) {
      setActiveId(resolvedId)
      setStoredActiveThreadId(resolvedId)
    }
  }, [])

  const council = useCallback(async (opts = {}) => {
    const text = (opts.question ?? input).trim()
    const forceCodebase = Boolean(opts.forceCodebase)
    if (!text || loading) return
    let tid = activeId
    if (!tid || !threads.some((x) => x.id === tid)) tid = newThread()
    if (isDraftThreadId(tid) && !forceCodebase) {
      setThreads((prev) =>
        prev.map((t) =>
          t.id === tid
            ? {
                ...t,
                messages: [
                  ...t.messages,
                  {
                    role: 'assistant',
                    kind: 'council_error',
                    content:
                      'Council requires a saved server thread. Send a chat message first.',
                    model_used: '',
                    cost_usd: 0,
                  },
                ],
              }
            : t
        )
      )
      return
    }
    const apiThreadId = serverThreadIdForApi(tid)
    const guard = canSubmitCouncil(text, apiThreadId)
    if (!guard.ok) {
      const blocked =
        guard.reason === 'duplicate'
          ? 'This council request was just submitted. Please wait a moment.'
          : 'Council is already in progress. Please wait for it to finish.'
      setThreads((prev) =>
        prev.map((t) =>
          t.id === tid
            ? {
                ...t,
                messages: [
                  ...t.messages,
                  { role: 'assistant', kind: 'council_error', content: blocked, model_used: '', cost_usd: 0 },
                ],
              }
            : t
        )
      )
      return
    }
    markCouncilSubmitStarted(guard.fingerprint)
    const clientRequestId = createClientRequestId()
    markCouncilPending({ clientRequestId, threadId: apiThreadId || tid })
    const userMsg = { role: 'user', content: text }
    setInput('')
    setThreads((prev) =>
      prev.map((t) =>
        t.id === tid ? { ...t, title: text.slice(0, 48) || t.title, messages: [...t.messages, userMsg] } : t
      )
    )

    logCouncilLifecycle('council_submit_started', {
      hasThreadId: Boolean(apiThreadId),
    })
    const controller = new AbortController()
    setLoading(true)

    try {
      const headers = await buildAppHeaders()
      logCouncilLifecycle('council_request_sent', {
        hasAuth: Boolean(headers.Authorization),
        hasThreadId: Boolean(apiThreadId),
      })

      const appendCouncilMessage = (msg) => {
        setThreads((prev) =>
          prev.map((t) =>
            t.id === tid
              ? { ...t, messages: [...t.messages, msg], loaded: true, isDraft: false }
              : t
          )
        )
      }

      if (USE_COUNCIL_STREAM) {
        let streamOk = false
        appendCouncilMessage({
          role: 'assistant',
          content: '',
          model_used: '',
          cost_usd: 0,
        })
        try {
          for await (const event of postCouncilStream({
            question: text,
            threadId: apiThreadId,
            clientRequestId,
            headers,
            signal: controller.signal,
            forceCodebase,
          })) {
            if (event.type === 'chunk') {
              streamOk = true
              const chunk = event.content ?? ''
              setThreads((prev) =>
                prev.map((t) => {
                  if (t.id !== tid) return t
                  const msgs = [...t.messages]
                  const last = msgs[msgs.length - 1]
                  if (last?.role === 'assistant' && !last.kind) {
                    msgs[msgs.length - 1] = { ...last, content: `${last.content || ''}${chunk}` }
                  }
                  return { ...t, messages: msgs }
                })
              )
            } else if (event.type === 'done') {
              streamOk = true
              setThreads((prev) =>
                prev.map((t) => {
                  if (t.id !== tid) return t
                  const msgs = [...t.messages]
                  const last = msgs[msgs.length - 1]
                  if (last?.role === 'assistant' && !last.kind) {
                    msgs[msgs.length - 1] = {
                      ...last,
                      content: event.response ?? last.content ?? '',
                      model_used: event.model_used ?? '',
                      provider_id: event.provider_id ?? activeSpeakingProviderId,
                      provider_used: event.provider_used ?? '',
                    }
                  }
                  return { ...t, messages: msgs }
                })
              )
            } else if (event.type === 'error') {
              appendCouncilMessage({
                role: 'assistant',
                kind: 'council_error',
                content: event.message || 'Council failed. You can retry.',
                model_used: '',
                cost_usd: 0,
              })
            }
          }
          if (streamOk) {
            setOrgBanner(null)
            clearCouncilPending()
            logCouncilLifecycle('council_stream_completed')
            return
          }
        } catch (streamErr) {
          const parsed = parseBenErrorResponse(streamErr.status, streamErr.data)
          if (parsed?.code === CLERK_ORG_REQUIRED) {
            setOrgBanner({ message: parsed.message, hint: parsed.hint })
            logCouncilLifecycle('council_submit_failed', {
              status: streamErr.status,
              reason: 'clerk_org_required',
            })
            return
          }
          logCouncilLifecycle('council_stream_fallback', {
            reason: streamErr?.name || 'error',
          })
        }
      }

      const abortTimer = setTimeout(() => controller.abort(), COUNCIL_CLIENT_TIMEOUT_MS)
      try {
        const { res, data } = await postCouncil({
          question: text,
          threadId: apiThreadId,
          clientRequestId,
          headers,
          signal: controller.signal,
          forceCodebase,
        })
        logCouncilLifecycle('council_response_received', { status: res.status })

        if (!res.ok) {
          const parsed = parseBenErrorResponse(res.status, data)
          if (parsed?.code === CLERK_ORG_REQUIRED) {
            setOrgBanner({ message: parsed.message, hint: parsed.hint })
            logCouncilLifecycle('council_submit_failed', {
              status: res.status,
              reason: 'clerk_org_required',
            })
            return
          }
          const errText = humanizeCouncilHttpError(res.status, data)
          logCouncilLifecycle('council_submit_failed', {
            status: res.status,
            reason: parsed?.code || 'http_error',
          })
          setThreads((prev) =>
            prev.map((t) =>
              t.id === tid
                ? {
                    ...t,
                    messages: [
                      ...t.messages,
                      { role: 'assistant', kind: 'council_error', content: errText, model_used: '', cost_usd: 0 },
                    ],
                  }
                : t
            )
          )
          return
        }

        setOrgBanner(null)
        clearCouncilPending()
        const extras = councilResponseToMessages(data, councilSynthesisBubbleText)
        applyCouncilMessages(tid, extras, apiThreadId)
        logCouncilLifecycle('council_render_completed', {
          messageCount: extras.length,
          runtimeState: data.runtime_state,
          idempotentReplay: data.idempotent_replay,
        })

        if (!apiThreadId) {
          void (async () => {
            try {
              const listData = await fetchThreadList(headers)
              const latest = listData.threads?.[0]
              if (latest?.id) {
                setThreads((prev) =>
                  prev.map((t) =>
                    t.id === tid ? { ...t, id: latest.id, isDraft: false, loaded: true } : t
                  )
                )
                setActiveId(latest.id)
                setStoredActiveThreadId(latest.id)
              }
            } catch (inner) {
              if (inner.parsed?.code === CLERK_ORG_REQUIRED) {
                setOrgBanner({ message: inner.parsed.message, hint: inner.parsed.hint })
              }
            }
          })()
        }
      } finally {
        clearTimeout(abortTimer)
      }
    } catch (e) {
      const errText = humanizeCouncilFetchError(e)
      logCouncilLifecycle('council_submit_failed', {
        reason: e?.name || 'error',
      })
      setThreads((prev) =>
        prev.map((t) =>
          t.id === tid
            ? {
                ...t,
                messages: [
                  ...t.messages,
                  { role: 'assistant', kind: 'council_error', content: errText, model_used: '', cost_usd: 0 },
                ],
              }
            : t
        )
      )
    } finally {
      setLoading(false)
      markCouncilSubmitFinished()
      clearCouncilPending()
      logCouncilLifecycle('council_submit_finally')
    }
  }, [
    input,
    loading,
    activeId,
    threads,
    newThread,
    getToken,
    applyCouncilMessages,
  ])

  const handleComposerSubmit = useCallback(() => {
    const text = input.trim()
    if (!text) return
    closeNavDrawerIfOverlay()
    closeSettings()
    if (CODEBASE_TRIGGER.test(text)) {
      council({ question: text, forceCodebase: true })
      return
    }
    void send()
  }, [input, send, council, closeSettings, closeNavDrawerIfOverlay])

  const appendThreadMessages = useCallback((tid, newMessages) => {
    setThreads((prev) =>
      prev.map((t) =>
        t.id === tid ? { ...t, messages: [...t.messages, ...newMessages], loaded: true } : t
      )
    )
  }, [])

  const insertThreadMessageAfter = useCallback((tid, anchorIndex, newMessage) => {
    setThreads((prev) =>
      prev.map((t) => {
        if (t.id !== tid) return t
        const messages = [...t.messages]
        messages.splice(anchorIndex + 1, 0, newMessage)
        return { ...t, messages, loaded: true }
      })
    )
  }, [])

  const updateThreadMessageAt = useCallback((tid, messageIndex, patch) => {
    setThreads((prev) =>
      prev.map((t) => {
        if (t.id !== tid) return t
        const messages = [...t.messages]
        const current = messages[messageIndex]
        if (!current) return t
        messages[messageIndex] = { ...current, ...patch }
        return { ...t, messages, loaded: true }
      })
    )
  }, [])

  const handleExpertOpinion = useCallback(
    async ({ anchorIndex, anchorMessageId, providerId, opinionMode }) => {
      if (loading || anchorMessageId == null || anchorIndex == null) return
      let tid = activeId
      if (!tid || !threads.some((t) => t.id === tid)) return
      const apiThreadId = serverThreadIdForApi(tid)
      if (!apiThreadId) return

      const sessionId = crypto.randomUUID()
      const insertIndex = anchorIndex + 1
      insertThreadMessageAfter(tid, anchorIndex, {
        role: 'assistant',
        kind: 'adhoc_expert',
        content: '',
        provider_id: providerId,
        model_used: '',
        provider_used: '',
        cost_usd: 0,
        adhoc_session_id: sessionId,
        expert_status: opinionMode === 'panel' ? 'Panel discussion' : 'Expert consult',
        message_type: opinionMode === 'panel' ? 'panel' : 'expert_consult',
      })

      setLoading(true)
      try {
        const headers = await buildAppHeaders()
        for await (const event of postAdhocExpertStream({
          threadId: apiThreadId,
          sessionId,
          providerId,
          tier,
          anchorMessageId,
          opinionMode,
          headers,
        })) {
          if (event.type === 'chunk') {
            setThreads((prev) =>
              prev.map((t) => {
                if (t.id !== tid) return t
                const messages = [...t.messages]
                const current = messages[insertIndex]
                if (!current) return t
                messages[insertIndex] = {
                  ...current,
                  content: `${current.content || ''}${event.content || ''}`,
                }
                return { ...t, messages }
              })
            )
          } else if (event.type === 'done') {
            updateThreadMessageAt(tid, insertIndex, {
              content: event.response ?? '',
              model_used: event.model_used ?? '',
              provider_id: event.provider_id ?? providerId,
              provider_used: event.provider_used ?? '',
              cost_usd: event.cost_usd ?? 0,
              kind: 'adhoc_expert',
              sqlite_message_id: event.sqlite_message_id ?? null,
              message_type: event.message_type ?? (opinionMode === 'panel' ? 'panel' : 'expert_consult'),
              expert_status: null,
            })
          } else if (event.type === 'error') {
            updateThreadMessageAt(tid, insertIndex, {
              kind: 'api_error',
              content: event.message || 'Expert opinion failed.',
              expert_status: null,
            })
          }
        }
      } catch (err) {
        updateThreadMessageAt(tid, insertIndex, {
          kind: 'api_error',
          content: err?.message || 'Expert opinion failed.',
          expert_status: null,
        })
      } finally {
        setLoading(false)
      }
    },
    [
      activeId,
      buildAppHeaders,
      insertThreadMessageAfter,
      loading,
      threads,
      tier,
      updateThreadMessageAt,
    ]
  )

  const handleWorkspaceFileAttach = useCallback(
    async (file) => {
      if (!persistentReady || !file || fileUploading || loading) return
      let tid = activeId
      if (!tid || !threads.some((x) => x.id === tid)) tid = newThread()

      if (!activeProjectId) {
        appendThreadMessages(tid, [
          {
            role: 'assistant',
            kind: 'api_error',
            content: 'Select an active workspace before attaching a file.',
            model_used: '',
            cost_usd: 0,
          },
        ])
        return
      }

      const chatId = serverThreadIdForApi(tid) || tid
      const localId = workspaceFileInventory.beginUpload(file)
      appendThreadMessages(tid, [
        {
          role: 'user',
          kind: 'file_upload',
          content: `Uploading: ${file.name}`,
          file_name: file.name,
          local_upload_id: localId,
          workspace_id: activeProjectId,
        },
      ])
      setFileUploading(true)
      try {
        const { result } = await workspaceFileInventory.uploadFile(file, {
          sourceChatId: chatId,
          localId,
        })
        const status = result?.status || 'uploaded'
        const failed = status === 'failed'
        appendThreadMessages(tid, [
          {
            role: 'assistant',
            kind: failed ? 'api_error' : 'file_library',
            content: failed
              ? `Upload failed: ${result?.failure_message || result?.failure_code || 'processing error'}`
              : `Saved to Workspace Files: ${result?.display_name || file.name}`,
            file_id: result?.id,
            file_name: result?.display_name || file.name,
            file_status: status,
            processing_stage: result?.processing_stage,
            job_status: result?.job_status,
            extraction_status: result?.extraction_status,
            index_status: result?.index_status,
            failure_message: result?.failure_message,
            workspace_id: result?.workspace_id || activeProjectId,
            model_used: '',
            cost_usd: 0,
          },
        ])
      } catch (e) {
        const parsed = parseBenErrorResponse(e.status, e.data)
        appendThreadMessages(tid, [
          {
            role: 'assistant',
            kind: 'api_error',
            content: parsed?.message || e.message || 'File upload failed.',
            model_used: '',
            cost_usd: 0,
          },
        ])
      } finally {
        setFileUploading(false)
      }
    },
    [
      activeId,
      activeProjectId,
      appendThreadMessages,
      buildAppHeaders,
      fileUploading,
      loading,
      newThread,
      persistentReady,
      threads,
    ]
  )

  const handleReceiptFile = useCallback(
    async (file, { creditMemo = false } = {}) => {
      if (!file || receiptCapturing || loading) return
      let tid = activeId
      if (!tid || !threads.some((x) => x.id === tid)) tid = newThread()

      if (!activeProjectId) {
        appendThreadMessages(tid, [
          {
            role: 'assistant',
            kind: 'api_error',
            content: 'Select an active project in the composer before capturing a receipt.',
            model_used: '',
            cost_usd: 0,
          },
        ])
        return
      }

      const previewUrl = URL.createObjectURL(file)
      appendThreadMessages(tid, [
        {
          role: 'user',
          kind: 'receipt_upload',
          content: `${creditMemo ? 'Credit memo' : 'Invoice'}: ${file.name}`,
          preview_url: previewUrl,
        },
      ])
      setReceiptCapturing(true)
      setLoading(true)
      try {
        const headers = await buildAppHeaders()
        const captureFn = creditMemo ? captureCreditMemo : captureInvoice
        const result = await captureFn(
          activeProjectId,
          {
            filename: file.name,
            file_path: file.name,
            image_url: previewUrl.startsWith('blob:') ? undefined : previewUrl,
          },
          headers
        )
        const ms = result.mutated_state || {}
        appendThreadMessages(tid, [
          {
            role: 'assistant',
            kind: 'action_card',
            card_type: ms.card_type || (creditMemo ? 'credit_memo' : 'receipt_capture'),
            action_payload: ms.payload || result,
            preview_url: previewUrl,
            content: '',
            model_used: '',
            cost_usd: 0,
          },
        ])
        setOrgBanner(null)
      } catch (e) {
        const parsed = parseBenErrorResponse(e.status, e.data)
        if (parsed?.code === CLERK_ORG_REQUIRED) {
          setOrgBanner({ message: parsed.message, hint: parsed.hint })
        }
        appendThreadMessages(tid, [
          {
            role: 'assistant',
            kind: 'api_error',
            content: parsed?.message || e.message || 'Document capture failed.',
            model_used: '',
            cost_usd: 0,
          },
        ])
      } finally {
        setReceiptCapturing(false)
        setLoading(false)
      }
    },
    [
      activeId,
      activeProjectId,
      appendThreadMessages,
      getToken,
      loading,
      newThread,
      receiptCapturing,
      threads,
    ]
  )

  const handleProcurementAction = useCallback(
    async (action) => {
      if (!activeProjectId || !getToken) return
      let tid = activeId
      if (!tid || !threads.some((x) => x.id === tid)) tid = newThread()
      setLoading(true)
      try {
        const headers = await buildAppHeaders()
        const result = await executeNativeTool(
          activeProjectId,
          'analyze_supplier_tender',
          {
            action: action.action,
            tender_id: action.tender_id,
            counter_offer_nis: action.counter_offer_nis,
            materials: action.materials,
            supplier_name: action.supplier_name,
          },
          headers
        )
        const ms = result.mutated_state || {}
        appendThreadMessages(tid, [
          {
            role: 'assistant',
            kind: 'action_card',
            card_type: ms.card_type || 'cost_engineering_bid_tabulation',
            action_payload: ms.payload || result,
            content: '',
            model_used: '',
            cost_usd: 0,
          },
        ])
      } catch (e) {
        const parsed = parseBenErrorResponse(e.status, e.data)
        appendThreadMessages(tid, [
          {
            role: 'assistant',
            kind: 'api_error',
            content: parsed?.message || e.message || 'Procurement action failed.',
            model_used: '',
            cost_usd: 0,
          },
        ])
      } finally {
        setLoading(false)
      }
    },
    [activeId, activeProjectId, appendThreadMessages, getToken, newThread, threads]
  )

  const handleAttendanceAction = useCallback(
    async (action) => {
      if (!activeProjectId || !getToken) return
      let tid = activeId
      if (!tid || !threads.some((x) => x.id === tid)) tid = newThread()
      setLoading(true)
      try {
        const headers = await buildAppHeaders()
        const result = await executeNativeTool(
          activeProjectId,
          'process_worker_response',
          {
            worker_name: action.worker_name,
            approve: action.approve || false,
            time_card_id: action.time_card_id,
            adjusted_hours: action.adjusted_hours,
            response_text: action.response_text,
          },
          headers
        )
        const ms = result.mutated_state || {}
        appendThreadMessages(tid, [
          {
            role: 'assistant',
            kind: 'action_card',
            card_type: ms.card_type || 'daily_attendance_approval',
            action_payload: ms.payload || result,
            content: '',
            model_used: '',
            cost_usd: 0,
          },
        ])
      } catch (e) {
        const parsed = parseBenErrorResponse(e.status, e.data)
        appendThreadMessages(tid, [
          {
            role: 'assistant',
            kind: 'api_error',
            content: parsed?.message || e.message || 'Attendance action failed.',
            model_used: '',
            cost_usd: 0,
          },
        ])
      } finally {
        setLoading(false)
      }
    },
    [activeId, activeProjectId, appendThreadMessages, getToken, newThread, threads]
  )

  const handleTrainingAction = useCallback(
    async (action) => {
      if (!activeProjectId || !getToken) return
      let tid = activeId
      if (!tid || !threads.some((x) => x.id === tid)) tid = newThread()
      setLoading(true)
      try {
        const headers = await buildAppHeaders()
        const result = await executeNativeTool(
          activeProjectId,
          'simulate_training_day_roi',
          {
            action: action.action || 'schedule_proctor_session',
            scheduled_date: action.scheduled_date,
            engineering_scope: action.engineering_scope,
            onsite_proctor_day_nis: action.onsite_proctor_day_nis,
          },
          headers
        )
        const ms = result.mutated_state || {}
        appendThreadMessages(tid, [
          {
            role: 'assistant',
            kind: 'action_card',
            card_type: ms.card_type || 'onsite_proctor_session',
            action_payload: ms.payload || result,
            content: '',
            model_used: '',
            cost_usd: 0,
          },
        ])
      } catch (e) {
        const parsed = parseBenErrorResponse(e.status, e.data)
        appendThreadMessages(tid, [
          {
            role: 'assistant',
            kind: 'api_error',
            content: parsed?.message || e.message || 'Training action failed.',
            model_used: '',
            cost_usd: 0,
          },
        ])
      } finally {
        setLoading(false)
      }
    },
    [activeId, activeProjectId, appendThreadMessages, getToken, newThread, threads]
  )

  const handleBasaltAction = useCallback(
    async (action) => {
      if (!activeProjectId || !getToken) return
      let tid = activeId
      if (!tid || !threads.some((x) => x.id === tid)) tid = newThread()
      setLoading(true)
      try {
        const headers = await buildAppHeaders()
        const result = await executeNativeTool(
          activeProjectId,
          'review_basalt_application',
          {
            action: action.action,
            application_id: action.application_id,
            scheduled_date: action.scheduled_date,
          },
          headers
        )
        const ms = result.mutated_state || {}
        appendThreadMessages(tid, [
          {
            role: 'assistant',
            kind: 'action_card',
            card_type: ms.card_type || 'basalt_web_application',
            action_payload: ms.payload || result,
            content: '',
            model_used: '',
            cost_usd: 0,
          },
        ])
      } catch (e) {
        const parsed = parseBenErrorResponse(e.status, e.data)
        appendThreadMessages(tid, [
          {
            role: 'assistant',
            kind: 'api_error',
            content: parsed?.message || e.message || 'Basalt application action failed.',
            model_used: '',
            cost_usd: 0,
          },
        ])
      } finally {
        setLoading(false)
      }
    },
    [activeId, activeProjectId, appendThreadMessages, getToken, newThread, threads]
  )

  const tierSelectOptions = useMemo(
    () => [
      { value: 'free', label: 'free' },
      { value: 'pro', label: 'pro' },
    ],
    []
  )

  const runProjectSetupBootstrap = useCallback(
    async (threadId) => {
      setLoading(true)
      setToolTelemetry('⚙️ System: Initializing project workspace agent...')
      let serverTid = threadId
      try {
        const headers = await buildAppHeaders()
        setThreads((prev) =>
          prev.map((t) =>
            t.id === threadId
              ? {
                  ...t,
                  messages: [
                    ...t.messages,
                    {
                      role: 'assistant',
                      content: '',
                      model_used: '',
                      provider_id: activeSpeakingProviderId,
                      provider_used: '',
                      cost_usd: 0,
                    },
                  ],
                  loaded: true,
                }
              : t
          )
        )
        let streamOk = false
        for await (const event of postChatStream({
          message: ' ',
          threadId,
          projectSetupBootstrap: true,
          tier,
          providerId: activeSpeakingProviderId,
          modelOverride: activeModelOverride,
          headers,
        })) {
          if (event.type === 'meta' && event.thread_id) serverTid = event.thread_id
          else if (event.type === 'tool_active') {
            setToolTelemetry(event.message || `⚙️ System: Running ${event.tool || 'tool'}...`)
          } else if (event.type === 'tool_done') setToolTelemetry(null)
          else if (event.type === 'chunk') {
            setToolTelemetry(null)
            streamOk = true
            const chunk = event.content ?? ''
            setThreads((prev) =>
              prev.map((t) => {
                if (t.id !== threadId && t.id !== serverTid) return t
                const msgs = [...t.messages]
                const last = msgs[msgs.length - 1]
                if (last?.role === 'assistant') {
                  msgs[msgs.length - 1] = { ...last, content: `${last.content || ''}${chunk}` }
                }
                return { ...t, id: serverTid, messages: msgs, loaded: true, sessionType: 'project_setup' }
              })
            )
          } else if (event.type === 'done') {
            streamOk = true
            serverTid = event.thread_id || serverTid
            setThreads((prev) =>
              prev.map((t) => {
                if (t.id !== threadId && t.id !== serverTid) return t
                const msgs = [...t.messages]
                const last = msgs[msgs.length - 1]
                if (last?.role === 'assistant') {
                  msgs[msgs.length - 1] = {
                    ...last,
                    content: event.response ?? last.content ?? '',
                    model_used: event.model_used ?? '',
                    provider_id: event.provider_id ?? activeSpeakingProviderId,
                    provider_used: event.provider_used ?? '',
                    cost_usd: event.cost_usd ?? 0,
                    sqlite_message_id: event.sqlite_assistant_id ?? last.sqlite_message_id ?? null,
                  }
                }
                return { ...t, id: serverTid, messages: msgs, loaded: true, sessionType: 'project_setup' }
              })
            )
            setActiveId(serverTid)
            setStoredActiveThreadId(serverTid)
          } else if (event.type === 'error') {
            throw new Error(event.message || 'Project setup failed.')
          }
        }
        if (!streamOk) throw new Error('Project setup returned no content.')
      } catch (e) {
        const parsed = parseBenErrorResponse(e.status, e.data)
        const msg = parsed?.message || humanizeChatFetchError(e)
        setThreads((prev) =>
          prev.map((t) =>
            t.id === threadId || t.id === serverTid
              ? {
                  ...t,
                  messages: [
                    ...t.messages.filter(
                      (m, i, arr) => !(i === arr.length - 1 && m.role === 'assistant' && !m.content)
                    ),
                    { role: 'assistant', kind: 'api_error', content: msg, model_used: '', cost_usd: 0 },
                  ],
                }
              : t
          )
        )
      } finally {
        setLoading(false)
        setToolTelemetry(null)
      }
    },
    [activeModelOverride, activeSpeakingProviderId, buildAppHeaders, tier]
  )

  const startProjectWorkspace = useCallback(
    async (workspaceContext = {}) => {
      const boundSlug = normalizeProjectSlug(workspaceContext.projectSlug) || null
      const projectTitle = String(workspaceContext.projectTitle || '').trim() || null
      const schemaBlueprint = Array.isArray(workspaceContext.schemaBlueprint)
        ? workspaceContext.schemaBlueprint
        : []
      const projectId = String(workspaceContext.projectId || '').trim() || null
      const tablesCreated =
        Number(workspaceContext.tablesCreated) || schemaBlueprint.length || 0

      try {
        const headers = await buildAppHeaders()
        const data = await createProjectWorkspace(headers, {
          projectSlug: boundSlug,
          title: projectTitle,
        })
        const thread = data.thread || {}
        const projectSlug = normalizeProjectSlug(thread.project_slug || boundSlug)
        const entry = {
          id: thread.id,
          title: thread.title || projectTitle || 'New Project Workspace',
          messages: [],
          loaded: true,
          sessionType: thread.session_type || 'project_setup',
          projectSlug,
          schemaBlueprint,
          projectId,
          isDraft: false,
        }
        setThreads((prev) => [entry, ...prev.filter((t) => t.id !== entry.id)])
        setActiveId(entry.id)
        setStoredActiveThreadId(entry.id)

        if (projectId) {
          setActiveProjectId(projectId)
          setProjectOptions((prev) => {
            if (prev.some((project) => project.id === projectId)) return prev
            return [
              { id: projectId, name: projectTitle || projectSlug || 'New project' },
              ...prev,
            ]
          })
        }

        closeNavDrawerIfOverlay()
        if (tablesCreated > 0) {
          setProjectToast(
            `Workspace ready — ${tablesCreated} JIT table${tablesCreated === 1 ? '' : 's'} provisioned.`
          )
        } else {
          setProjectToast('Project workspace ready — BEN is preparing your onboarding interview.')
        }
        window.setTimeout(() => setProjectToast(null), 4500)
        await runProjectSetupBootstrap(entry.id)
      } catch (e) {
        const parsed = parseBenErrorResponse(e.status, e.data)
        setProjectToast(parsed?.message || e.message || 'Could not start project workspace.')
        window.setTimeout(() => setProjectToast(null), 5000)
      }
    },
    [buildAppHeaders, closeNavDrawerIfOverlay, runProjectSetupBootstrap]
  )

  const handleNewProjectSubmit = useCallback(
    async (formValues) => {
      if (!canCreateProject) return
      setCreatingProject(true)
      setNewProjectError(null)
      try {
        const headers = await buildAppHeaders()
        const payload = buildConversationalInitPayload(formValues)
        const initResponse = await conversationalProjectInit(payload, headers)
        const init = parseConversationalInitResponse(initResponse)
        setNewProjectModalOpen(false)
        await startProjectWorkspace({
          projectSlug: init.projectSlug,
          projectTitle: init.projectName,
          schemaBlueprint: init.schemaBlueprint,
          projectId: init.projectId,
          tablesCreated: init.tablesCreated,
        })
      } catch (e) {
        const parsed = parseBenErrorResponse(e.status, e.data)
        setNewProjectError(parsed?.message || e.message || 'Could not create project.')
      } finally {
        setCreatingProject(false)
      }
    },
    [buildAppHeaders, canCreateProject, startProjectWorkspace]
  )

  const handleCertCapture = useCallback(
    ({ application_id, file, candidate_name }) => {
      if (!file) return
      let tid = activeId
      if (!tid || !threads.some((x) => x.id === tid)) tid = newThread()
      const preview = URL.createObjectURL(file)
      appendThreadMessages(tid, [
        {
          role: 'user',
          kind: 'receipt_upload',
          content: `Certification captured — ${candidate_name || 'candidate'} (${application_id?.slice(0, 8) || 'app'})`,
          preview_url: preview,
        },
      ])
    },
    [activeId, appendThreadMessages, newThread, threads]
  )

  return (
    <div className={`app app--gemini${navDrawerOpen ? ' app--nav-open' : ''}`}>
      <NewProjectModal
        open={newProjectModalOpen}
        onClose={() => {
          if (creatingProject) return
          setNewProjectModalOpen(false)
          setNewProjectError(null)
        }}
        onSubmit={handleNewProjectSubmit}
        submitting={creatingProject}
        error={newProjectError || createProjectReason}
        canSubmit={canCreateProject}
      />
      <DiscoveryCenterOverlay
        open={catalogOpen}
        onClose={() => setCatalogOpen(false)}
        buildHeaders={persistentReady ? persistentHeaders : null}
        disabled={loading || !persistentReady}
        featureState={platformFeatures}
        onFeaturesChange={handleWorkspaceFeaturesChange}
      />
      <NewsOverlay
        open={Boolean(newsRoute)}
        route={newsRoute}
        onClose={closeNews}
        onOpenFeed={openNewsFeed}
        onOpenTopic={openNewsTopic}
        buildHeaders={buildAppHeaders}
        disabled={loading}
      />
      <FileLibraryOverlay
        open={filesOpen}
        onClose={closeFilesLibrary}
        workspaceId={activeProjectId}
        workspaceName={activeProjectName}
        buildHeaders={persistentReady ? persistentHeaders : null}
        disabled={fileUploading || !persistentReady}
      />
      <ProjectLibraryOverlay
        open={projectsOpen}
        onClose={closeProjectsLibrary}
        activeProjectId={activeProjectId}
        activeProjectName={activeProjectName}
        buildHeaders={persistentReady ? persistentHeaders : null}
        disabled={loading || !persistentReady}
        canCreateProject={canCreateProject}
        onNewProject={() => {
          setProjectsOpen(false)
          setNewProjectError(null)
          setNewProjectModalOpen(true)
        }}
        onOpenProject={handleOpenProject}
      />
      <AppTopBar
        menuButtonRef={navMenuButtonRef}
        menuOpen={navDrawerOpen}
        onMenuClick={() => {
          closeSettings()
          setNavDrawerOpen((open) => !open)
        }}
        settingsButtonRef={settingsButtonRef}
        settingsOpen={settingsOpen}
        onSettingsClick={() => {
          closeNavDrawerIfOverlay()
          setSettingsOpen((open) => !open)
        }}
        onSettingsClose={closeSettings}
        settingsPanelRef={settingsPanelRef}
        authControls={HAS_CLERK_UI ? <ClerkAuthControls /> : null}
        shellAuth={
          showClerkSignIn ? <ClerkAuthControls variant="shell" /> : null
        }
      />

      <div className="app-layout">
        <NavDrawer
          ref={navDrawerRef}
          open={navDrawerOpen}
          overlay={isOverlayNav}
          onClose={closeNavDrawer}
        >
          <section className="nav-drawer__section">
            <h2 className="nav-drawer__section-title">Actions</h2>
            <div className="nav-drawer__actions">
              <button
                type="button"
                className="new-btn new-btn--compact"
                onClick={() => {
                  newThread()
                  closeNavDrawerIfOverlay()
                }}
              >
                <span className="new-btn__label new-btn__label--long">+ New chat</span>
              </button>
              {canCreateProject ? (
                <button
                  type="button"
                  className="new-btn new-btn--compact new-btn--project"
                  onClick={() => {
                    closeNavDrawerIfOverlay()
                    setNewProjectError(null)
                    setNewProjectModalOpen(true)
                  }}
                >
                  <span className="new-btn__label new-btn__label--long">+ New project</span>
                </button>
              ) : null}
            </div>
            <ProjectLibraryNavTrigger
              onOpen={openProjectsLibrary}
              active={projectsOpen}
              disabled={loading || !persistentReady}
            />
            <FileLibraryNavTrigger
              onOpen={openFilesLibrary}
              active={filesOpen}
              disabled={loading || !persistentReady}
            />
            <KnowledgeBasesPanel
              embedded
              buildHeaders={persistentReady ? persistentHeaders : null}
              disabled={loading || !persistentReady}
            />
            <KnowledgeSidebar
              projectSlug={active?.projectSlug || null}
              workspaceId={activeProjectId}
              buildHeaders={persistentReady ? persistentHeaders : null}
              disabled={loading || fileUploading || !persistentReady}
              attentionFocusRequest={attentionFocusRequest}
              onOpenFileLibrary={openFilesLibrary}
            />
            <CapabilityCatalogTrigger
              onOpen={() => {
                closeNavDrawerIfOverlay()
                setCatalogOpen(true)
              }}
              disabled={loading || !persistentReady}
            />
            <NewsNavTrigger
              onOpen={openNewsFeed}
              active={Boolean(newsRoute)}
              disabled={loading}
            />
            <ProjectRepositoriesDashboard
              projectSlug={active?.projectSlug || null}
              buildHeaders={persistentReady ? persistentHeaders : null}
              disabled={loading || !persistentReady}
            />
          </section>

          <NavDrawerHistory
            threads={threads}
            activeProjectSlug={active?.projectSlug || null}
            activeId={activeId}
            onSelectThread={selectThread}
            onBulkDelete={handleBulkDeleteThreads}
            disabled={loading || deletingThread}
          />
        </NavDrawer>

        <main className="main main--copilot main--focused">
        <ProjectSuccessToast message={projectToast} visible={Boolean(projectToast)} />
        <OrgRecoveryBanner banner={orgBanner} onDismiss={() => setOrgBanner(null)} />
        {showClerkSignIn ? <ClerkSignInBanner /> : null}
        <div className="messages" ref={messagesScrollRef}>
          <div className="chat-centered-channel">
          <ChatHeader
            title={active?.title}
            sessionType={active?.sessionType || 'chat'}
            canPromote={Boolean(activeId && isPersistedThreadId(activeId))}
            visible={Boolean(active) && !hydrating}
            deleting={deletingThread}
            promoting={promotingThread}
            onDelete={handleDeleteActiveConversation}
            onPromote={handlePromoteActiveConversation}
          />
          <SystemTelemetryBadge message={toolTelemetry} active={Boolean(toolTelemetry)} />
          {hydrating && persistentReady && threads.length === 0 ? (
            <div className="hydrate-hint">Loading conversations…</div>
          ) : null}
          {(active?.messages ?? []).map((m, i) => {
            const chatMeta =
              m.role === 'assistant' &&
              m.kind !== 'council_error' &&
              m.kind !== 'api_error' &&
              m.kind !== 'council_synthesis' &&
              m.kind !== 'adhoc_synthesis' &&
              m.kind !== 'action_card'
                ? formatChatAssistantMeta(m)
                : ''
            const providerAccent =
              m.kind === 'adhoc_expert'
                ? getSpeakingProviderById(m.provider_id)?.accent
                : undefined
            const messageDir = getMessageTextDirection(messageTextForDirection(m))
            const messageDirClass = messageDir === 'rtl' ? 'bubble-wrap--rtl' : 'bubble-wrap--ltr'
            return (
            <div
              key={i}
              dir={messageDir}
              className={`bubble-wrap ${m.role} ${messageDirClass}${m.kind === 'council_synthesis' || m.kind === 'adhoc_synthesis' ? ' synthesis-wrap' : ''}${m.kind === 'action_card' ? ' action-card-wrap' : ''}`}
            >
              <div
                className="bubble-stack"
                style={{
                  maxWidth: m.kind === 'action_card' ? 'min(440px, 94%)' : 'min(72ch, 92%)',
                  width: 'fit-content',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: messageDir === 'rtl' ? 'flex-end' : 'flex-start',
                }}
              >
              {m.kind === 'action_card' ? (
                <ActionCard
                  cardType={m.card_type}
                  payload={m.action_payload}
                  previewUrl={m.preview_url}
                  onAttendanceAction={handleAttendanceAction}
                  onProcurementAction={handleProcurementAction}
                  onTrainingAction={handleTrainingAction}
                  onBasaltAction={handleBasaltAction}
                  onCertCapture={handleCertCapture}
                />
              ) : m.kind === 'receipt_upload' ? (
                <div className="bubble user receipt-upload-bubble" dir={messageDir}>
                  <div className="bubble-text">{m.content}</div>
                  {m.preview_url ? (
                    <div className="receipt-upload-preview">
                      <img src={m.preview_url} alt="Uploaded invoice" loading="lazy" />
                    </div>
                  ) : null}
                </div>
              ) : m.kind === 'file_upload' || m.kind === 'file_library' ? (
                <FileLifecycleBubble message={m} />
              ) : m.kind === 'council_synthesis' || m.kind === 'adhoc_synthesis' ? (
                <div className="bubble synthesis assistant" dir={messageDir}>
                  <ChatMarkdown content={synthesisBubbleContent(m)} />
                  {m.synthesis ? (
                    <SynthesisReasoningExtras
                      synthesis={m.synthesis}
                      synthesisPipeline={
                        m.kind === 'adhoc_synthesis'
                          ? m.synthesis_pipeline || m.synthesis?.synthesis_pipeline
                          : undefined
                      }
                      outputLocale={
                        m.kind === 'adhoc_synthesis'
                          ? m.output_locale || m.synthesis?.output_locale
                          : undefined
                      }
                    />
                  ) : null}
                  {(m.model_used || m.cost_usd !== undefined) && (
                    <div className="meta">
                      {m.model_used && <span>{m.model_used}</span>}
                      {m.model_used && <span className="dot">·</span>}
                      <span>${Number(m.cost_usd).toFixed(6)}</span>
                    </div>
                  )}
                </div>
              ) : (
                <div
                  className={`bubble ${m.role}${m.kind === 'council_error' ? ' council-error' : ''}${m.kind === 'api_error' ? ' api-error' : ''}${m.kind === 'adhoc_expert' ? ' adhoc-expert' : ''}`}
                  dir={messageDir}
                  style={
                    providerAccent
                      ? {
                          borderInlineStart: `3px solid ${providerAccent}`,
                          paddingInlineStart: '0.65rem',
                        }
                      : undefined
                  }
                >
                  {shouldRenderAssistantMarkdown(m) ? (
                    <ChatMarkdown content={m.content} />
                  ) : (
                    <div className="bubble-text">{m.content}</div>
                  )}
                  {isStandardChatAssistant(m) && m.used_files?.length ? (
                    <div className="used-files">
                      <div className="used-files__label">Used files:</div>
                      <ul className="used-files__list">
                        {m.used_files.map((file) => (
                          <li key={file.id}>{file.name}</li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  {isStandardChatAssistant(m) && m.workspace_files_unavailable_note ? (
                    <p className="used-files__unavailable">{m.workspace_files_unavailable_note}</p>
                  ) : null}
                  {m.role === 'assistant' &&
                    m.kind !== 'council_error' &&
                    m.kind !== 'api_error' &&
                    m.kind !== 'council_synthesis' &&
                    m.kind !== 'adhoc_synthesis' &&
                    (chatMeta || m.model_used || m.cost_usd !== undefined || m.expert_status) && (
                    <div className="meta">
                      {m.expert_status && <span className="expert-status">{m.expert_status}</span>}
                      {m.expert_status && (chatMeta || m.model_used) && <span className="dot">·</span>}
                      <span>{chatMeta || m.model_used || 'Assistant'}</span>
                      {(chatMeta || m.model_used) && <span className="dot">·</span>}
                      <span>${Number(m.cost_usd).toFixed(6)}</span>
                    </div>
                  )}
                </div>
              )}
              {m.kind !== 'action_card' && (m.role === 'user' || m.role === 'assistant') ? (
                <MessageActionBar
                  role={m.role}
                  content={m.content}
                  onEditRequest={m.role === 'user' ? handleEditRequest : undefined}
                  sqliteMessageId={resolveSqliteMessageId(m)}
                  expertDisabled={loading || !isPersistedThreadId(serverThreadIdForApi(activeId) || '')}
                  onExpertOpinion={(payload) =>
                    void handleExpertOpinion({
                      ...payload,
                      anchorIndex: i,
                      anchorMessageId: resolveSqliteMessageId(m),
                    })
                  }
                />
              ) : null}
              </div>
            </div>
            )
          })}
          <div ref={messagesEndRef} className="messages-scroll-anchor" aria-hidden="true" />
          </div>
        </div>
        <footer className="composer-footer" ref={composerFooterRef}>
          <div className="chat-centered-channel">
          {(active?.messages?.length ?? 0) >= 2 ? (
            <CopyConversationButton messages={active.messages} />
          ) : null}
          <div className="composer-shell" style={{ '--shell-accent': shellAccent }}>
            <ComposerCapsule
              value={input}
              onChange={setInput}
              onSubmit={handleComposerSubmit}
              placeholder={composerPlaceholder}
              ariaLabel={composerAriaLabel}
              disabled={loading || !persistentReady}
              canSend={canSendComposer}
              loading={loading}
              sendLabel="Send"
              shellAccent={shellAccent}
              attachMenuItems={attachMenuItems}
              attachMenuHidden={
                <>
                  <input
                    ref={attachFileRef}
                    type="file"
                    accept=".pdf,.docx,.doc,.txt,.md,.markdown,.csv,.xlsx,.pptx,.png,.jpg,.jpeg,.gif,.webp,.json,application/pdf,text/plain,text/markdown,text/csv,image/*"
                    className="receipt-file-input"
                    tabIndex={-1}
                    aria-hidden="true"
                    disabled={loading || receiptCapturing || fileUploading || !persistentReady}
                    onChange={(event) => {
                      const file = event.target.files?.[0] || null
                      event.target.value = ''
                      if (file) void handleWorkspaceFileAttach(file)
                    }}
                  />
                  <CameraCaptureInput
                    ref={invoiceCaptureRef}
                    disabled={loading || receiptCapturing || fileUploading || !persistentReady}
                    triggerClassName="receipt-capture-btn receipt-capture-btn--capsule"
                    onFile={(file) => void handleReceiptFile(file, { creditMemo: false })}
                    className="hw-capture-wrap--composer"
                  >
                    🧾
                  </CameraCaptureInput>
                  <CameraCaptureInput
                    ref={creditCaptureRef}
                    disabled={loading || receiptCapturing || fileUploading || !persistentReady}
                    mode="credit"
                    triggerClassName="receipt-capture-btn receipt-capture-btn--capsule"
                    onFile={(file) => void handleReceiptFile(file, { creditMemo: true })}
                    className="hw-capture-wrap--composer"
                  >
                    ↩
                  </CameraCaptureInput>
                </>
              }
              engineSettings={{
                activeProviderId: activeSpeakingProviderId,
                onProviderChange: handleEngineSelect,
                providerModels,
                onProviderModelChange: handleProviderModelChange,
                tier,
                onTierChange: setTier,
                tierOptions: tierSelectOptions,
                disabled: loading,
                activeCatalogKeys: liveCatalogKeys,
                gateProviders: false,
              }}
            />
          </div>
          </div>
        </footer>
        </main>
      </div>
    </div>
  )
}

export default App
