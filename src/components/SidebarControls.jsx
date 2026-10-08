import { SidebarIcon, SearchIcon, PlusIcon } from './Icons'

export function SidebarControls({ collapsed, onToggle, onSearch, onNewChat, mobile = false }) {
  return (
    <div className="sidebar-controls" role="group" aria-label="Chat controls">
      <button
        type="button"
        className="ctrl-btn has-tip"
        data-tip={collapsed ? 'Open sidebar' : 'Close sidebar'}
        aria-label={collapsed ? 'Open sidebar' : 'Close sidebar'}
        aria-expanded={!collapsed}
        onClick={onToggle}
      >
        <SidebarIcon />
      </button>
      {collapsed && (
        <>
          {!mobile && (
            <button
              type="button"
              className="ctrl-btn has-tip"
              data-tip="Search"
              aria-label="Search chats"
              onClick={onSearch}
            >
              <SearchIcon />
            </button>
          )}
          <button
            type="button"
            className="ctrl-btn has-tip"
            data-tip="New chat"
            aria-label="New chat"
            onClick={onNewChat}
          >
            <PlusIcon />
          </button>
        </>
      )}
    </div>
  )
}
