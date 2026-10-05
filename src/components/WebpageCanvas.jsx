import { useEffect, useMemo, useState } from 'react'
import { ArrowLeftIcon, CloseIcon, DownloadIcon, FileIcon, RefreshIcon, WindowIcon } from './Icons'
import { assemblePreview, hasFiles, pickEntryFile } from '../lib/webpagePreview'
import { highlightCode } from '../lib/highlightCode'

function fileList(files) {
  return Object.keys(files).sort((a, b) => {
    const entry = pickEntryFile(files)
    if (a === entry) return -1
    if (b === entry) return 1
    return a.localeCompare(b)
  })
}

// One download: the same assembled document the preview shows, so a saved page
// works on its own (linked styles/scripts are inlined).
function downloadPage(files) {
  const html = assemblePreview(files)
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

export function WebpageCanvas({ files, streaming, onClose }) {
  const [tab, setTab] = useState('preview')
  const [activeFile, setActiveFile] = useState(null)
  const [menuOpen, setMenuOpen] = useState(true)
  const [revision, setRevision] = useState(0)

  const names = useMemo(() => fileList(files ?? {}), [files])
  const preview = useMemo(() => assemblePreview(files ?? {}), [files])
  const shownFile = activeFile && activeFile in files ? activeFile : names[0] ?? null
  const shownCode = files?.[shownFile] ?? ''
  const highlighted = useMemo(() => highlightCode(shownCode, shownFile), [shownCode, shownFile])

  // Nothing to show means the picker is the only useful view.
  useEffect(() => {
    if (!shownFile) setMenuOpen(true)
  }, [shownFile])

  const refresh = () => setRevision((r) => r + 1)
  const pick = (name) => {
    setActiveFile(name)
    setMenuOpen(false)
  }

  if (!hasFiles(files)) {
    return (
      <aside className="webpage-canvas" aria-label="Webpage canvas">
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
    <aside className="webpage-canvas" aria-label="Webpage canvas">
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
          </div>
          <button type="button" className="canvas-icon-btn has-tip" data-tip="Refresh" aria-label="Refresh preview" onClick={refresh}>
            <RefreshIcon />
          </button>
          <button type="button" className="canvas-icon-btn has-tip" data-tip="Download" aria-label="Download the page" onClick={() => downloadPage(files)}>
            <DownloadIcon />
          </button>
          <button type="button" className="canvas-icon-btn has-tip" data-tip="Close" aria-label="Close canvas" onClick={onClose}>
            <CloseIcon />
          </button>
        </div>
      </header>

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
              </div>
              <pre
                className="canvas-code-body"
                dangerouslySetInnerHTML={{ __html: highlighted }}
              />
            </>
          )}
        </div>
      )}
    </aside>
  )
}
