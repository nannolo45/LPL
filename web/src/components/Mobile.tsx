import { useEffect, useState } from 'react'
import { api, coverUrl, Photo } from '../api'
import { useStore } from '../store'
import { exifLine, fmtDay, stars } from '../fields'
import { FilterPanel, SortPanel } from './Left'
import { HideButton, RatingLabels, SeriesPicker, TagPanel, WhereLine } from './Inspector'
import { Thumb, useGroups, useReveal } from './Library'
import { FoldersView } from './Folders'
import { WorkView } from './Work'
import { BoardView } from './Board'
import { SettingsDialog } from './Shortcuts'
import { EditOnPhone, useListTitle } from './PhoneEdit'
import { selectionIds, takeDeepLink } from '../devices'
import { useBackLayer } from '../back'
import { phoneSpot } from '../phonetabs'
import { tabTitle } from './Tabs'

type Tab = 'lib' | 'folders' | 'series' | 'pc'  // 'pc': the devices and their working sets (same as PhoneTab)

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
  useEffect(() => {
    const el = document.getElementById('m-sentinel')
    if (!el) return
    const io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) s.loadMore() }, { rootMargin: '600px' })
    io.observe(el); return () => io.disconnect()
  }, [s.loadMore, s.items.length])
  return (
    <div className="scroll">
      {groups.map((g, gi) => (
        <div key={gi + g.label} style={{ marginBottom: 8 }}>
          {canGroup && <div className="group-h" style={{ marginBottom: 4 }}><b>{g.label}</b><span className="small muted">{g.items.length}</span></div>}
          <div className="mgrid">
            {g.items.map(p => (
              <button key={p.id} data-pid={p.id} className={selSet.has(p.id) ? 'sel' : ''} style={{ opacity: p.rating < 0 ? .45 : 1 }} aria-label={p.filename}
                onClick={() => (selecting ? s.setSel(selSet.has(p.id) ? s.sel.filter(x => x !== p.id) : [...s.sel, p.id]) : onOpen(p))}>
                <Thumb p={p} />
                {p.rating > 0 && <span className="stars">{stars(p.rating)}</span>}
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
        <RatingLabels photos={[p]} big />
        <WhereLine id={p.id} />
        <button className="btn primary big" onClick={() => s.editInDarktable([p.id])}>Edit in darktable</button>
        <EditOnPhone ids={idx >= 0 ? s.items.map(x => x.id) : [p.id]} start={p.id} series={list.series} title={list.title} />
        <span className="small muted" style={{ marginTop: -6 }}>Opens on one of your devices (or as soon as it's online). Ratings, labels and tags are saved to the XMP right away.</span>
        <SeriesPicker ids={[p.id]} />
        <TagPanel photos={[p]} />
        <HideButton photos={[p]} big />
        <button className="btn big" onClick={onBack}>Back to library</button>
      </div>
    </div>
  )
}

function SeriesTab({ open, board }: { open: (id: number) => void; board: (id: number) => void }) {
  const s = useStore()
  return (
    <div className="scroll">
      <div className="col" style={{ padding: '0 14px', gap: 12 }}>
        {s.series.map(x => (
          <div key={x.id} className="card">
            <button style={{ border: 0, background: 'none', padding: 0, textAlign: 'left', color: 'inherit' }} className="col" onClick={() => board(x.id)}>
              <div className="row"><b className="grow">{x.name}</b><span className="muted">{x.count}</span></div>
              <div className="row" style={{ gap: 3, overflow: 'hidden' }}>
                {x.covers.map(c => <img key={c.id} src={coverUrl(c)} alt="" style={{ width: 56, height: 56, objectFit: 'cover', borderRadius: 4 }} />)}
              </div>
            </button>
            <div className="row" style={{ gap: 8 }}>
              <button className="btn primary big grow" onClick={() => board(x.id)}>Board</button>
              <button className="btn big grow" onClick={() => open(x.id)}>Photos in order</button>
            </div>
          </div>
        ))}
        {!s.series.length && <span className="muted">No series yet. Select photos and use “Add to series”.</span>}
        <span className="small muted">On the board: drag empty space to move around, pinch to zoom, tap a photo to select it and drag it to move it. What happens on a double-tap and more are in Settings (⚙ at the top).</span>
      </div>
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
  const [cameFrom, setCameFrom] = useState<'folders' | 'series' | null>(null)  // a folder or series opened from its overview
  useEffect(() => { if (photo != null && s.sel.length === 1 && s.sel[0] !== photo) setPhoto(s.sel[0]) }, [s.sel])
  // show what the open tab shows (also a tab made or changed on the desktop); a view the phone has no
  // screen for is shown as the grid, without changing the tab
  useEffect(() => {
    const st = s.tabs.find(t => t.id === s.activeTab)?.st  // as stored: the live values may not be applied yet
    const sp = phoneSpot(st?.view ?? s.view, st ? st.boardSeries : s.boardSeries)
    setTab(sp.tab); setBoard(sp.board); setPhoto(null); setCameFrom(null); setSelecting(false)
  }, [s.activeTab])
  // the phone's Back steps out of what is open, one thing at a time
  useBackLayer(photo != null, () => setPhoto(null))
  useBackLayer(board != null, () => { setBoard(null); s.setView('grid') })
  useBackLayer(cameFrom != null && tab === 'lib', () => goTab(cameFrom!))
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
  useEffect(() => { if (s.revealId != null) { setTab('lib'); setPhoto(null) } }, [s.revealId])
  const openPhoto = (p: Photo) => { s.setSel([p.id]); setPhoto(p.id) }
  const goTab = (t: Tab) => { setTab(t); setPhoto(null); setBoard(null); setCameFrom(null); if (t === 'lib') s.setView('grid'); if (t === 'folders') s.setView('folders'); if (t === 'pc' && !s.isLocal) s.setView('work') }
  const title = board != null ? (s.series.find(x => x.id === board)?.name || 'Board') : photo != null ? 'Photo' : tab === 'lib' ? 'Library' : tab === 'folders' ? 'Folders' : tab === 'series' ? 'Series' : 'Devices'
  const fcount = [s.filters.folder, (s.filters.folders || []).length, s.filters.month, s.filters.tag, s.filters.series, s.filters.edited, (s.filters.lenses || []).length, (s.filters.cameras || []).length, (s.filters.labels || []).length, (s.filters.ratings || []).length].filter(Boolean).length
  return (
    <div className="app">
      <div className="mhead">
        {(photo != null || board != null) && <button className="btn ghost big" onClick={() => { if (photo != null) setPhoto(null); else { setBoard(null); s.setView('grid') } }} aria-label="Back">‹</button>}
        <span className="display">{title}</span>
        <button className="btn big" style={{ minWidth: 44, fontWeight: 600 }} aria-label={`Tabs: ${s.tabs.length} open`} title="Tabs" onClick={() => setTabsOpen(true)}>{s.tabs.length}</button>
        {photo == null && !(tab === 'lib' && !board) && <button className="btn ghost big" aria-label="Settings" onClick={() => setSettings(true)}>⚙</button>}
        {tab === 'lib' && photo == null && (
          <>
            <button className={'btn big' + (selecting ? ' primary' : '')} onClick={() => { setSelecting(!selecting); if (selecting) s.setSel([]) }}>{selecting ? 'Done' : 'Select'}</button>
            <button className="btn big" onClick={() => setSheet('sort')}>Sort{fcount ? ` · ${fcount}` : ''}</button>
          </>
        )}
      </div>
      {tab === 'lib' && photo == null && (
        <div className="col" style={{ padding: '0 14px 8px', gap: 8 }}>
          <input className="input" style={{ height: 40, fontSize: 15 }} placeholder="Search filename, folder, tag, lens…" value={s.filters.q || ''} onChange={e => s.patchFilters({ q: e.target.value })} aria-label="Search" />
          <div className="row wrap" style={{ gap: 6 }}>
            <span className="small muted">{s.total} photos</span>
            {s.filters.folder && <button className="chip on" onClick={() => s.patchFilters({ folder: null })}>{s.filters.folder.split('/').pop()} ×</button>}
            {(s.filters.folders || []).length > 0 && <button className="chip on" onClick={() => s.patchFilters({ folders: [] })}>{(s.filters.folders || []).length} folders ×</button>}
            {s.filters.series && <button className="chip on" onClick={() => s.patchFilters({ series: null })}>{s.series.find(x => x.id === s.filters.series)?.name} ×</button>}
            {s.filters.tag && <button className="chip on" onClick={() => s.patchFilters({ tag: null })}>{s.filters.tag} ×</button>}
          </div>
        </div>
      )}
      <div key={s.activeTab} style={{ flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        {photo != null ? <PhotoPage id={photo} onBack={() => setPhoto(null)} />
          : board != null ? <div className="mboard"><BoardView /></div>
          : tab === 'lib' ? <MobileGrid selecting={selecting} onOpen={openPhoto} />
          : tab === 'folders' ? <FoldersViewMobile onOpen={() => { goTab('lib'); setCameFrom('folders') }} />
          : tab === 'series' ? <SeriesTab board={id => { s.setBoardSeries(id); s.setView('board'); setBoard(id) }} open={id => { s.patchFilters({ series: id, folder: null, tag: null, month: null }); s.setSort([{ f: 'custom', d: 'asc' }]); s.setGroup(false); goTab('lib'); setCameFrom('series') }} />
          : <WorkView />}
      </div>
      {selecting && s.sel.length > 0 && photo == null && (
        <div className="row" style={{ padding: '8px 14px', background: '#222226', borderTop: '1px solid var(--line)' }}>
          <span className="grow">{s.sel.length} selected</span>
          <button className="btn primary big" onClick={() => setSheet('edit')}>Rate & tag</button>
          <button className="btn big" onClick={() => s.editInDarktable(s.sel)}>darktable</button>
          {(() => { const ids = selectionIds(s.items.map(x => x.id), s.sel)  // only the selected photos
            return <EditOnPhone ids={ids} start={ids[0]} series={list.series} title={list.title} label="This phone" /> })()}
        </div>
      )}
      <nav className="mtabs">
        {([['lib', 'Library'], ['folders', 'Folders'], ['series', 'Series'], ['pc', 'Devices']] as [Tab, string][]).map(([t, l]) => (
          <button key={t} className={tab === t ? 'on' : ''} onClick={() => goTab(t)}><i />{l}</button>
        ))}
      </nav>
      {settings && <SettingsDialog onClose={() => setSettings(false)} />}
      {tabsOpen && <TabsSheet onClose={() => setTabsOpen(false)} />}
      {sheet === 'sort' && <Sheet title="Sort & filter" onClose={() => setSheet('')}><SortPanel /><div className="hr" /><FilterPanel /></Sheet>}
      {sheet === 'edit' && <Sheet title={`${s.selPhotos.length} photos`} onClose={() => setSheet('')}>
        <RatingLabels photos={s.selPhotos} big />
        <SeriesPicker ids={s.sel} />
        <TagPanel photos={s.selPhotos} />
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
          const title = tabTitle(t, s.series)
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
