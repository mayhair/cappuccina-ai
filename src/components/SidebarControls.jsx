import { SidebarIcon, SearchIcon, PlusIcon } from './Icons'
import { useLanguage } from '../i18n/LanguageProvider'

export function SidebarControls({ collapsed, onToggle, onSearch, onNewChat, mobile = false }) {
  const { t } = useLanguage()
  return (
    <div className="sidebar-controls" role="group" aria-label={t('controls.group')}>
      <button
        type="button"
        className="ctrl-btn has-tip"
        data-tip={collapsed ? t('controls.openSidebar') : t('sidebar.close')}
        aria-label={collapsed ? t('controls.openSidebar') : t('sidebar.close')}
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
              data-tip={t('controls.search')}
              aria-label={t('sidebar.search')}
              onClick={onSearch}
            >
              <SearchIcon />
            </button>
          )}
          <button
            type="button"
            className="ctrl-btn has-tip"
            data-tip={t('sidebar.newChat')}
            aria-label={t('sidebar.newChat')}
            onClick={onNewChat}
          >
            <PlusIcon />
          </button>
        </>
      )}
    </div>
  )
}
