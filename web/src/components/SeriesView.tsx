// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useMemo, useState } from 'react'
import { api, LinkState, Target } from '../api'
import { useStore } from '../store'
import { keyOf, resolveTarget } from '../seriesnav'
import { bumpSeries, MenuButton, MenuItem, useSeriesOps } from '../seriesops'
import { useBackLayer } from '../back'
import { BoardView } from './Board'
import { StripView } from './Strip'
import { FolderIcon, LinkIcon, SeriesTree } from './SeriesTree'
import { AddPhotosDialog } from './AddPhotos'

type Mode = 'board' | 'strip'
const BoardIcon = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="8" height="6" rx="1" /><rect x="13" y="4" width="8" height="10" rx="1" /><rect x="3" y="12" width="8" height="8" rx="1" /><rect x="13" y="16" width="8" height="4" rx="1" /></svg>
const StripIcon = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="5" width="5" height="5.5" rx="1" /><rect x="9.5" y="5" width="5" height="5.5" rx="1" /><rect x="16" y="5" width="5" height="5.5" rx="1" /><rect x="3" y="13.5" width="5" height="5.5" rx="1" /><rect x="9.5" y="13.5" width="5" height="5.5" rx="1" /><rect x="16" y="13.5" width="5" height="5.5" rx="1" /></svg>
function readMode(key: number): Mode {
  try { return localStorage.getItem('lpl.seriesmode.' + key) === 'strip' ? 'strip' : 'board' } catch { return 'board' }
}

const plural = (n: number, one: string, many = one + 's') => `${n} ${n === 1 ? one : many}`
const ORD = ['', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth', 'ninth', 'tenth']

/** "Link this board again": the board and the strip went apart while unlinked; which one wins, in plain words. */
function LinkDialog({ target, name, st, canUndo, onPick, onClose }: {
  target: Target; name: string; st: LinkState; canUndo: boolean; onPick: (mode: 'strip' | 'board') => void; onClose: () => void
}) {
  const s = useStore()
  useBackLayer(true, onClose)
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])
  const folder = target.kind === 'folder'
  const free = st.free_frames || [], loose = st.loose || [], missing = st.missing_frames || []
  const quote = (xs: string[]) => xs.map(x => `“${x}”`).join(', ')
  let intro: string, a: string, b: string
  if (folder) {
    const parts = [
      free.length || missing.length ? `the board has ${plural(st.frames, 'frame')}, the strip has ${st.series} series` : '',
      st.moves ? `${plural(st.moves, 'photo')} ${st.moves === 1 ? 'lies' : 'lie'} in another series' frame` : '',
      !free.length && !missing.length && !st.moves && st.reordered ? `the photos of ${plural(st.reordered!, 'series', 'series')} stand in another order on the board` : '',
    ].filter(Boolean)
    intro = `The board and the strip of ${name} no longer match: ${parts.join('; ') || 'they differ'}. Which one wins?`
    const fp = free.reduce((n, f) => n + f.photos, 0)
    a = 'One frame per series, photos placed in strip order.'
      + (free.length ? ` The frame${free.length > 1 ? 's' : ''} ${quote(free.map(f => f.label))} ${free.length > 1 ? 'are' : 'is'} removed${fp ? `; ${fp === 1 ? 'its photo goes' : `${free.length > 1 ? 'their' : 'its'} ${fp} photos go`} back to ${fp === 1 ? 'its series' : 'their series'}` : ''}.` : '')
      + (missing.length ? ` ${quote(missing)} get${missing.length === 1 ? 's' : ''} a frame.` : '')
      + (loose.length ? ` ${plural(loose.length, 'photo')} on the board ${loose.length === 1 ? 'is' : 'are'} in no series (${loose.slice(0, 4).map(p => p.filename).join(', ')}${loose.length > 4 ? ', …' : ''}): ${loose.length === 1 ? 'it stays' : 'they stay'} on the board, outside the frames.` : '')
    b = 'Each frame becomes a series, photos in board order.'
      + (free.length ? ` ${quote(free.map(f => f.label))} become${free.length === 1 ? `s a ${ORD[st.series!] || 'new'} series` : ' new series'} in ${name}.` : '')
      + (st.moves ? ` ${plural(st.moves, 'photo')} change${st.moves === 1 ? 's' : ''} series (their tags follow).` : '')
      + (missing.length ? ` ${quote(missing)} get${missing.length === 1 ? 's' : ''} a frame.` : '')
  } else {
    intro = `The board and the strip of ${name} no longer match: ${plural(st.differs || 0, 'photo')} ${st.differs === 1 ? 'stands' : 'stand'} in another place. Which one wins?`
    a = `The photos are placed into the frame${st.frames === 1 ? '' : 's'} in strip order${st.frames > 1 ? ' (each frame keeps its number of photos, the last one takes the rest)' : ''}.`
    b = 'The order is read from the board: frame by frame, row by row, left to right.' + (st.maybe ? ` ${plural(st.maybe, 'photo')} in “maybe” frames ${st.maybe === 1 ? 'has' : 'have'} no place in the order.` : '')
  }
  const strip = <span className="lk-ic"><StripIcon /></span>, board = <span className="lk-ic"><BoardIcon /></span>
  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className={(s.isMobile ? 'sheet' : 'dialog') + ' linkdlg'} role="dialog" aria-label="Link this board again">
        {s.isMobile && <span className="grab" />}
        <span className="display" style={{ fontSize: 19, fontWeight: 500 }}>Link this board again</span>
        <span className="soft" style={{ lineHeight: 1.5 }}>{intro}</span>
        <button type="button" className="lk-choice" onClick={() => onPick('strip')}>
          <span className="lk-how">{strip}<span className="lk-arrow">→</span>{board}</span>
          <span className="col" style={{ gap: 3 }}><b>Arrange the board from the strip</b><span className="small soft">{a}{canUndo ? ' Undo on the board brings the old layout back (unlinked again).' : ''}</span></span>
        </button>
        <button type="button" className="lk-choice" onClick={() => onPick('board')}>
          <span className="lk-how">{board}<span className="lk-arrow">→</span>{strip}</span>
          <span className="col" style={{ gap: 3 }}><b>Take the order from the board</b><span className="small soft">{b}</span></span>
        </button>
        <div className="row">
          <span className="small muted grow">After linking, board and strip change together.</span>
          <button type="button" className={'btn' + (s.isMobile ? ' big' : '')} onClick={onClose}>Keep it unlinked</button>
        </div>
      </div>
    </>
  )
}

/** The Series view: a folder of series or a single series, as Board or Strip, with the link between the two. */
export function SeriesView() {
  const s = useStore()
  const ops = useSeriesOps()
  const phone = s.isMobile
  const target = useMemo(() => resolveTarget(s.boardSeries, s.series, s.seriesFolders), [s.boardSeries, s.series, s.seriesFolders])
  const key = target ? keyOf(target) : 0
  const [mode, setModeRaw] = useState<Mode>(() => readMode(key))
  const [boardSeen, setBoardSeen] = useState(mode === 'board')  // the board is made when first shown and then kept (its undo history lives in it)
  const [adding, setAdding] = useState(false)
  const [dlg, setDlg] = useState<LinkState | null>(null)
  const [info, setInfo] = useState<{ hidden: number; maybe: number }>({ hidden: 0, maybe: 0 })
  const [busy, setBusy] = useState(false)
  useEffect(() => { const m = readMode(key); setModeRaw(m); setBoardSeen(m === 'board'); setAdding(false); setDlg(null); setInfo({ hidden: 0, maybe: 0 }) }, [key])
  const setMode = (m: Mode) => { setModeRaw(m); if (m === 'board') setBoardSeen(true); try { localStorage.setItem('lpl.seriesmode.' + key, m) } catch { /* ignore */ } }

  const folder = target?.kind === 'folder' ? s.seriesFolders.find(f => f.id === target.id) || null : null
  const series = target?.kind === 'series' ? s.series.find(x => x.id === target.id) || null : null
  const parent = series?.folder_id != null ? s.seriesFolders.find(f => f.id === series.folder_id) || null : null
  const name = folder?.name || series?.name || ''
  const linked = folder ? folder.linked : !!series?.linked
  const showHidden = !!s.filters.show_hidden

  const tree = !phone && (
    <nav className="series-list" aria-label="Series">
      <SeriesTree current={target} />
      <details className="st-help small muted">
        <summary>How the board works</summary>
        “+ Add photos” brings in whole folders or everything matching a rating, label, tag or date. Add a frame with the frame button on the left, double-click its name to rename it, drag it by its name bar or edges. Shift+1 shows everything, Shift+2 the selection, [ and ] jump between frames, and the frames button lists them all.
        <br /><br />Drag on empty space to select several things (Shift adds). Drag with the right or middle mouse button, or hold Space and drag, to move around; on a touch screen drag empty space with one finger and pinch with two to zoom. How the mouse, wheel and double-click behave is in Settings (⚙ at the top). Keys: V select, N note, A arrow, Delete takes what's selected off the board (change them with the keyboard button at the top). Double-click empty space for a note, an arrow to write on it.
      </details>
    </nav>
  )

  if (!target || (!folder && !series)) {
    return (
      <div className="board-wrap">
        {tree}
        <div className="center">
          <div className="col" style={{ padding: 40, alignItems: 'flex-start' }}>
            <span className="display" style={{ fontSize: 20 }}>No series yet</span>
            <span className="muted">Select photos in the Library, then use “Add to series… → new series”; or start an empty one here and add photos to it.</span>
            <div className="row">
              <button className={'btn primary' + (phone ? ' big' : '')} onClick={() => ops.newSeries(null, s.sel)}>New series</button>
              <button className={'btn' + (phone ? ' big' : '')} onClick={() => ops.newFolder()}>New folder</button>
            </div>
          </div>
        </div>
        {ops.node}
      </div>
    )
  }

  const changed = () => { s.reloadSeries(); s.reloadTags(); bumpSeries() }
  const toggleLink = async () => {
    if (busy || parent) return
    setBusy(true)
    try {
      if (linked) {
        await api.tlinkSet(target, false)
        s.toast(folder ? 'Board not linked: frames are only visual groups now, the strip keeps the order.' : 'Board not linked: the strip keeps the order now.')
        changed()
      } else {
        const st = await api.tlink(target)
        if (st.same) { await api.tlinkSet(target, true, 'board'); s.toast(`Board linked to ${name} again; nothing had changed.`); changed() }
        else setDlg(st)
      }
    } catch (e: any) { s.toast(e.message) }
    setBusy(false)
  }
  const relink = async (m: 'strip' | 'board') => {
    setDlg(null)
    try {
      const r = await api.tlinkSet(target, true, m)
      const bits = [
        m === 'strip' ? 'Board arranged from the strip.' : 'Order taken from the board.',
        r.removed_frames?.length ? `Removed: ${r.removed_frames.map(x => `“${x}”`).join(', ')}.` : '',
        r.created?.length ? `New series: ${r.created.map(x => `“${x}”`).join(', ')}.` : '',
        r.loose?.length ? `${plural(r.loose.length, 'photo')} in no series left outside the frames.` : '',
      ]
      s.toast(bits.filter(Boolean).join(' '))
      changed()
    } catch (e: any) { s.toast(e.message) }
  }
  const showInLibrary = () => {
    const here = s.sel
    if (here.length === 1) { s.reveal(here[0]); return }
    s.patchFilters({ series: series ? series.id : null, series_folder: folder ? folder.id : null, folder: null, folders: [], month: null, tag: null })
    s.setSort([{ f: 'custom', d: 'asc' }]); s.setView('grid')
  }
  const menu: MenuItem[] = [
    { label: s.sel.length === 1 ? "Show the selected photo's folder in the library" : `Show ${folder ? 'the folder' : 'the series'} in the library, in order`, onClick: showInLibrary },
    ...(folder ? [{ label: `New series in ${folder.name}`, onClick: () => { ops.newSeries(folder) } }] : []),
    { label: 'Rename', onClick: () => (folder ? ops.renameFolder(folder) : ops.renameSeries(series!)) },
    ...(series && parent ? [{ label: `Open the folder ${parent.name}`, onClick: () => ops.open({ kind: 'folder', id: parent.id }) }] : []),
    { label: folder ? 'Delete folder' : 'Delete', danger: true, onClick: () => { folder ? ops.deleteFolder(folder) : ops.deleteSeries(series!) } },
  ]
  const counts = folder ? `${folder.series_count} series · ${plural(folder.count, 'photo')}` : plural(series!.count, 'photo') + (info.maybe ? ` · ${info.maybe} in maybe piles` : '')
  const linkTitle = parent ? `Inside a folder the order of a series comes from the folder: ${parent.name}'s board${parent.linked ? '' : ' or strip'}. This board is free.`
    : `Link or unlink the board and the ${folder ? 'series of ' + name : 'order of ' + name}`
  const linkText = parent ? (phone ? `Order from ${parent.name}` : `Order follows ${parent.name}`) : linked ? (phone ? 'Board linked' : `Board linked to ${name}`) : phone ? 'Not linked' : 'Board not linked'
  const seg = (
    <div className="sv-seg" role="group" aria-label={`Show ${name} as`}>
      <button type="button" className={mode === 'board' ? 'on' : ''} aria-pressed={mode === 'board'} onClick={() => setMode('board')}><BoardIcon /><span>Board</span></button>
      <button type="button" className={mode === 'strip' ? 'on' : ''} aria-pressed={mode === 'strip'} onClick={() => setMode('strip')}><StripIcon /><span>Strip</span></button>
    </div>
  )
  const link = (
    <button type="button" role="switch" aria-checked={linked && !parent} className={'sv-link' + (linked && !parent ? ' on' : '')} disabled={!!parent || busy} title={linkTitle} onClick={toggleLink}>
      <span className="row" style={{ gap: 8 }}><LinkIcon off={!linked || !!parent} /><span>{linkText}</span></span>
      {!parent && <span className="sv-sw"><i /></span>}
    </button>
  )
  const hiddenChip = (info.hidden > 0 || showHidden) && (
    <button className={'chip' + (showHidden ? ' on' : '')} title="Photos you hid from the library. They stay in the series and keep their place." onClick={() => s.patchFilters({ show_hidden: !showHidden, only_hidden: false })}>
      {showHidden ? 'showing' : 'show'} {info.hidden} hidden
    </button>
  )
  const add = <button className="btn primary" onClick={() => setAdding(true)} title="Add photos from folders, or by rating, label, tag and date">+ Add photos</button>
  const more = <MenuButton label="More: show in library, rename, delete" items={menu} className="sv-more" />
  return (
    <div className="board-wrap">
      {tree}
      <div className="center">
        {phone ? (
          <div className="sv-head phone">
            <div className="row" style={{ gap: 8 }}>{seg}{link}</div>
            <div className="row" style={{ gap: 8 }}>
              <span className="small muted grow">{counts}{folder ? ' · numbers are the order in each series' : ''}</span>
              {hiddenChip}{add}{more}
            </div>
          </div>
        ) : (
          <div className="ctxbar sv-head">
            {folder && <span className="sv-ficon"><FolderIcon size={18} /></span>}
            {parent && <button className="linkbtn muted" onClick={() => ops.open({ kind: 'folder', id: parent.id })} title={`Open the folder ${parent.name}`}>{parent.name} /</button>}
            <span className="display" style={{ fontSize: 17, fontWeight: 500 }}>{name}</span>
            <span className="muted" style={{ whiteSpace: 'nowrap' }}>{counts}</span>
            {seg}{link}{hiddenChip}
            <div className="grow" />
            {add}{more}
          </div>
        )}
        <div className="sv-body">
          {boardSeen && <div className={'sv-pane' + (mode === 'board' ? '' : ' off')}><BoardView key={key} target={target} active={mode === 'board'} onInfo={i => { if (mode === 'board') setInfo(i) }} /></div>}
          {mode === 'strip' && <div className="sv-pane"><StripView key={key} target={target} name={name} folder={folder} ops={ops} onInfo={i => setInfo({ hidden: i.hidden, maybe: 0 })} onNewSeries={() => { if (folder) ops.newSeries(folder) }} /></div>}
        </div>
      </div>
      {adding && <AddPhotosDialog target={target} name={name} onClose={() => setAdding(false)} onAdded={n => { setAdding(false); s.toast(`${n} photo${n === 1 ? '' : 's'} added to “${name}”`); changed() }} />}
      {dlg && <LinkDialog target={target} name={name} st={dlg} canUndo={boardSeen} onPick={relink} onClose={() => setDlg(null)} />}
      {ops.node}
    </div>
  )
}
