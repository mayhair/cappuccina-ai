import { useEffect, useRef, useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { SidebarControls } from './components/SidebarControls'
import { TopBarTitle } from './components/TopBarTitle'
import { ChatWindow } from './components/ChatWindow'
import { WebpageCanvas } from './components/WebpageCanvas'
import { TopBarMenu } from './components/TopBarMenu'
import { WindowIcon } from './components/Icons'
import { Sky, LandingPage } from './components/LandingPage'
import { AlphaGate } from './components/AlphaGate'
import { LanguageGate } from './components/LanguageGate'
import { SettingsDialog } from './components/SettingsDialog'
import { getMe, logout } from './api/authClient'
import { useChatController } from './hooks/useChatController'
import { useMediaQuery } from './hooks/useMediaQuery'
import tts from './lib/tts'
import { setManifestEnabled } from './lib/pwa'
import { availableModes, effectiveMode } from './lib/modes'
import { useLanguage } from './i18n/LanguageProvider'
import {
  navigate,
  useLocation,
  parseRoute,
  currentRoute,
  buildUrl,
  saveReturnTo,
  takeReturnTo,
} from './lib/router'

// Voice replies are paused: Fish Audio may train on the text it receives,
// which can include Gmail and Calendar details. The backend refuses TTS too
// (TTS_ENABLED in backend/app/config.py). Flip both to bring them back.
const VOICE_REPLIES_AVAILABLE = false

// The tab title of the landing page and of a new chat (index.html's <title>).
const BASE_TITLE = document.title
const TITLE_SUFFIX = 'Ballerina Cappuccina'

export default function App() {
  const { t } = useLanguage()
  // The URL decides the chat, the canvas and the Settings section.
  const route = parseRoute(useLocation())
  const settingsOpen = route.settings != null
  // The stored preference: 'light', 'dark' or 'system' (follow the OS).
  const [themePref, setThemePref] = useState(() => {
    const saved = localStorage.getItem('cappuccina-theme')
    return saved === 'light' || saved === 'dark' ? saved : 'system'
  })
  const systemDark = useMediaQuery('(prefers-color-scheme: dark)')
  const theme = themePref === 'system' ? (systemDark ? 'dark' : 'light') : themePref

  useEffect(() => {
    localStorage.setItem('cappuccina-theme', themePref)
  }, [themePref])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    // Browser chrome (mobile address bar, installed-app title bar) follows the page background.
    const pageBg = getComputedStyle(document.documentElement).getPropertyValue('--page-bg').trim()
    if (pageBg) document.querySelector('meta[name="theme-color"]')?.setAttribute('content', pageBg)
  }, [theme])

  // Voice replies (TTS) are off until the user opts in. While
  // VOICE_REPLIES_AVAILABLE is false they stay off and the switches are hidden.
  const [voice, setVoice] = useState(
    () => VOICE_REPLIES_AVAILABLE && localStorage.getItem('cappuccina-voice') === '1',
  )
  const onVoice = VOICE_REPLIES_AVAILABLE ? setVoice : null

  useEffect(() => {
    localStorage.setItem('cappuccina-voice', voice ? '1' : '0')
    tts.setEnabled(voice)
  }, [voice])

  const isMobile = useMediaQuery('(max-width: 900px)')

  const [collapsed, setCollapsed] = useState(() => {
    const saved = localStorage.getItem('cappuccina-sidebar-collapsed')
    if (saved === '1') return true
    if (saved === '0') return false
    return window.matchMedia?.('(max-width: 900px)').matches ?? false
  })

  useEffect(() => {
    localStorage.setItem('cappuccina-sidebar-collapsed', collapsed ? '1' : '0')
  }, [collapsed])

  // Track the visual viewport so the composer stays above the phone keyboard.
  // Only the keyboard shrinks vv.height; pinch/browser zoom also shrinks
  // vv.width, so we ignore those (and desktop entirely) to avoid reflowing the
  // app on every zoom frame, which made the layout twist while zooming.
  useEffect(() => {
    const vv = window.visualViewport
    if (!vv) return
    const root = document.documentElement
    const update = () => {
      const zoomed = vv.width < root.clientWidth - 1
      if (window.innerWidth > 900 || zoomed) {
        root.style.removeProperty('--app-height')
        return
      }
      root.style.setProperty('--app-height', `${Math.round(vv.height)}px`)
    }
    update()
    vv.addEventListener('resize', update)
    window.addEventListener('resize', update)
    return () => {
      vv.removeEventListener('resize', update)
      window.removeEventListener('resize', update)
    }
  }, [])

  const searchInputRef = useRef(null)

  const toggleSidebar = () => setCollapsed((c) => !c)

  const openSidebarAndSearch = () => {
    setCollapsed(false)
    requestAnimationFrame(() => searchInputRef.current?.focus())
  }

  // On mobile the sidebar is a drawer: picking a chat or starting a new one
  // closes it so the chat gets the whole screen.
  const handleSelectChat = (id) => {
    navigate(buildUrl({ chatId: id }))
    if (isMobile) setCollapsed(true)
  }

  const handleNewChat = () => {
    // Already on a new chat: nothing to navigate to, just refocus the composer.
    if (route.chatId === null) inputRef.current?.focus()
    else navigate('/')
    if (isMobile) setCollapsed(true)
  }

  // The controller moves the URL itself when a chat gets its server id, when
  // the open chat is deleted and when a page appears. A pushed canvas entry is
  // flagged so closing the canvas can step back instead of stacking entries.
  const handleLocationChange = (url, { replace = false } = {}) => {
    const canvas = parseRoute(new URL(url, window.location.origin)).canvas
    navigate(url, { replace, state: !replace && canvas ? { canvasPushed: true } : undefined })
  }

  const openCanvasPage = () => {
    const now = currentRoute()
    if (!now.chatId || now.canvas) return
    navigate(buildUrl({ ...now, canvas: true }), { state: { canvasPushed: true } })
  }

  const closeCanvasPage = () => {
    const now = currentRoute()
    if (!now.canvas) return
    if (window.history.state?.canvasPushed) window.history.back()
    else navigate(buildUrl({ ...now, canvas: false }), { replace: true })
  }

  // Mobile opens Settings on its index; desktop on the first section.
  const openSettings = (section) => {
    const target = section ?? (window.matchMedia('(max-width: 768px)').matches ? '' : 'account')
    navigate(buildUrl({ ...currentRoute(), settings: target }), { state: { settingsPushed: true } })
  }

  const closeSettings = () => {
    if (window.history.state?.settingsPushed) window.history.back()
    else navigate(buildUrl({ ...currentRoute(), settings: null }), { replace: true })
  }

  const changeSettingsSection = (section) => {
    navigate(buildUrl({ ...currentRoute(), settings: section }), { replace: true })
  }

  useEffect(() => {
    if (!isMobile || collapsed) return
    const onKey = (e) => {
      if (e.key === 'Escape') setCollapsed(true)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isMobile, collapsed])

  // Swipe to open/close the drawer on mobile. Opening starts at the screen edge
  // the drawer lives on (so it never fights with scrolling content); closing
  // can start anywhere. Mirrored for right-to-left languages.
  useEffect(() => {
    if (!isMobile) return
    const EDGE = 32
    const MIN_DX = 60
    let start = null
    const onStart = (e) => {
      if (e.touches.length !== 1) { start = null; return }
      const { clientX: x, clientY: y } = e.touches[0]
      const rtl = document.documentElement.dir === 'rtl'
      const nearEdge = rtl ? window.innerWidth - x <= EDGE : x <= EDGE
      start = collapsed && !nearEdge ? null : { x, y, rtl }
    }
    const onEnd = (e) => {
      if (!start) return
      const { clientX: x, clientY: y } = e.changedTouches[0]
      const dx = (x - start.x) * (start.rtl ? -1 : 1) // positive = toward the content
      const dy = y - start.y
      start = null
      if (Math.abs(dx) < MIN_DX || Math.abs(dx) < Math.abs(dy) * 1.5) return
      if (collapsed && dx > 0) setCollapsed(false)
      else if (!collapsed && dx < 0) setCollapsed(true)
    }
    const cancel = () => { start = null }
    window.addEventListener('touchstart', onStart, { passive: true })
    window.addEventListener('touchend', onEnd, { passive: true })
    window.addEventListener('touchcancel', cancel, { passive: true })
    return () => {
      window.removeEventListener('touchstart', onStart)
      window.removeEventListener('touchend', onEnd)
      window.removeEventListener('touchcancel', cancel)
    }
  }, [isMobile, collapsed])

  const [user, setUser] = useState(null)
  const [authPhase, setAuthPhase] = useState('checking') // checking | landing
  const [authError, setAuthError] = useState('')
  const [gmailNotice, setGmailNotice] = useState('')
  const [calendarNotice, setCalendarNotice] = useState('')

  // OAuth redirects land back on "/" with a short status in the query string.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const error = params.get('auth_error')
    const gmail = params.get('gmail')
    const calendar = params.get('calendar')
    if (error) setAuthError(error)
    const gmailDone = gmail === 'error' || gmail === 'connected'
    const calendarDone = calendar === 'error' || calendar === 'connected'
    if (gmailDone) setGmailNotice(gmail)
    if (calendarDone) setCalendarNotice(calendar)
    if (error || gmail || calendar) {
      params.delete('auth_error')
      params.delete('gmail')
      params.delete('calendar')
      const qs = params.toString()
      window.history.replaceState({}, '', window.location.pathname + (qs ? `?${qs}` : ''))
    }
    // Back to the chat the connect button was pressed in, with the result
    // showing in Settings.
    if (gmailDone || calendarDone) {
      navigate(`${takeReturnTo() ?? '/'}?settings=connections`, { replace: true })
    }
  }, [])

  useEffect(() => {
    let canceled = false
    getMe()
      .then((me) => {
        if (canceled) return
        setUser(me)
        // Signed in again after the landing page bounced a deep link.
        const back = takeReturnTo()
        if (back) navigate(back, { replace: true })
      })
      .catch(() => {
        if (!canceled) setAuthPhase('landing')
      })
    return () => {
      canceled = true
    }
  }, [])

  // The landing page lives at "/" only. Keep a deep link so signing in lands
  // on it, rather than on a new chat.
  useEffect(() => {
    if (user || authPhase !== 'landing') return
    const { pathname, search } = window.location
    if (pathname === '/' && !search) return
    saveReturnTo(pathname + search)
    navigate('/', { replace: true })
  }, [user, authPhase])

  // The app is installable only for signed-in users, so the manifest link follows the session.
  useEffect(() => {
    setManifestEnabled(Boolean(user))
  }, [user])

  const chat = useChatController(Boolean(user), {
    onLocationChange: handleLocationChange,
    storyModeEnabled: user?.storyModeEnabled !== false,
  })
  const {
    chats,
    chatsLoaded,
    pristineChatId,
    hasChat,
    activeChat,
    activeChatId,
    draft,
    setDraft,
    query,
    setQuery,
    effort,
    setEffort,
    setMode,
    setStory,
    sendText,
    isStreaming,
    inputRef,
    typingTitleId,
    sendMessage,
    retryMessage,
    editMessage,
    regenerateMessage,
    navigateBranch,
    newChat,
    deleteChat,
    renameChat,
    clearTypingTitle,
    markDraftSent,
    markEventDeleted,
    markMemoryUndone,
    selectChat,
    attachments,
    uploading,
    attachError,
    addAttachments,
    removeAttachment,
    webpage,
    hasWebpage,
    canvasRevision,
    canvasOpen,
    canvasNotice,
    clearCanvasNotice,
    canvasBusy,
    activateVersion,
    saveFile,
    openCanvas,
    closeCanvas,
  } = chat

  // Make the controller match the URL. Idempotent: each branch only acts when
  // the controller disagrees with the route.
  useEffect(() => {
    if (!user) return
    if (route.chatId === null) {
      // "/" is a fresh new chat.
      if (!pristineChatId) newChat()
      else if (activeChatId !== pristineChatId) selectChat(pristineChatId)
      if (canvasOpen) closeCanvas()
      return
    }
    // Until the history arrives the chat area stays empty; never another chat.
    if (!chatsLoaded) return
    if (!hasChat(route.chatId)) {
      navigate('/', { replace: true })
      return
    }
    if (activeChatId !== route.chatId) selectChat(route.chatId)
    if (canvasOpen !== route.canvas) {
      if (route.canvas) openCanvas()
      else closeCanvas()
    }
  }, [
    user,
    route.chatId,
    route.canvas,
    chatsLoaded,
    pristineChatId,
    activeChatId,
    canvasOpen,
    hasChat,
    newChat,
    selectChat,
    openCanvas,
    closeCanvas,
  ])

  // The tab names the open chat.
  const tabTitle = route.chatId && activeChat && !activeChat.pending && activeChat.title !== 'New chat'
    ? activeChat.title
    : ''
  useEffect(() => {
    document.title = user && tabTitle ? `${tabTitle} — ${TITLE_SUFFIX}` : BASE_TITLE
  }, [user, tabTitle])

  // The result notices belong to one visit to Settings.
  const wasSettingsOpen = useRef(false)
  useEffect(() => {
    if (wasSettingsOpen.current && !settingsOpen) {
      setGmailNotice('')
      setCalendarNotice('')
    }
    wasSettingsOpen.current = settingsOpen
  }, [settingsOpen])

  const handleLogout = async () => {
    tts.stop()
    await logout()
    navigate('/', { replace: true })
    setUser(null)
    setAuthPhase('landing')
  }

  // Same exit as signing out; the server already cleared the session cookie.
  const handleAccountDeleted = () => {
    tts.stop()
    navigate('/', { replace: true })
    setUser(null)
    setAuthPhase('landing')
  }

  if (!user) {
    if (authPhase === 'checking') {
      return (
        <div className="landing">
          <Sky theme={theme} />
        </div>
      )
    }
    return (
      <div className="landing">
        <Sky theme={theme} />
        <LandingPage theme={theme} onTheme={setThemePref} error={authError} />
        <LanguageGate />
        <AlphaGate />
      </div>
    )
  }

  return (
    <div className={`app${collapsed ? ' is-collapsed' : ''}${canvasOpen ? ' canvas-open' : ''}`}>
      <Sky theme={theme} />

      {isMobile && !collapsed && (
        <div
          className="sidebar-scrim"
          aria-hidden="true"
          onClick={() => setCollapsed(true)}
        />
      )}

      <Sidebar
        chats={chats}
        activeChatId={activeChatId}
        onSelect={handleSelectChat}
        onNewChat={handleNewChat}
        onDelete={deleteChat}
        onRename={renameChat}
        query={query}
        onQuery={setQuery}
        searchInputRef={searchInputRef}
        collapsed={collapsed}
        typingTitleId={typingTitleId}
        onTitleTyped={clearTypingTitle}
        user={user}
        onLogout={handleLogout}
        onSettings={() => {
          openSettings()
          if (isMobile) setCollapsed(true)
        }}
        onClose={() => setCollapsed(true)}
      />
      <SettingsDialog
        user={user}
        open={settingsOpen}
        initialSection={route.settings ?? ''}
        onSectionChange={changeSettingsSection}
        onClose={closeSettings}
        onUserUpdated={setUser}
        onAccountDeleted={handleAccountDeleted}
        gmailNotice={gmailNotice}
        onGmailNoticeClear={() => setGmailNotice('')}
        calendarNotice={calendarNotice}
        onCalendarNoticeClear={() => setCalendarNotice('')}
        theme={themePref}
        onTheme={setThemePref}
        voice={voice}
        onVoice={onVoice}
      />

      <main className="main">
        <header className="top-bar">
          {/* DOM order is title-first (it is the view's heading); CSS places it
              after the sidebar pill. */}
          <div className="top-bar-title">
            {activeChat && <TopBarTitle
              title={activeChat.pending ? '' : activeChat.title === 'New chat' ? t('sidebar.newChat') : activeChat.title}
              typing={activeChat.id === typingTitleId}
              onTyped={clearTypingTitle}
              onRename={(title) => renameChat(activeChat.id, title)}
            />}
          </div>
          <SidebarControls
            collapsed={collapsed}
            onToggle={toggleSidebar}
            onSearch={openSidebarAndSearch}
            onNewChat={handleNewChat}
            mobile={isMobile}
          />
          <div className="top-bar-right">
            {hasWebpage && (
              <button
                type="button"
                className={`ctrl-btn has-tip${canvasOpen ? ' is-on' : ''}`}
                data-tip={canvasOpen ? t('app.hideWebpage') : t('app.showWebpage')}
                aria-label={canvasOpen ? t('app.hideWebpage') : t('app.showWebpage')}
                aria-pressed={canvasOpen}
                onClick={canvasOpen ? closeCanvasPage : openCanvasPage}
              >
                <WindowIcon />
              </button>
            )}
            <TopBarMenu
              theme={themePref}
              onTheme={setThemePref}
              voice={voice}
              onVoice={onVoice}
              onSettings={() => openSettings()}
            />
          </div>
        </header>

        {activeChat && <ChatWindow
          chat={activeChat}
          user={user}
          isStreaming={isStreaming}
          draft={draft}
          onDraft={setDraft}
          onSend={sendMessage}
          onRetry={retryMessage}
          onEdit={editMessage}
          onRegenerate={regenerateMessage}
          onNavigateBranch={navigateBranch}
          onDraftSent={(draftId) => markDraftSent(activeChatId, draftId)}
          onEventDeleted={(eventId) => markEventDeleted(activeChatId, eventId)}
          onMemoryUndone={(toolName, memoryId) => markMemoryUndone(activeChatId, toolName, memoryId)}
          onManageMemory={() => openSettings('memory')}
          onManageCharacters={() => openSettings('story')}
          onOpenWebpage={openCanvasPage}
          inputRef={inputRef}
          effort={effort}
          onEffort={setEffort}
          mode={effectiveMode(activeChat.mode, user)}
          modes={availableModes(user)}
          onMode={(mode, subject) => setMode(activeChatId, mode, subject)}
          onSendText={sendText}
          onStory={(story) => setStory(activeChatId, story)}
          attachments={attachments}
          uploading={uploading}
          attachError={attachError}
          onAttach={addAttachments}
          onRemoveAttachment={removeAttachment}
        />}
      </main>

      {canvasOpen && (
        <WebpageCanvas
          chatId={webpage.chatId}
          files={webpage.files}
          previewUrl={webpage.previewUrl}
          previewBase={webpage.previewBase}
          activeVersionId={webpage.activeVersionId ?? null}
          versions={webpage.versions ?? []}
          hasBuild={Boolean(webpage.hasBuild)}
          lastBuild={webpage.lastBuild ?? null}
          streaming={isStreaming}
          busy={canvasBusy}
          notice={canvasNotice}
          revision={canvasRevision}
          onDismissNotice={clearCanvasNotice}
          onActivateVersion={(versionId) => activateVersion(webpage.chatId, versionId)}
          onSaveFile={(path, content, baseVersionId) =>
            saveFile(webpage.chatId, path, content, baseVersionId)
          }
          onAskFix={(text) => {
            setDraft(text)
            inputRef.current?.focus()
          }}
          onClose={closeCanvasPage}
        />
      )}

      <LanguageGate />
    </div>
  )
}
