import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeftIcon,
  ChevronIcon,
  CloseIcon,
  CollapseIcon,
  DotsIcon,
  DownloadIcon,
  ExpandIcon,
  FileIcon,
  PencilIcon,
  RefreshIcon,
  WindowIcon,
} from './Icons'
import { assemblePreview, hasFiles, pickEntryFile } from '../lib/webpagePreview'
import { highlightCode } from '../lib/highlightCode'
import { getWebpageAssetDataUrl, getWebpageVersionDiff } from '../api/webpageClient'

// One download: the same assembled document the preview shows, so a saved page
// works on its own (linked styles/scripts and folder pictures are inlined).
function downloadPage(files, assetUrls) {
  const html = assemblePreview(files, assetUrls)
  if (!html) return
  const blob = new Blob([html], { type: 'text/html' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'cappuccina-webpage.html'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// Assets never change once added, so their data URLs are fetched once per
// session and kept here (a page's pictures are a handful, and this survives
// switching iterations and tabs).
const assetUrlCache = new Map()

function cachedAssetUrl(chatId, path) {
  return assetUrlCache.get(`${chatId}:${path}`)
}

async function loadAssetUrl(chatId, path) {
  const key = `${chatId}:${path}`
  if (assetUrlCache.has(key)) return assetUrlCache.get(key)
  const url = await getWebpageAssetDataUrl(chatId, path)
  assetUrlCache.set(key, url)
  return url
}

// The page's text files first (the entry file on top), then the folder's
// pictures, alphabetically — the order the canvas has always used for files.
function fileList(files, assetNames) {
  const entry = pickEntryFile(files)
  return [...Object.keys(files), ...assetNames].sort((a, b) => {
    const aAsset = assetNames.includes(a)
    const bAsset = assetNames.includes(b)
    if (aAsset !== bAsset) return aAsset ? 1 : -1
    if (a === entry) return -1
    if (b === entry) return 1
    return a.localeCompare(b)
  })
}

// How a folder picture reads in the file list: "812 KB".
function assetSize(info) {
  const size = info?.size ?? 0
  if (size <= 0) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

// "AI run · 2 files · +8 −1" (or the edit's own label): what one iteration did.
function historyLabel(version) {
  if (!version) return ''
  const name = version.origin === 'manual' ? version.label || 'Manual edit' : 'AI run'
  const summary = version.summary ?? {}
  return `${name} · ${summary.files ?? 0} file(s) · +${summary.added ?? 0} −${summary.removed ?? 0}`
}

// Timestamps read better as a clock time for today and a date for older runs.
function iterationWhen(iso) {
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return ''
  const today = new Date()
  const sameDay =
    when.getFullYear() === today.getFullYear() &&
    when.getMonth() === today.getMonth() &&
    when.getDate() === today.getDate()
  return new Intl.DateTimeFormat(
    undefined,
    sameDay ? { hour: '2-digit', minute: '2-digit' } : { month: 'short', day: 'numeric' },
  ).format(when)
}

function historyMeta(version) {
  const when = iterationWhen(version?.createdAt)
  return when ? `${historyLabel(version)} · ${when}` : historyLabel(version)
}

export function WebpageCanvas({
  chatId,
  files,
  assets,
  activeVersionId,
  versions,
  streaming,
  busy,
  notice,
  onDismissNotice,
  onActivateVersion,
  onSaveFile,
  onClose,
}) {
  const [tab, setTab] = useState('preview')
  const [activeFile, setActiveFile] = useState(null)
  const [menuOpen, setMenuOpen] = useState(true)
  const [revision, setRevision] = useState(0)
  const [historyOpen, setHistoryOpen] = useState(false)
  // The canvas normally shares the window with the chat; fullscreen makes it
  // cover the whole browser window instead (whatever size that window is).
  const [fullscreen, setFullscreen] = useState(false)
  // { path, text, baseVersionId }: the draft keeps the iteration it was started
  // from, so the save is refused (409) rather than overwriting a newer one.
  const [draft, setDraft] = useState(null)
  const [diff, setDiff] = useState({ files: [], truncated: false })
  const [diffFile, setDiffFile] = useState(null)
  const [diffLoading, setDiffLoading] = useState(false)
  // {path: dataUrl} for the pictures the page folder shows, plus the ones still
  // loading (the preview redraws as each arrives).
  const [assetUrls, setAssetUrls] = useState({})
  const highlightRef = useRef(null)
  const editRef = useRef(null)

  const index = versions.findIndex((v) => v.id === activeVersionId)
  const current = index >= 0 ? versions[index] : null
  // The folder holds the pictures the active iteration had; without an
  // iteration list (a page built before this change) it falls back to all of
  // the environment's assets.
  const assetNames = useMemo(() => {
    const known = Object.keys(assets ?? {})
    const shown = current ? current.assets ?? [] : known
    return shown.filter((name) => known.includes(name)).sort()
  }, [assets, current])
  const names = useMemo(() => fileList(files ?? {}, assetNames), [files, assetNames])
  const shownFile = activeFile && names.includes(activeFile) ? activeFile : names[0] ?? null
  // A picture (or other binary) has no text to show or edit: the Code tab
  // renders it, and the pencil stays away.
  const shownAsset = Boolean(shownFile && assetNames.includes(shownFile))
  const shownCode = files?.[shownFile] ?? ''
  const preview = useMemo(
    () => assemblePreview(files ?? {}, assetUrls),
    [files, assetUrls],
  )
  const highlighted = useMemo(() => highlightCode(shownCode, shownFile), [shownCode, shownFile])
  // The same highlighting for the text being edited, rendered behind the
  // textarea. A trailing newline is padded so the pre keeps exactly as many
  // lines as the textarea (a trailing newline is dropped when rendering HTML,
  // which would make the two layers scroll out of step at the end of the file).
  const draftHighlight = useMemo(() => {
    if (!draft) return ''
    const html = highlightCode(draft.text, draft.path)
    return draft.text.endsWith('\n') ? `${html}\n` : html
  }, [draft])

  // Only the textarea scrolls by itself (the highlight layer sits behind it with
  // hidden overflow), so it drives the pair.
  const syncEditScroll = () => {
    const pre = highlightRef.current
    const area = editRef.current
    if (!pre || !area) return
    pre.scrollTop = area.scrollTop
    pre.scrollLeft = area.scrollLeft
  }

  // Switching mid-run would race the agent's own writes; the server refuses it
  // too, so the controls go quiet instead of throwing errors at the user.
  const canSwitch = !streaming && !busy && versions.length > 0
  // Nothing to show means the picker is the only useful view.
  useEffect(() => {
    if (!shownFile) setMenuOpen(true)
  }, [shownFile])

  // Mid-run nothing can be switched, so the list closes instead of sitting
  // there with every row unclickable.
  useEffect(() => {
    if (!canSwitch) setHistoryOpen(false)
  }, [canSwitch])

  // The draft belongs to one file: changing file or tab discards it (a
  // successful save swaps the snapshot and clears it explicitly).
  useEffect(() => {
    setDraft(null)
  }, [shownFile, tab])

  // Pressing the pencil should leave a usable editor: focus it with the caret
  // at the end of the text, so typing appends instead of nothing happening.
  useEffect(() => {
    const area = editRef.current
    if (!area) return
    area.focus()
    area.setSelectionRange(area.value.length, area.value.length)
  }, [draft?.path])

  // Escape leaves fullscreen. While a draft is open Escape belongs to the
  // editor (it cancels the edit), so fullscreen stays put.
  useEffect(() => {
    if (!fullscreen) return
    const onKeyDown = (e) => {
      if (e.key === 'Escape' && !draft) setFullscreen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [fullscreen, draft])

  // The diff is fetched when the Changes tab is shown and refetched whenever
  // the active iteration moves (activation, a new run, a manual save).
  useEffect(() => {
    if (tab !== 'changes' || !activeVersionId || !chatId) return
    let cancelled = false
    setDiffLoading(true)
    getWebpageVersionDiff(chatId, activeVersionId)
      .then((data) => {
        if (cancelled) return
        const list = data.files ?? []
        setDiff({ files: list, truncated: Boolean(data.truncated) })
        setDiffFile(list[0]?.path ?? null)
      })
      .catch(() => {
        if (!cancelled) setDiff({ files: [], truncated: false })
      })
      .finally(() => {
        if (!cancelled) setDiffLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [tab, activeVersionId, chatId])

  // The pictures the active iteration shows are fetched once (the module cache
  // keeps them for the session) and inlined into the preview and the Code tab.
  useEffect(() => {
    if (!chatId || assetNames.length === 0) return
    let cancelled = false
    Promise.all(
      assetNames.map((name) =>
        loadAssetUrl(chatId, name)
          .then((url) => [name, url])
          .catch(() => [name, null]),
      ),
    ).then((entries) => {
      if (cancelled) return
      setAssetUrls(Object.fromEntries(entries.filter(([, url]) => url)))
    })
    return () => {
      cancelled = true
    }
  }, [chatId, assetNames])

  const refresh = () => setRevision((r) => r + 1)
  const pick = (name) => {
    setActiveFile(name)
    setMenuOpen(false)
  }

  const startEdit = () => setDraft({ path: shownFile, text: shownCode, baseVersionId: activeVersionId })

  const saveDraft = async () => {
    if (!draft) return
    const ok = await onSaveFile(draft.path, draft.text, draft.baseVersionId)
    if (ok) setDraft(null)
  }

  const onEditKeyDown = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 's') {
      e.preventDefault()
      saveDraft()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setDraft(null)
    }
  }

  const change = diff.files.find((f) => f.path === diffFile) ?? diff.files[0] ?? null
  const shellClass = fullscreen ? 'webpage-canvas canvas-fullscreen' : 'webpage-canvas'

  if (!hasFiles(files)) {
    return (
      <aside className={shellClass} aria-label="Webpage canvas">
        <header className="canvas-head">
          <span className="canvas-title">
            <WindowIcon /> Webpage
          </span>
          <button type="button" className="canvas-icon-btn" aria-label="Close canvas" onClick={onClose}>
            <CloseIcon />
          </button>
        </header>
        <div className="canvas-empty">
          <WindowIcon />
          <p>Nothing here yet.</p>
          <p className="canvas-empty-hint">Ask Cappuccina to build you a webpage!</p>
        </div>
      </aside>
    )
  }

  return (
    <aside className={shellClass} aria-label="Webpage canvas">
      <header className="canvas-head">
        <span className="canvas-title">
          <WindowIcon /> Webpage
          {streaming && <span className="canvas-heading-dots" aria-hidden="true"><i /><i /><i /></span>}
        </span>
        <div className="canvas-actions">
          <div className="canvas-tabs" role="tablist" aria-label="Canvas view">
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'preview'}
              className={tab === 'preview' ? 'active' : ''}
              onClick={() => setTab('preview')}
            >
              Preview
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'code'}
              className={tab === 'code' ? 'active' : ''}
              onClick={() => setTab('code')}
            >
              Code
            </button>
            {versions.length > 0 && (
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'changes'}
                className={tab === 'changes' ? 'active' : ''}
                onClick={() => {
                  setHistoryOpen(false)
                  setTab('changes')
                }}
              >
                Changes
              </button>
            )}
          </div>
          {/* The icon cluster is one unit, so on a narrow canvas the whole row
              moves above the tabs rather than splitting around them. */}
          <div className="canvas-icons">
            <button
              type="button"
              className="canvas-icon-btn has-tip"
              data-tip={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
              aria-label={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
              aria-pressed={fullscreen}
              onClick={() => setFullscreen((on) => !on)}
            >
              {fullscreen ? <CollapseIcon /> : <ExpandIcon />}
            </button>
            <button type="button" className="canvas-icon-btn has-tip" data-tip="Refresh" aria-label="Refresh preview" onClick={refresh}>
              <RefreshIcon />
            </button>
            <button type="button" className="canvas-icon-btn has-tip" data-tip="Download" aria-label="Download the page" onClick={() => downloadPage(files, assetUrls)}>
              <DownloadIcon />
            </button>
            <button type="button" className="canvas-icon-btn has-tip" data-tip="Close" aria-label="Close canvas" onClick={onClose}>
              <CloseIcon />
            </button>
          </div>
        </div>
      </header>

      {versions.length > 0 && (
        <div className="canvas-history" role="group" aria-label="Iterations">
          <button
            type="button"
            className="canvas-history-step"
            aria-label="Previous iteration"
            disabled={!canSwitch || index <= 0}
            onClick={() => onActivateVersion(versions[index - 1].id)}
          >
            <ChevronIcon />
          </button>
          <div className="canvas-history-label">
            <span className="canvas-history-count">
              Iteration {index + 1} of {versions.length}
            </span>
            <span className="canvas-history-meta">{historyMeta(current)}</span>
          </div>
          <button
            type="button"
            className="canvas-history-step"
            aria-label="Next iteration"
            disabled={!canSwitch || index < 0 || index >= versions.length - 1}
            onClick={() => onActivateVersion(versions[index + 1].id)}
          >
            <ChevronIcon />
          </button>
          <button
            type="button"
            className="canvas-history-btn"
            aria-expanded={historyOpen}
            aria-label="All iterations"
            disabled={!canSwitch}
            onClick={() => setHistoryOpen((open) => !open)}
          >
            <DotsIcon />
          </button>
          {historyOpen && (
            <div className="canvas-history-panel" role="listbox" aria-label="Iterations">
              {[...versions].reverse().map((v) => (
                <button
                  key={v.id}
                  type="button"
                  role="option"
                  aria-selected={v.id === activeVersionId}
                  className={`canvas-file-row${v.id === activeVersionId ? ' active' : ''}`}
                  disabled={!canSwitch}
                  onClick={() => {
                    setHistoryOpen(false)
                    onActivateVersion(v.id)
                  }}
                >
                  <span className="canvas-history-seq">#{v.seq}</span>
                  <span className="canvas-file-name">{historyLabel(v)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {notice && (
        <div className="canvas-notice" role="status">
          {notice}
          <button type="button" className="canvas-icon-btn" aria-label="Dismiss" onClick={onDismissNotice}>
            <CloseIcon />
          </button>
        </div>
      )}

      {tab === 'preview' ? (
        <div className="canvas-stage">
          <iframe
            key={revision}
            className="canvas-frame"
            title="Webpage preview"
            sandbox="allow-scripts"
            srcDoc={preview}
          />
        </div>
      ) : tab === 'changes' ? (
        <div className="canvas-code">
          {diffLoading ? (
            <div className="canvas-empty">
              <p>Loading changes…</p>
            </div>
          ) : diff.files.length === 0 ? (
            <div className="canvas-empty">
              <p>No changes yet.</p>
              <p className="canvas-empty-hint">This iteration is the first version of the page.</p>
            </div>
          ) : (
            <>
              <div className="canvas-change-list" role="listbox" aria-label="Changed files">
                {diff.files.map((f) => (
                  <button
                    key={f.path}
                    type="button"
                    role="option"
                    aria-selected={f.path === change?.path}
                    className={`canvas-file-row${f.path === change?.path ? ' active' : ''}`}
                    onClick={() => setDiffFile(f.path)}
                  >
                    <FileIcon />
                    <span className="canvas-file-name">{f.path}</span>
                    <span className="canvas-history-meta">
                      +{f.added} −{f.removed}
                    </span>
                  </button>
                ))}
              </div>
              <pre className="canvas-code-body canvas-diff">
                {(change?.lines ?? []).map((line, i) => (
                  <span key={i} className={`diff-line diff-${line.kind}`}>
                    {line.kind === 'add' ? '+ ' : line.kind === 'del' ? '- ' : line.kind === 'hunk' ? '' : '  '}
                    {line.text}
                  </span>
                ))}
              </pre>
              {diff.truncated && (
                <p className="canvas-empty-hint canvas-diff-note">
                  Diff truncated — open a single file to see the rest.
                </p>
              )}
            </>
          )}
        </div>
      ) : (
        <div className="canvas-code">
          {menuOpen ? (
            <div className="canvas-file-menu">
              <div className="canvas-file-menu-head">Files</div>
              <div className="canvas-file-list" role="listbox" aria-label="Files">
                {names.map((name) => (
                  <button
                    key={name}
                    type="button"
                    role="option"
                    aria-selected={name === shownFile}
                    className={`canvas-file-row${name === shownFile ? ' active' : ''}`}
                    onClick={() => pick(name)}
                  >
                    <FileIcon />
                    <span className="canvas-file-name">{name}</span>
                    {assetNames.includes(name) && (
                      <span className="canvas-history-meta">{assetSize(assets?.[name])}</span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              <div className="canvas-code-head">
                <button
                  type="button"
                  className="canvas-back-btn"
                  aria-label="Back to files"
                  onClick={() => setMenuOpen(true)}
                >
                  <ArrowLeftIcon />
                </button>
                <FileIcon />
                <span className="canvas-file-name">{shownFile}</span>
                {!shownAsset && (
                  <button
                    type="button"
                    className="canvas-icon-btn canvas-edit-btn"
                    aria-label={`Edit ${shownFile}`}
                    disabled={streaming || busy || !onSaveFile}
                    onClick={startEdit}
                  >
                    <PencilIcon />
                  </button>
                )}
              </div>
              {shownAsset ? (
                // A picture in the folder: show it, with its size. There is no
                // text to edit — the page references it by name.
                <div className="canvas-asset">
                  {assetUrls[shownFile] ? (
                    <img className="canvas-asset-preview" src={assetUrls[shownFile]} alt={shownFile} />
                  ) : (
                    <p className="canvas-empty-hint">Loading {shownFile}…</p>
                  )}
                  <p className="canvas-empty-hint">
                    {shownFile}
                    {assetSize(assets?.[shownFile]) ? ` · ${assetSize(assets?.[shownFile])}` : ''}
                    {assets?.[shownFile]?.mime ? ` · ${assets[shownFile].mime}` : ''}
                  </p>
                </div>
              ) : draft ? (
                <>
                  {/* The highlighted copy stays in place behind a transparent
                      textarea, so the text keeps its colors while editing. */}
                  <div className="canvas-code-edit-wrap">
                    <pre
                      className="canvas-code-body canvas-code-highlight"
                      aria-hidden="true"
                      ref={highlightRef}
                      dangerouslySetInnerHTML={{ __html: draftHighlight }}
                    />
                    <textarea
                      className="canvas-code-body canvas-code-edit"
                      aria-label={`Edit ${draft.path}`}
                      spellCheck={false}
                      wrap="off"
                      ref={editRef}
                      value={draft.text}
                      onChange={(e) => setDraft({ ...draft, text: e.target.value })}
                      onKeyDown={onEditKeyDown}
                      onScroll={syncEditScroll}
                    />
                  </div>
                  <div className="canvas-edit-bar">
                    <span className="canvas-edit-hint">Editing {draft.path}</span>
                    <button type="button" className="canvas-edit-cancel" onClick={() => setDraft(null)}>
                      Cancel
                    </button>
                    <button
                      type="button"
                      className="canvas-edit-save"
                      disabled={busy || draft.text === shownCode}
                      onClick={saveDraft}
                    >
                      {busy ? 'Saving…' : 'Save'}
                    </button>
                  </div>
                </>
              ) : (
                <pre
                  className="canvas-code-body"
                  dangerouslySetInnerHTML={{ __html: highlighted }}
                />
              )}
            </>
          )}
        </div>
      )}
    </aside>
  )
}
