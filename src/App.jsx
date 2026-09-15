import { useEffect, useState } from 'react'
import { Sidebar } from './components/Sidebar'
import { ChatWindow } from './components/ChatWindow'
import { ThemeToggle } from './components/ThemeToggle'
import { SparkleIcon } from './components/Icons'
import { useChatController } from './hooks/useChatController'


export default function App() {
  const [theme, setTheme] = useState(() => {
    const saved = localStorage.getItem('cappuccina-theme')
    if (saved === 'light' || saved === 'dark') return saved
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('cappuccina-theme', theme)
  }, [theme])

  const {
    chats,
    activeChat,
    activeChatId,
    draft,
    setDraft,
    query,
    setQuery,
    effort,
    setEffort,
    isStreaming,
    inputRef,
    typingTitleId,
    sendMessage,
    retryMessage,
    newChat,
    deleteChat,
    renameChat,
    clearTypingTitle,
    selectChat,
  } = useChatController()

  return (
    <div className="app">
      <div className="sky" aria-hidden="true">
        <img src="/plates/sky-band.png" alt="" style={{ opacity: theme === 'light' ? 1 : 0 }} />
        <img src="/plates/sky-dark.png" alt="" className="dark" style={{ opacity: theme === 'dark' ? 1 : 0 }} />
      </div>

      <Sidebar
        chats={chats}
        activeChatId={activeChatId}
        onSelect={selectChat}
        onNewChat={newChat}
        onDelete={deleteChat}
        onRename={renameChat}
        query={query}
        onQuery={setQuery}
        typingTitleId={typingTitleId}
        onTitleTyped={clearTypingTitle}
      />

      <main className="main">
        <header className="top-bar">
          <ThemeToggle theme={theme} onChange={setTheme} />
          <span className="ai-badge">
            <SparkleIcon />
            I'm an AI
          </span>
        </header>

        <ChatWindow
          chat={activeChat}
          isStreaming={isStreaming}
          draft={draft}
          onDraft={setDraft}
          onSend={sendMessage}
          onRetry={retryMessage}
          inputRef={inputRef}
          effort={effort}
          onEffort={setEffort}
        />
      </main>
    </div>
  )
}
