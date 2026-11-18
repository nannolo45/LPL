import { useEffect, useState } from 'react'
import { api, coverUrl, Photo } from '../api'
import { useStore } from '../store'
import { exifLine, fmtDay, stars } from '../fields'
import { FilterPanel, SortPanel } from './Left'
import { RatingLabels, SeriesPicker, TagPanel } from './Inspector'
import { Thumb, useGroups } from './Library'
import { FoldersView } from './Folders'
import { WorkView } from './Work'

type Tab = 'lib' | 'folders' | 'series' | 'pc'

function Sheet({ onClose, title, children }: { onClose: () => void; title: string; children: React.ReactNode }) {
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
              <button key={p.id} className={selSet.has(p.id) ? 'sel' : ''} style={{ opacity: p.rating < 0 ? .45 : 1 }} aria-label={p.filename}
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
  useEffect(() => { if (idx < 0) api.photo(id).then(setExtra).catch(() => {}) }, [id, idx])
  const p = idx >= 0 ? s.items[idx] : extra
  if (!p) return null
  const nav = (d: number) => { const n = s.items[idx + d]; if (n) s.setSel([n.id]) }
  return (
    <div className="scroll" style={{ padding: 0 }}>
      <div style={{ background: '#0f0f10', height: '42vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Thumb p={p} size="l" style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }} />
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
        <button className="btn primary big" onClick={() => s.editInDarktable([p.id])}>Edit in darktable</button>
        <span className="small muted" style={{ marginTop: -6 }}>Opens on your PC (or as soon as it's online). Ratings, labels and tags are saved to the XMP right away.</span>
        <SeriesPicker ids={[p.id]} />
        <TagPanel photos={[p]} />
        <button className="btn big" onClick={onBack}>Back to library</button>
      </div>
    </div>
  )
}

function SeriesTab({ open }: { open: (id: number) => void }) {
  const s = useStore()
  return (
    <div className="scroll">
      <div className="col" style={{ padding: '0 14px', gap: 12 }}>
        {s.series.map(x => (
          <button key={x.id} className="card" style={{ border: 0, textAlign: 'left', color: 'inherit' }} onClick={() => open(x.id)}>
            <div className="row"><b className="grow">{x.name}</b><span className="muted">{x.count}</span></div>
            <div className="row" style={{ gap: 3, overflow: 'hidden' }}>
              {x.covers.map(c => <img key={c.id} src={coverUrl(c)} alt="" style={{ width: 56, height: 56, objectFit: 'cover', borderRadius: 4 }} />)}
            </div>
          </button>
        ))}
        {!s.series.length && <span className="muted">No series yet. Select photos and use “Add to series”.</span>}
        <span className="small muted">Arranging a series on the free-form board works on a tablet or computer. Here you see it in board order.</span>
      </div>
    </div>
  )
}

export function MobileApp() {
  const s = useStore()
  const [tab, setTab] = useState<Tab>('lib')
  const [sheet, setSheet] = useState<'' | 'sort' | 'edit'>('')
  const [selecting, setSelecting] = useState(false)
  const [photo, setPhoto] = useState<number | null>(null)
  useEffect(() => { if (photo != null && s.sel.length === 1 && s.sel[0] !== photo) setPhoto(s.sel[0]) }, [s.sel])
  useEffect(() => { if (['folders', 'timeline', 'board', 'work', 'list'].includes(s.view) && tab === 'lib') s.setView('grid') }, [])
  const openPhoto = (p: Photo) => { s.setSel([p.id]); setPhoto(p.id) }
  const goTab = (t: Tab) => { setTab(t); setPhoto(null); if (t === 'lib') s.setView('grid'); if (t === 'folders') s.setView('folders') }
  const title = photo != null ? 'Photo' : tab === 'lib' ? 'Library' : tab === 'folders' ? 'Folders' : tab === 'series' ? 'Series' : 'PC'
  const fcount = [s.filters.folder, s.filters.month, s.filters.tag, s.filters.series, s.filters.edited, (s.filters.lenses || []).length, (s.filters.cameras || []).length, (s.filters.labels || []).length, (s.filters.ratings || []).length].filter(Boolean).length
  return (
    <div className="app">
      <div className="mhead">
        {photo != null && <button className="btn ghost big" onClick={() => setPhoto(null)} aria-label="Back">‹</button>}
        <span className="display">{title}</span>
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
            {s.filters.series && <button className="chip on" onClick={() => s.patchFilters({ series: null })}>{s.series.find(x => x.id === s.filters.series)?.name} ×</button>}
            {s.filters.tag && <button className="chip on" onClick={() => s.patchFilters({ tag: null })}>{s.filters.tag} ×</button>}
          </div>
        </div>
      )}
      <div style={{ flex: '1 1 auto', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        {photo != null ? <PhotoPage id={photo} onBack={() => setPhoto(null)} />
          : tab === 'lib' ? <MobileGrid selecting={selecting} onOpen={openPhoto} />
          : tab === 'folders' ? <FoldersViewMobile onOpen={() => goTab('lib')} />
          : tab === 'series' ? <SeriesTab open={id => { s.patchFilters({ series: id, folder: null, tag: null, month: null }); s.setSort([{ f: 'custom', d: 'asc' }]); s.setGroup(false); goTab('lib') }} />
          : <WorkView />}
      </div>
      {selecting && s.sel.length > 0 && photo == null && (
        <div className="row" style={{ padding: '8px 14px', background: '#222226', borderTop: '1px solid var(--line)' }}>
          <span className="grow">{s.sel.length} selected</span>
          <button className="btn primary big" onClick={() => setSheet('edit')}>Rate & tag</button>
          <button className="btn big" onClick={() => s.editInDarktable(s.sel)}>darktable</button>
        </div>
      )}
      <nav className="mtabs">
        {([['lib', 'Library'], ['folders', 'Folders'], ['series', 'Series'], ['pc', 'PC']] as [Tab, string][]).map(([t, l]) => (
          <button key={t} className={tab === t ? 'on' : ''} onClick={() => goTab(t)}><i />{l}</button>
        ))}
      </nav>
      {sheet === 'sort' && <Sheet title="Sort & filter" onClose={() => setSheet('')}><SortPanel /><div className="hr" /><FilterPanel /></Sheet>}
      {sheet === 'edit' && <Sheet title={`${s.selPhotos.length} photos`} onClose={() => setSheet('')}>
        <RatingLabels photos={s.selPhotos} big />
        <SeriesPicker ids={s.sel} />
        <TagPanel photos={s.selPhotos} />
      </Sheet>}
    </div>
  )
}

function FoldersViewMobile({ onOpen }: { onOpen: () => void }) {
  const s = useStore()
  // FoldersView switches to view 'grid' when a folder is opened; follow it back to the Library tab
  useEffect(() => { if (s.view === 'grid') onOpen() }, [s.view])
  return <FoldersView />
}
