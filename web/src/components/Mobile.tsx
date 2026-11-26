import { tray } from '../tray'
import { useEffect, useState } from 'react'
import { api, coverUrl, Photo } from '../api'
import { useStore } from '../store'
import { exifLine, fmtDay, stars } from '../fields'
import { FilterPanel, SortPanel } from './Left'
import { HideButton, RatingLabels, SeriesPicker, TagPanel, WhereLine } from './Inspector'
import { AutotagLine, AutotagMany } from './Autotag'
import { LastEdit } from './EditRecord'
import { photoShown, Thumb, useGroups, useReveal, useScrollResume } from './Library'
import { FoldersView } from './Folders'
import { WorkView } from './Work'
import { SeriesView } from './SeriesView'
import { FolderIcon, SeriesTree } from './SeriesTree'
import { buildTree, folderSubtree, targetName, targetOf } from '../seriesnav'
import { useSeriesOps } from '../seriesops'
import { DevicesIndicatorPhone } from './DevicesIndicator'
import { SettingsDialog } from './Shortcuts'
import { EditOnPhone, useListTitle } from './PhoneEdit'
import { ExportsList } from './Exports'
import { useVersionsFor, VersionBadge, VersionsStrip } from './Versions'
import { selectionIds, takeDeepLink } from '../devices'
import { useBackLayer } from '../back'
import { phoneSpot } from '../phonetabs'
import { tabTitle } from './Tabs'
import { NewSeriesSheet } from './NewSeries'
import { RenderDialog, RenderProgress } from './RenderProgress'

type Tab = 'lib' | 'folders' | 'series'  // (same as PhoneTab; the Devices page is not a tab: `devices` below)

function Sheet({ onClose, title, children }: { onClose: () => void; title: string; children: React.ReactNode }) {
  useBackLayer(true, onClose)  // Back closes it
  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="sheet" role="dialog" aria-label={title}>
        <span className="grab" />
        <div className="row"><span className="display grow" style={{ fontSize: 19, fontWeight: 500 }}>{title}</span><button className="btn primary big" onClick={onClose}>Done</button></div>
        {children}
      </div>
    </>
  )
}

function MobileGrid({ selecting, onOpen }: { selecting: boolean; onOpen: (p: Photo) => void }) {
  const s = useStore()
  const { groups, canGroup } = useGroups(s.items)
  const selSet = new Set(s.sel)
  useReveal()
  const resume = useScrollResume('phone')  // the photo page takes the grid's place: back from it, resume
  useEffect(() => {
    const el = document.getElementById('m-sentinel')
    if (!el) return
    const io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) s.loadMore() }, { rootMargin: '600px' })
    io.observe(el); return () => io.disconnect()
  }, [s.loadMore, s.items.length])
  return (
    <div className="scroll" {...resume}>
      {groups.map((g, gi) => (
        <div key={gi + g.label} style={{ marginBottom: 8 }}>
          {canGroup && <div className="group-h" style={{ marginBottom: 4 }}><b>{g.label}</b><span className="small muted">{g.items.length}</span></div>}
          <div className="mgrid">
            {g.items.map(p => (
              <button key={p.id} data-pid={p.id} className={selSet.has(p.id) ? 'sel' : ''} style={{ opacity: p.rating < 0 ? .45 : 1 }} aria-label={p.filename}
                onClick={() => (selecting ? s.setSel(selSet.has(p.id) ? s.sel.filter(x => x !== p.id) : [...s.sel, p.id]) : onOpen(p))}>
                <Thumb p={p} />
                {p.rating > 0 && <span className="stars">{stars(p.rating)}</span>}
                <VersionBadge p={p} />
              </button>
            ))}
          </div>
        </div>
      ))}
      {!s.loading && !s.items.length && <div className="muted" style={{ padding: 30, textAlign: 'center' }}>No photos match.</div>}
      <div id="m-sentinel" style={{ height: 1 }} />
    </div>
  )
}

function PhotoPage({ id, onBack }: { id: number; onBack: () => void }) {
  const s = useStore()
  const idx = s.items.findIndex(p => p.id === id)
  const [extra, setExtra] = useState<Photo | null>(null)
  const list = useListTitle()
  useEffect(() => { if (idx < 0) api.photo(id).then(setExtra).catch(() => {}) }, [id, idx])
  const p = idx >= 0 ? s.items[idx] : extra
  const vv = useVersionsFor(p ?? null)  // versions (R17): rating, labels and tags below are those of the version being viewed
  if (!p) return null
  const nav = (d: number) => { const n = s.items[idx + d]; if (n) s.setSel([n.id]) }
  const openFull = () => s.setFullscreen(idx >= 0 ? { list: s.items, index: idx } : { list: [p], index: 0 })
  return (
    <div className="scroll" style={{ padding: 0 }}>
      <div style={{ background: '#0f0f10', height: '42vh', display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative' }}
        onClick={openFull} role="button" aria-label="View full screen">
        <Thumb p={p} size="l" style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }} />
        <span className="fs-hint" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" /></svg>
        </span>
      </div>
      <div className="col" style={{ padding: 16, gap: 14 }}>
        <div className="row">
          <button className="btn big" disabled={idx <= 0} onClick={() => nav(-1)} aria-label="Previous">‹</button>
          <div className="col grow" style={{ gap: 2 }}>
            <b>{p.filename}</b>
            <span className="small muted">{p.taken ? fmtDay(p.taken) + ' ' + p.taken.slice(11, 16) : ''} · {p.folder.split('/').pop()}</span>
            <span className="small muted">{p.camera} · {exifLine(p)}</span>
          </div>
          <button className="btn big" disabled={idx < 0 || idx >= s.items.length - 1} onClick={() => nav(1)} aria-label="Next">›</button>
        </div>
        {vv.list && <VersionsStrip p={p} list={vv.list} n={vv.n} />}
        <RatingLabels photos={[vv.view ?? p]} big version={vv.mv} />
        <ExportsList p={p} />
        <LastEdit p={p} />
        <WhereLine id={p.id} />
        <button className="btn primary big" onClick={() => s.editInDarktable([p.id])}>Edit in darktable</button>
        <EditOnPhone ids={idx >= 0 ? s.items.map(x => x.id) : [p.id]} start={p.id} series={list.series} title={list.title} />
        <span className="small muted" style={{ marginTop: -6 }}>Opens on one of your devices (or as soon as it's online). Ratings, labels and tags are saved to the XMP right away.</span>
        <SeriesPicker ids={[p.id]} />
        <TagPanel photos={[vv.view ?? p]} version={vv.mv} />
        <AutotagLine id={p.id} />
        <HideButton photos={[p]} big />
        <button className="btn big" onClick={onBack}>Back to library</button>
      </div>
    </div>
  )
}

/** The Series tab's overview: folders (each with its series) and the series at the top level. `board` opens one
 *  (a series' id, or minus a folder's id); `open` shows its photos in order in the Library. */
function SeriesTab({ open, board }: { open: (key: number) => void; board: (key: number) => void }) {
  const s = useStore()
  const ops = useSeriesOps()
  const tree = buildTree(s.series, s.seriesFolders)
  const card = (key: number, name: string, sub: string, covers: { id: number; v: number }[], folder: boolean) => (
    <div key={key} className="card">
      <button style={{ border: 0, background: 'none', padding: 0, textAlign: 'left', color: 'inherit' }} className="col" onClick={() => board(key)}>
        <div className="row">{folder && <span style={{ color: 'var(--accent)', display: 'inline-flex' }}><FolderIcon size={18} /></span>}<b className="grow ellipsis">{name}</b><span className="muted">{sub}</span></div>
        <div className="row" style={{ gap: 3, overflow: 'hidden' }}>
          {covers.map(c => <img key={c.id} src={coverUrl(c)} alt="" style={{ width: 56, height: 56, objectFit: 'cover', borderRadius: 4 }} />)}
        </div>
      </button>
      <div className="row" style={{ gap: 8 }}>
        <button className="btn primary big grow" onClick={() => board(key)}>{folder ? 'Open' : 'Board'}</button>
        <button className="btn big grow" onClick={() => open(key)}>Photos in order</button>
      </div>
    </div>
  )
  return (
    <div className="scroll">
      <div className="col" style={{ padding: '0 14px', gap: 12 }}>
        {tree.entries.map(e => e.kind === 'folder'
          ? card(-e.folder.id, e.folder.name, `${e.folder.total_series ?? e.folder.series_count} series · ${e.folder.total_count ?? e.folder.count}`, s.series.filter(x => x.folder_id != null && folderSubtree(s.seriesFolders, e.folder.id).has(x.folder_id)).flatMap(x => x.covers.slice(0, 2)).slice(0, 6), true)
          : card(e.series.id, e.series.name, String(e.series.count), e.series.covers, false))}
        {!s.series.length && !s.seriesFolders.length && <span className="muted">No series yet. Select photos and use “Add to series”.</span>}
        <button className="btn dashed big" onClick={() => ops.newFolder()}>+ New folder</button>
        <span className="small muted">A folder holds several series (and folders: “Series” at the top of an open folder lists them). In a folder or a series, “Strip” shows the photos in order: press and hold one to move it. On the board: drag empty space to move around, pinch to zoom, tap a photo to select it and drag it to move it. What happens on a double-tap and more are in Settings (⚙ at the top).</span>
      </div>
      {ops.node}
    </div>
  )
}

export function MobileApp() {
  const s = useStore()
  const list = useListTitle()
  const [tab, setTab] = useState<Tab>('lib')
  const [sheet, setSheet] = useState<'' | 'sort' | 'edit'>('')
  const [selecting, setSelecting] = useState(false)
  const [photo, setPhoto] = useState<number | null>(null)
  const [board, setBoard] = useState<number | null>(null)
  const [settings, setSettings] = useState(false)
  const [tabsOpen, setTabsOpen] = useState(false)
  const [newSeries, setNewSeries] = useState(false)
  const [tree, setTree] = useState(false)  // the series tree, opened from the title of an open folder or series
  const [cameFrom, setCameFrom] = useState<'folders' | 'series' | null>(null)  // a folder or series opened from its overview
  const [devices, setDevices] = useState(false)  // the Devices page, over whatever was open; Back returns to that
  const closeDevices = () => { setDevices(false); if (s.view === 'work') s.setView('grid') }
  useEffect(() => { if (photo != null && s.sel.length === 1 && s.sel[0] !== photo) setPhoto(s.sel[0]) }, [s.sel])
  useEffect(() => { if (photo != null) photoShown(s.activeTab, photo) }, [photo])  // the grid shows it when you go back
  // show what the open tab shows (also a tab made or changed on the desktop); a view the phone has no
  // screen for is shown as the grid, without changing the tab
  useEffect(() => {
    const st = s.tabs.find(t => t.id === s.activeTab)?.st  // as stored: the live values may not be applied yet
    const sp = phoneSpot(st?.view ?? s.view, st ? st.boardSeries : s.boardSeries)
    setTab(sp.tab); setBoard(sp.board); setPhoto(null); setCameFrom(null); setSelecting(false); setDevices(!!sp.devices)
  }, [s.activeTab])
  // the phone's Back steps out of what is open, one thing at a time
  useBackLayer(photo != null, () => setPhoto(null))
  useBackLayer(board != null, () => { setBoard(null); s.setView('grid') })
  // another folder or series was opened from inside (the tree, a lane's menu), or the open one was deleted
  useEffect(() => { if (board != null && s.boardSeries !== board) { setBoard(s.boardSeries); if (s.boardSeries == null) s.setView('grid') } }, [s.boardSeries])
  useBackLayer(cameFrom != null && tab === 'lib', () => goTab(cameFrom!))
  useBackLayer(devices, closeDevices)
  // a deep link: ?photo=<id> opens the photo's page, ?series=<id> the series' board
  useEffect(() => {
    const d = takeDeepLink()
    if (d?.photo != null) { const id = d.photo; api.photo(id).then(() => { setTab('lib'); s.setSel([id]); setPhoto(id) }).catch(e => s.toast(e.message)) }
    else if (d?.series != null) {
      const id = d.series
      api.series().then(r => {
        if (r.series.some(x => x.id === id)) { setTab('series'); setPhoto(null); s.setBoardSeries(id); s.setView('board'); setBoard(id) }
        else s.toast('series not found')
      }).catch(e => s.toast(e.message))
    }
  }, [])
  // "Show in library" (e.g. from the Devices tab): go to the Library, where the grid scrolls to the photo
  useEffect(() => { if (s.revealId != null) { setTab('lib'); setPhoto(null); setDevices(false) } }, [s.revealId])
  const openPhoto = (p: Photo) => { s.setSel([p.id]); setPhoto(p.id) }
  const goTab = (t: Tab) => { setTab(t); setPhoto(null); setBoard(null); setCameFrom(null); if (t === 'lib') s.setView('grid'); if (t === 'folders') s.setView('folders'); setDevices(false) }
  const title = devices ? 'Devices' : board != null ? (targetName(targetOf(board), s.series, s.seriesFolders) || 'Series') : photo != null ? 'Photo' : tab === 'lib' ? 'Library' : tab === 'folders' ? 'Folders' : 'Series'
  const fcount = [s.filters.folder, (s.filters.folders || []).length, s.filters.month, s.filters.tag, s.filters.series, s.filters.series_folder, s.filters.edited, (s.filters.lenses || []).length, (s.filters.cameras || []).length, (s.filters.formats || []).length, (s.filters.labels || []).length, (s.filters.ratings || []).length].filter(Boolean).length
  return (
    <div className="app">
      <RenderProgress />
      <RenderDialog />
      <div className="mhead">
        {(devices || photo != null || board != null) && <button className="btn ghost big" onClick={() => { if (devices) closeDevices(); else if (photo != null) setPhoto(null); else { setBoard(null); s.setView('grid') } }} aria-label={board != null && photo == null ? 'Back to all series' : 'Back'}>‹</button>}
        {!devices && board != null && photo == null
          ? <button className="mtitle" aria-label={`${title}. Open the series tree to switch folder or series`} onClick={() => setTree(true)}>
              {board < 0 && <span style={{ color: 'var(--accent)', display: 'inline-flex', flex: '0 0 auto' }}><FolderIcon size={20} /></span>}
              <span className="display">{title}</span>
              <span className="mtitle-chev"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg></span>
            </button>
          : <span className="display">{title}</span>}
        {!s.isLocal && <DevicesIndicatorPhone pressed={devices} onOpen={() => (devices ? closeDevices() : setDevices(true))} />}
        <button className="btn big" style={{ minWidth: 44, fontWeight: 600 }} aria-label={`Tabs: ${s.tabs.length} open`} title="Tabs" onClick={() => setTabsOpen(true)}>{s.tabs.length}</button>
        {(devices || (photo == null && !(tab === 'lib' && !board))) && <button className="btn ghost big" aria-label="Settings" onClick={() => setSettings(true)}>⚙</button>}
        {!devices && tab === 'series' && photo == null && board == null && <button className="btn big" onClick={() => setNewSeries(true)}>New series</button>}
        {!devices && tab === 'lib' && photo == null && (
          <>
            <button className={'btn big' + (selecting ? ' primary' : '')} onClick={() => { setSelecting(!selecting); if (selecting) s.setSel([]) }}>{selecting ? 'Done' : 'Select'}</button>
            <button className="btn big" onClick={() => setSheet('sort')}>Sort{fcount ? ` · ${fcount}` : ''}</button>
          </>
        )}
      </div>
      {!devices && tab === 'lib' && photo == null && (
        <div className="col" style={{ padding: '0 14px 8px', gap: 8 }}>
          <input className="input" style={{ height: 40, fontSize: 15 }} placeholder="Search filename, folder, tag, lens…" value={s.filters.q || ''} onChange={e => s.patchFilters({ q: e.target.value })} aria-label="Search" />
          <div className="row wrap" style={{ gap: 6 }}>
            <span className="small muted">{s.total} photos</span>
            {s.filters.folder && <button className="chip on" onClick={() => s.patchFilters({ folder: null })}>{s.filters.folder.split('/').pop()} ×</button>}
            {(s.filters.folders || []).length > 0 && <button className="chip on" onClick={() => s.patchFilters({ folders: [] })}>{(s.filters.folders || []).length} folders ×</button>}
            {s.filters.series && <button className="chip on" onClick={() => s.patchFilters({ series: null })}>{s.series.find(x => x.id === s.filters.series)?.name} ×</button>}
            {s.filters.series_folder && <button className="chip on" onClick={() => s.patchFilters({ series_folder: null })}>{s.seriesFolders.find(x => x.id === s.filters.series_folder)?.name} ×</button>}
            {s.filters.tag && <button className="chip on" onClick={() => s.patchFilters({ tag: null })}>{s.filters.tag} ×</button>}
          </div>
        </div>
      )}
      <div key={s.activeTab} style={{ flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        {devices ? <WorkView />
          : photo != null ? <PhotoPage id={photo} onBack={() => setPhoto(null)} />
          : board != null ? <div className="mboard"><SeriesView /></div>
          : tab === 'lib' ? <MobileGrid selecting={selecting} onOpen={openPhoto} />
          : tab === 'folders' ? <FoldersViewMobile onOpen={() => { goTab('lib'); setCameFrom('folders') }} />
          : tab === 'series' ? <SeriesTab board={id => { s.setBoardSeries(id); s.setView('board'); setBoard(id) }} open={id => { s.patchFilters({ series: id > 0 ? id : null, series_folder: id < 0 ? -id : null, folder: null, tag: null, month: null }); s.setSort([{ f: 'custom', d: 'asc' }]); s.setGroup(false); goTab('lib'); setCameFrom('series') }} />
          : <WorkView />}
      </div>
      {selecting && s.sel.length > 0 && photo == null && !devices && (
        <div className="row" style={{ padding: '8px 14px', background: '#222226', borderTop: '1px solid var(--line)' }}>
          <span className="grow">{s.sel.length} selected</span>
          <button className="btn primary big" onClick={() => setSheet('edit')}>Rate & tag</button>
          <button className="btn big" onClick={() => s.editInDarktable(s.sel)}>darktable</button>
          <button className="btn big" title="Collect them to place into a series' Strip later" onClick={() => { const n = tray.add(selectionIds(s.items.map(x => x.id), s.sel)); s.toast(n ? `${n} added to the tray · open a series as Strip to place them` : 'Already in the tray') }}>Tray</button>
          {(() => { const ids = selectionIds(s.items.map(x => x.id), s.sel)  // only the selected photos
            return <EditOnPhone ids={ids} start={ids[0]} series={list.series} title={list.title} label="This phone" /> })()}
        </div>
      )}
      <nav className="mtabs">
        {([['lib', 'Library'], ['folders', 'Folders'], ['series', 'Series']] as [Tab, string][]).map(([t, l]) => (
          <button key={t} className={tab === t ? 'on' : ''} onClick={() => goTab(t)}><i />{l}</button>
        ))}
      </nav>
      {settings && <SettingsDialog onClose={() => setSettings(false)} />}
      {tabsOpen && <TabsSheet onClose={() => setTabsOpen(false)} />}
      {tree && <Sheet title="Series" onClose={() => setTree(false)}><SeriesTree phone current={targetOf(board)} onOpened={() => setTree(false)} /></Sheet>}
      {newSeries && <NewSeriesSheet onClose={() => setNewSeries(false)} onCreate={async name => {
        try {
          const r = await api.seriesCreate(name, [])
          s.toast(`Series “${name}” created`)
          s.reloadSeries(); s.setBoardSeries(r.id); s.setView('board'); setBoard(r.id)  // its empty board, with "+ Add photos"
          return true
        } catch (e: any) { s.toast(e.message); return false }
      }} />}
      {sheet === 'sort' && <Sheet title="Sort & filter" onClose={() => setSheet('')}><SortPanel /><div className="hr" /><FilterPanel /></Sheet>}
      {sheet === 'edit' && <Sheet title={`${s.selPhotos.length} photos`} onClose={() => setSheet('')}>
        <RatingLabels photos={s.selPhotos} big />
        <SeriesPicker ids={s.sel} />
        <TagPanel photos={s.selPhotos} />
        {s.sel.length > 1 ? <AutotagMany ids={s.sel} /> : s.sel.length === 1 ? <AutotagLine id={s.sel[0]} /> : null}
        <HideButton photos={s.selPhotos} big />
      </Sheet>}
    </div>
  )
}

/** The desktop's tabs on the phone, like a browser's tab switcher: tap one to switch, + for a new one,
 *  ✎ to name it, × to close it (never the last one). Same tabs and same rules as the tab strip on the desktop. */
function TabsSheet({ onClose }: { onClose: () => void }) {
  const s = useStore()
  const [editing, setEditing] = useState<string | null>(null)
  const tabs = s.tabs.map(t => (t.id === s.activeTab ? { ...t, st: s.liveTab() } : t))
  return (
    <Sheet title={`${tabs.length} tab${tabs.length > 1 ? 's' : ''}`} onClose={onClose}>
      <div className="col" style={{ gap: 6 }} role="tablist" aria-label="Tabs">
        {tabs.map(t => {
          const title = tabTitle(t, s.series, s.seriesFolders)
          const on = t.id === s.activeTab
          const note = phoneSpot(t.st.view, t.st.boardSeries).note
          return (
            <div key={t.id} role="tab" aria-selected={on} className="card" style={{ flexDirection: 'row', alignItems: 'center', gap: 8, padding: '8px 10px', border: '1px solid ' + (on ? 'var(--accent)' : 'transparent') }}>
              {editing === t.id
                ? <input autoFocus className="input grow" style={{ height: 40, fontSize: 15 }} defaultValue={t.title || title} aria-label="Tab name"
                    onBlur={e => { s.renameTab(t.id, e.target.value === title && !t.title ? '' : e.target.value); setEditing(null) }}
                    onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditing(null) }} />
                : <button className="linkbtn grow col" style={{ gap: 2, minHeight: 40, justifyContent: 'center', alignItems: 'flex-start' }} onClick={() => { s.switchTab(t.id); onClose() }}>
                    <span className="ellipsis" style={{ fontSize: 15, fontWeight: on ? 600 : 400 }}>{title}</span>
                    {(on || note) && <span className="tiny muted">{[on ? 'open now' : '', note].filter(Boolean).join(' · ')}</span>}
                  </button>}
              {editing !== t.id && <button className="btn ghost big" aria-label={`Name ${title}`} onClick={() => setEditing(t.id)}>✎</button>}
              {s.tabs.length > 1 && editing !== t.id && <button className="btn ghost big" aria-label={`Close ${title}`} onClick={() => s.closeTab(t.id)}>×</button>}
            </div>
          )
        })}
      </div>
      <button className="btn big" onClick={() => { s.newTab(); onClose() }}>+ New tab, showing all photos</button>
    </Sheet>
  )
}

function FoldersViewMobile({ onOpen }: { onOpen: () => void }) {
  const s = useStore()
  // FoldersView switches to view 'grid' when a folder is opened; follow it back to the Library tab
  useEffect(() => { if (s.view === 'grid') onOpen() }, [s.view])
  return <FoldersView />
}
