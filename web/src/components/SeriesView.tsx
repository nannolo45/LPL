// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, LinkState, SeriesFolder, Target } from '../api'
import { useStore } from '../store'
import { keyOf, resolveTarget } from '../seriesnav'
import { bumpSeries, setPendingUndo, MenuButton, MenuItem, useSeriesOps } from '../seriesops'
import { useBackLayer } from '../back'
import { BoardView } from './Board'
import { ExtDrag, StripView } from './Strip'
import { StripSource } from './StripSource'
import { tray } from '../tray'
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
  const nf = st.new_folders || []
  let intro: string, a: string, b: string
  if (folder) {
    const parts = [
      free.length || missing.length ? `the board has ${plural(st.frames, 'frame')}, the strip has ${st.series} series` : '',
      st.moves ? `${plural(st.moves, 'photo')} ${st.moves === 1 ? 'lies' : 'lie'} in another series' frame` : '',
      st.refiled ? `${st.refiled} frame${st.refiled === 1 ? ' lies' : 's lie'} in another folder's group frame than in the tree` : '',
      nf.length ? `${quote(nf)} hold${nf.length === 1 ? 's' : ''} frames but ${nf.length === 1 ? 'is' : 'are'} no folder yet` : '',
      !free.length && !missing.length && !st.moves && st.reordered ? `the photos of ${plural(st.reordered!, 'series', 'series')} stand in another order on the board` : '',
    ].filter(Boolean)
    intro = `The board and the strip of ${name} no longer match: ${parts.join('; ') || 'they differ'}. Which one wins?`
    const fp = free.reduce((n, f) => n + f.photos, 0)
    a = 'One frame per series, photos placed in strip order.'
      + (free.length ? ` The frame${free.length > 1 ? 's' : ''} ${quote(free.map(f => f.label))} ${free.length > 1 ? 'are' : 'is'} removed${fp ? `; ${fp === 1 ? 'its photo goes' : `${free.length > 1 ? 'their' : 'its'} ${fp} photos go`} back to ${fp === 1 ? 'its series' : 'their series'}` : ''}.` : '')
      + (missing.length ? ` ${quote(missing)} get${missing.length === 1 ? 's' : ''} a frame.` : '')
      + (st.refiled || nf.length ? ' Group frames are rebuilt from the folder tree: every subfolder gets its frame around its series.' + (nf.length ? ` ${quote(nf)} ${nf.length === 1 ? 'is' : 'are'} removed.` : '') : '')
      + (loose.length ? ` ${plural(loose.length, 'photo')} on the board ${loose.length === 1 ? 'is' : 'are'} in no series (${loose.slice(0, 4).map(p => p.filename).join(', ')}${loose.length > 4 ? ', …' : ''}): ${loose.length === 1 ? 'it stays' : 'they stay'} on the board, outside the frames.` : '')
    b = 'Each frame becomes a series, photos in board order.'
      + (free.length ? ` ${quote(free.map(f => f.label))} become${free.length === 1 ? `s a ${ORD[st.series!] || 'new'} series` : ' new series'} in ${name}.` : '')
      + (st.moves ? ` ${plural(st.moves, 'photo')} change${st.moves === 1 ? 's' : ''} series (their tags follow).` : '')
      + (nf.length ? ` ${quote(nf)} become${nf.length === 1 ? 's a subfolder' : ' subfolders'} of ${name}.` : '')
      + (st.refiled ? ` ${st.refiled} series or folder${st.refiled === 1 ? '' : 's'} move${st.refiled === 1 ? 's' : ''} to the folder whose group frame ${st.refiled === 1 ? 'it lies' : 'they lie'} in (their tags follow).` : '')
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
          <span className="col" style={{ gap: 3 }}><b>Take the order from the board</b><span className="small soft">{b}{canUndo ? ' Undo on the board puts the old order, series and tags back (unlinked again).' : ''}</span></span>
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
  // the left side in Strip mode: the series tree, or the photo panel to drag photos in from (remembered on this device)
  const [side, setSideRaw] = useState<'series' | 'photos'>(() => { try { return localStorage.getItem('lpl.strip.side') === 'photos' ? 'photos' : 'series' } catch { return 'series' } })
  const setSide = (v: 'series' | 'photos') => { setSideRaw(v); try { localStorage.setItem('lpl.strip.side', v) } catch { /* ignore */ } }
  const [members, setMembers] = useState<Map<number, string>>(new Map())
  const [srcRev, setSrcRev] = useState(0)
  const ext = useRef<ExtDrag | null>(null)
  useEffect(() => { const m = readMode(key); setModeRaw(m); setBoardSeen(m === 'board'); setAdding(false); setDlg(null); setInfo({ hidden: 0, maybe: 0 }) }, [key])
  const setMode = (m: Mode) => { setModeRaw(m); if (m === 'board') setBoardSeen(true); try { localStorage.setItem('lpl.seriesmode.' + key, m) } catch { /* ignore */ } }

  const folder = target?.kind === 'folder' ? s.seriesFolders.find(f => f.id === target.id) || null : null
  const series = target?.kind === 'series' ? s.series.find(x => x.id === target.id) || null : null
  const parent = series?.folder_id != null ? s.seriesFolders.find(f => f.id === series.folder_id) || null : null
  const name = folder?.name || series?.name || ''
  const linked = folder ? folder.linked : !!series?.linked
  // nested folders: the folders above what is open (the way back), and the top-level folder whose board a subfolder is on
  const crumbs = useMemo(() => {
    const out: SeriesFolder[] = []
    for (let f = s.seriesFolders.find(x => x.id === (folder ? folder.parent_id : series?.folder_id)), n = 0; f && n < 50; f = s.seriesFolders.find(x => x.id === f!.parent_id), n++) out.unshift(f)
    return out
  }, [folder, series, s.seriesFolders])
  const rootFolder = folder && crumbs.length ? crumbs[0] : folder
  const showHidden = !!s.filters.show_hidden

  const photosSide = !phone && side === 'photos' && mode === 'strip' && !!target
  const sideSeg = !phone && mode === 'strip' && !!target && (
    <div className="seg ssrc-seg" role="group" aria-label="Left panel">
      <button className={side === 'series' ? 'on' : ''} aria-pressed={side === 'series'} onClick={() => setSide('series')}>Series</button>
      <button className={side === 'photos' ? 'on' : ''} aria-pressed={side === 'photos'} onClick={() => setSide('photos')}>Photos</button>
    </div>
  )
  const tree = !phone && (photosSide ? (
    <nav className="series-list wide" aria-label="Photos to add">
      {sideSeg}
      <StripSource target={target!} name={folder?.name || series?.name || ''} members={members} ext={ext} rev={srcRev} />
    </nav>
  ) : (
    <nav className="series-list" aria-label="Series">
      {sideSeg}
      <SeriesTree current={target} />
      <details className="st-help small muted">
        <summary>How the board works</summary>
        “+ Add photos” brings in whole folders or everything matching a rating, label, tag or date. Add a frame with the frame button on the left, double-click its name to rename it, drag it by its name bar or edges. Shift+1 shows everything, Shift+2 the selection, [ and ] jump between frames, and the frames button lists them all.
        <br /><br />Drag on empty space to select several things (Shift adds). Drag with the right or middle mouse button, or hold Space and drag, to move around; on a touch screen drag empty space with one finger and pinch with two to zoom. How the mouse, wheel and double-click behave is in Settings (⚙ at the top). Keys: V select, N note, A arrow, Delete takes what's selected off the board (change them with the keyboard button at the top). Double-click empty space for a note, an arrow to write on it.
      </details>
    </nav>
  ))

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
        if (st.same) { await api.tlinkSet(target, true, 'board'); s.toast(`Board linked to ${rootFolder?.name || name} again; nothing had changed.`); changed() }
        else setDlg(st)
      }
    } catch (e: any) { s.toast(e.message) }
    setBusy(false)
  }
  const relink = async (m: 'strip' | 'board') => {
    setDlg(null)
    try {
      const r = await api.tlinkSet(target, true, m)
      if (m === 'board' && r.undo != null && boardSeen) setPendingUndo(target, r.undo)
      const bits = [
        m === 'strip' ? 'Board arranged from the strip.' : 'Order taken from the board.',
        r.removed_frames?.length ? `Removed: ${r.removed_frames.map(x => `“${x}”`).join(', ')}.` : '',
        r.created?.length ? `New series: ${r.created.map(x => `“${x}”`).join(', ')}.` : '',
        r.created_folders?.length ? `New folder${r.created_folders.length > 1 ? 's' : ''}: ${r.created_folders.map(x => `“${x}”`).join(', ')}.` : '',
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
    ...(folder ? [{ label: `New series in ${folder.name}`, onClick: () => { ops.newSeries(folder) } }, { label: `New folder in ${folder.name}`, onClick: () => { ops.newFolder(folder) } }] : []),
    ...(folder && crumbs.length ? [{ label: `Open the folder ${crumbs[crumbs.length - 1].name}`, onClick: () => ops.open({ kind: 'folder', id: crumbs[crumbs.length - 1].id }) }] : []),
    { label: 'Rename', onClick: () => (folder ? ops.renameFolder(folder) : ops.renameSeries(series!)) },
    ...(series && parent ? [{ label: `Open the folder ${parent.name}`, onClick: () => ops.open({ kind: 'folder', id: parent.id }) }] : []),
    { label: folder ? 'Delete folder' : 'Delete', danger: true, onClick: () => { folder ? ops.deleteFolder(folder) : ops.deleteSeries(series!) } },
  ]
  const counts = folder ? `${folder.total_series ?? folder.series_count} series · ${plural(folder.total_count ?? folder.count, 'photo')}` : plural(series!.count, 'photo') + (info.maybe ? ` · ${info.maybe} in maybe piles` : '')
  const linkTitle = parent ? `Inside a folder the order of a series comes from the folder: ${parent.name}'s board${parent.linked ? '' : ' or strip'}. This board is free.`
    : `Link or unlink the board and the ${folder ? 'series of ' + name : 'order of ' + name}`
  const linkText = parent ? (phone ? `Order from ${parent.name}` : `Order follows ${parent.name}`) : linked ? (phone ? 'Board linked' : `Board linked to ${rootFolder?.name || name}`) : phone ? 'Not linked' : 'Board not linked'
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
  const add = <button className="btn primary" onClick={() => setAdding(true)} aria-label="Add photos" title="Add photos from folders, or by rating, label, tag and date">{phone ? '+ Add' : '+ Add photos'}</button>
  const more = <MenuButton label="More: show in library, rename, delete" items={menu} className="sv-more" />
  return (
    <div className="board-wrap">
      {tree}
      <div className="center">
        {phone ? (
          <div className="sv-head phone">
            {crumbs.length > 0 && <div className="row sv-crumbs" style={{ gap: 6 }}>{crumbs.map(f => <button key={f.id} className="linkbtn muted sv-crumb" onClick={() => ops.open({ kind: 'folder', id: f.id })}>{f.name} /</button>)}<span className="small">{name}</span></div>}
            <div className="row" style={{ gap: 8 }}>{seg}{link}</div>
            <div className="row" style={{ gap: 8 }}>
              <span className="small muted grow">{counts}{folder ? ' · numbers are the order in each series' : ''}</span>
              {hiddenChip}{add}{more}
            </div>
          </div>
        ) : (
          <div className="ctxbar sv-head">
            {folder && <span className="sv-ficon"><FolderIcon size={18} /></span>}
            {crumbs.map(f => <button key={f.id} className="linkbtn muted sv-crumb" onClick={() => ops.open({ kind: 'folder', id: f.id })} title={`Open the folder ${f.name}`}>{f.name} /</button>)}
            <span className="display" style={{ fontSize: 17, fontWeight: 500 }}>{name}</span>
            <span className="muted" style={{ whiteSpace: 'nowrap' }}>{counts}</span>
            {seg}{link}{hiddenChip}
            <div className="grow" />
            {add}{more}
          </div>
        )}
        <div className="sv-body">
          {boardSeen && <div className={'sv-pane' + (mode === 'board' ? '' : ' off')}><BoardView key={key} target={target} active={mode === 'board'} onInfo={i => { if (mode === 'board') setInfo(i) }} /></div>}
          {mode === 'strip' && <div className="sv-pane"><StripView key={key} target={target} name={name} folder={folder} ops={ops} ext={ext}
            onInfo={i => { setInfo({ hidden: i.hidden, maybe: 0 }); if (i.members) setMembers(i.members) }}
            onAdded={() => setSrcRev(r => r + 1)}
            onNewSeries={async ids => {
              if (!folder) return
              const id = await ops.newSeries(folder, ids || [])
              if (id && ids?.length) { tray.remove(ids); setSrcRev(r => r + 1) }
            }} /></div>}
        </div>
      </div>
      {adding && <AddPhotosDialog target={target} name={name} onClose={() => setAdding(false)} onAdded={n => { setAdding(false); s.toast(`${n} photo${n === 1 ? '' : 's'} added to “${name}”`); changed() }} />}
      {dlg && <LinkDialog target={target} name={name} st={dlg} canUndo={boardSeen} onPick={relink} onClose={() => setDlg(null)} />}
      {ops.node}
    </div>
  )
}
