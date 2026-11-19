import { useEffect, useMemo, useState } from 'react'
import { api, Photo } from '../api'
import { useStore } from '../store'
import { keysOf, useShortcutsVersion } from '../shortcuts'
import { exifLine, fmtDay, LABELS, leaf } from '../fields'
import { Thumb } from './Library'

export function RatingLabels({ photos, big = false }: { photos: Photo[]; big?: boolean }) {
  const s = useStore()
  const ids = photos.map(p => p.id)
  const r = photos.length && photos.every(p => p.rating === photos[0].rating) ? photos[0].rating : null
  return (
    <div className="col" style={{ gap: big ? 12 : 8 }}>
      <div className="row" style={{ gap: 2 }}>
        {[1, 2, 3, 4, 5].map(n => (
          <button key={n} className={'starbtn' + (r !== null && r >= n ? ' on' : '')} style={big ? { fontSize: 28, width: 44, height: 44 } : undefined}
            aria-label={`Rate ${n} stars`} onClick={() => s.meta({ rating: r === n && photos.length === 1 ? 0 : n }, ids)}>★</button>
        ))}
        <button className="btn" style={{ marginLeft: 6 }} onClick={() => s.meta({ rating: 0 }, ids)}>0</button>
        <button className={'btn' + (r === -1 ? ' danger' : '')} onClick={() => s.meta({ rating: r === -1 ? 0 : -1 }, ids)}>reject</button>
      </div>
      <div className="row" style={{ gap: big ? 12 : 8 }}>
        {LABELS.map(l => {
          const all = photos.length > 0 && photos.every(p => p.labels.includes(l.i))
          const some = !all && photos.some(p => p.labels.includes(l.i))
          return <button key={l.i} aria-label={`Toggle ${l.name} label`} className={'dot' + (all ? ' on' : some ? '' : ' dim')}
            style={{ background: l.c, ...(big ? { width: 36, height: 36, borderWidth: 3 } : {}) }} onClick={() => s.meta({ toggle_label: l.i }, ids)} />
        })}
      </div>
    </div>
  )
}

export function SeriesPicker({ ids }: { ids: number[] }) {
  const s = useStore()
  const add = async (v: string) => {
    if (!v) return
    try {
      if (v === '__new') {
        const name = window.prompt('Name of the new series')
        if (!name) return
        const r = await api.seriesCreate(name, ids)
        s.toast(`Series “${name}” created with ${ids.length} photo${ids.length > 1 ? 's' : ''}`)
        s.setBoardSeries(r.id)
      } else {
        const r = await api.seriesAdd(+v, ids)
        s.toast(`${r.added} added to ${s.series.find(x => x.id === +v)?.name}`)
      }
      s.reloadSeries(); s.reloadTags()
      api.get(ids).then(r => s.mergePhotos(r.items))
    } catch (e: any) { s.toast(e.message) }
  }
  return (
    <select className="input" value="" onChange={e => add(e.target.value)} aria-label="Add to series">
      <option value="">Add to series…</option>
      {s.series.map(x => <option key={x.id} value={x.id}>{x.name} ({x.count})</option>)}
      <option value="__new">+ new series…</option>
    </select>
  )
}

interface Node { path: string; name: string; depth: number; isTag: boolean; kids: boolean; n: number; syn: string; category: boolean; priv: boolean }

/** Hide the photos from the library views, or show them again (only in LPL; the files and XMPs are untouched). */
export function HideButton({ photos, big }: { photos: Photo[]; big?: boolean }) {
  const s = useStore()
  useShortcutsVersion()
  if (!photos.length) return null
  const all = photos.every(p => p.hidden)
  const k = keysOf('hide')[0]
  const what = photos.length > 1 ? `${photos.length} photos` : 'this photo'
  return (
    <button className={'btn' + (big ? ' big' : '')} title="Only in LightPhotoLibrary: the files and their XMPs stay as they are. Turn on “Show hidden photos” in Filters to see them again."
      onClick={() => s.hidePhotos(photos.map(p => p.id), !all)}>
      {all ? `Show ${what} again` : `Hide ${what} from the library`}{k && !big ? ` (${k})` : ''}
    </button>
  )
}

export function TagPanel({ photos }: { photos: Photo[] }) {
  const s = useStore()
  const [q, setQ] = useState('')
  const [sugg, setSugg] = useState<string[]>([])
  const [exp, setExp] = useState<Record<string, 1>>(() => { try { return JSON.parse(localStorage.getItem('lpl.tagexp') || '{}') } catch { return {} } })
  const [menu, setMenu] = useState<{ x: number; y: number; node: Node } | null>(null)
  useEffect(() => { localStorage.setItem('lpl.tagexp', JSON.stringify(exp)) }, [exp])
  // the list of all tags folds away (remembered; folded by default on a phone). Searching still shows matches.
  const [treeOpen, setTreeOpen] = useState<boolean>(() => { const v = localStorage.getItem('lpl.sec.tagtree'); return v == null ? !s.isMobile : v === '1' })
  const flipTree = () => { localStorage.setItem('lpl.sec.tagtree', treeOpen ? '0' : '1'); setTreeOpen(!treeOpen) }
  const ids = photos.map(p => p.id)
  const idKey = ids.join(',')
  useEffect(() => {
    if (!ids.length) { setSugg([]); return }
    const t = window.setTimeout(() => api.tagSuggest(ids).then(r => setSugg(r.tags)).catch(() => {}), 250)
    return () => window.clearTimeout(t)
  }, [idKey, s.tags])

  const mark = (path: string) => {
    if (!photos.length) return ''
    const n = photos.filter(p => p.tags.includes(path)).length
    return n === photos.length ? '✓' : n ? '–' : ''
  }
  const attached = useMemo(() => Array.from(new Set(photos.flatMap(p => p.tags))).sort(), [photos])

  const nodes = useMemo(() => {
    const map = new Map<string, Node>()
    const count = (path: string) => s.tags.filter(t => t.path === path || t.path.startsWith(path + '|')).reduce((a, t) => a + t.n, 0)
    for (const t of s.tags) {
      const parts = t.path.split('|')
      for (let k = 1; k <= parts.length; k++) {
        const path = parts.slice(0, k).join('|')
        if (!map.has(path)) map.set(path, { path, name: parts[k - 1], depth: k - 1, isTag: false, kids: false, n: 0, syn: '', category: false, priv: false })
        const node = map.get(path)!
        if (k < parts.length) node.kids = true
      }
      const node = map.get(t.path)!
      node.isTag = true; node.syn = t.synonyms; node.category = !!t.category; node.priv = !!t.private
    }
    map.forEach(n => (n.n = count(n.path)))
    return Array.from(map.values()).sort((a, b) => a.path.localeCompare(b.path))
  }, [s.tags])

  const ql = q.trim().toLowerCase()
  const visible = ql
    ? nodes.filter(n => n.isTag && (n.path.toLowerCase().includes(ql) || n.syn.toLowerCase().includes(ql)))
    : nodes.filter(n => { const parts = n.path.split('|'); for (let k = 1; k < parts.length; k++) if (!exp[parts.slice(0, k).join('|')]) return false; return true })

  const attach = (t: string) => {
    if (!photos.length) { s.patchFilters({ tag: t }); if (!['grid', 'list'].includes(s.view)) s.setView('grid'); return }
    s.meta({ attach: [t] }, ids)
  }
  const detach = (t: string) => s.meta({ detach: [t] }, ids)
  const toggle = (path: string) => setExp(e => { const n = { ...e }; if (n[path]) delete n[path]; else n[path] = 1; return n })
  const submit = async () => {
    const t = q.trim().replace(/^\|+|\|+$/g, '')
    if (!t) return
    if (photos.length) await s.meta({ attach: [t] }, ids)
    else { await api.tagCreate(t); s.reloadTags(); s.toast(`Tag “${t}” created`) }
    setQ('')
  }
  const act = async (kind: string, n: Node) => {
    setMenu(null)
    try {
      if (kind === 'rename') {
        const nw = window.prompt('Rename or move this tag. Use | for levels.\nEverything below it moves too.', n.path)
        if (!nw || nw === n.path) return
        const r = await api.tagRename(n.path, nw)
        s.toast(`Renamed on ${r.photos} photos`)
      } else if (kind === 'delete') {
        if (!window.confirm(`Delete “${n.path}” and every tag below it from all ${n.n} photos? This rewrites their XMP files.`)) return
        const r = await api.tagDelete(n.path)
        s.toast(`Removed from ${r.photos} photos`)
      } else if (kind === 'syn') {
        const v = window.prompt('Synonyms (comma separated). They help search, e.g. Lisboa for Lisbon.', n.syn)
        if (v === null) return
        await api.tagProps(n.path, { synonyms: v })
      } else if (kind === 'cat') {
        await api.tagProps(n.path, { category: !n.category })
      } else if (kind === 'priv') {
        await api.tagProps(n.path, { private: !n.priv })
      } else if (kind === 'filter') {
        s.patchFilters({ tag: n.path }); if (!['grid', 'list'].includes(s.view)) s.setView('grid')
      } else if (kind === 'create') {
        const v = window.prompt('New tag below ' + n.path, n.path + '|')
        if (v) await api.tagCreate(v)
      }
      s.reloadTags()
      s.reload()
    } catch (e: any) { s.toast(e.message) }
  }

  return (
    <div className="col">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <span className="caps">Tags</span>
        <span className="tiny muted">{photos.length ? `on ${photos.length} selected` : 'click a tag to filter'}</span>
      </div>
      {attached.length > 0 && (
        <div className="row wrap" style={{ gap: 6 }}>
          {attached.map(t => (
            <span key={t} className={'tagchip' + (mark(t) === '✓' ? '' : ' some')} title={t}>
              {mark(t) !== '✓' && <span className="muted tiny">some ·</span>}{leaf(t)}
              <button aria-label={`Detach ${t}`} onClick={() => detach(t)}>×</button>
            </span>
          ))}
        </div>
      )}
      <div className="row" style={{ gap: 6 }}>
        <input className="input grow" value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') submit() }}
          placeholder="find or create · use | for levels" aria-label="Find or create a tag" />
        <button className="btn" onClick={submit}>{q.trim() && !s.tags.some(t => t.path === q.trim()) ? 'create' : 'attach'}</button>
      </div>
      {sugg.length > 0 && photos.length > 0 && (
        <div className="row wrap" style={{ gap: 5 }}>
          <span className="tiny muted">suggested</span>
          {sugg.map(t => <button key={t} className="chip" title={t} onClick={() => attach(t)}>+ {leaf(t)}</button>)}
        </div>
      )}
      <button className="sechead" aria-expanded={treeOpen} onClick={flipTree}>
        <span className="chev" style={{ transform: treeOpen ? 'rotate(90deg)' : 'none' }}>›</span>
        <span className="small soft">All tags</span>
        <span className="tiny muted">{s.tags.length}{!treeOpen && ql ? ' · showing matches' : ''}</span>
      </button>
      {(treeOpen || ql) && <div className="tree">
        {visible.map(n => {
          const m = n.isTag ? mark(n.path) : ''
          const open = !!exp[n.path]
          return (
            <div className="node" key={n.path} style={{ paddingLeft: ql ? 6 : 4 + n.depth * 14 }}>
              {!ql && <button className="chev" style={{ visibility: n.kids ? 'visible' : 'hidden' }} aria-label={(open ? 'Collapse ' : 'Expand ') + n.name} onClick={() => toggle(n.path)}>{open ? '▾' : '▸'}</button>}
              <button className={'lab' + (m === '✓' ? ' on' : '') + (n.isTag ? '' : ' free')}
                onClick={() => (!n.isTag ? toggle(n.path) : m === '✓' ? detach(n.path) : attach(n.path))}
                title={n.isTag ? (photos.length ? (m === '✓' ? 'detach' : 'attach') : 'show photos with this tag') : 'folder-only node'}>
                <span className="ellipsis">{ql ? n.path : n.name}</span>
                {n.syn && <span className="tiny muted ellipsis">= {n.syn}</span>}
                {n.category && <span className="tiny muted">cat</span>}
                {n.priv && <span className="tiny muted">priv</span>}
                <span className="grow" />
                <span className="tiny muted">{n.n || ''}</span>
                <span className="mark">{m}</span>
              </button>
              <button className="more" aria-label={`More actions for ${n.path}`} onClick={e => setMenu({ x: Math.min(e.clientX, window.innerWidth - 200), y: Math.min(e.clientY, window.innerHeight - 260), node: n })}>⋯</button>
            </div>
          )
        })}
        {!visible.length && <div className="small muted" style={{ padding: 10 }}>{ql ? 'No match. Press Enter to create it.' : 'No tags yet.'}</div>}
      </div>}
      {menu && (
        <>
          <div className="backdrop" style={{ background: 'transparent' }} onClick={() => setMenu(null)} />
          <div className="menu" style={{ left: menu.x, top: menu.y }}>
            <button onClick={() => act('filter', menu.node)}>Show photos with this tag</button>
            <button onClick={() => act('create', menu.node)}>New tag below…</button>
            <button onClick={() => act('rename', menu.node)}>Rename / change path…</button>
            <button onClick={() => act('syn', menu.node)}>Synonyms…</button>
            <button onClick={() => act('cat', menu.node)}>{menu.node.category ? 'Unset' : 'Set as'} category</button>
            <button onClick={() => act('priv', menu.node)}>{menu.node.priv ? 'Unset' : 'Set as'} private</button>
            <button onClick={() => act('delete', menu.node)} style={{ color: '#ff8b8e' }}>Delete tag…</button>
          </div>
        </>
      )}
    </div>
  )
}

export function Inspector() {
  const s = useStore()
  useShortcutsVersion()
  const photos = s.selPhotos
  const one = photos.length === 1 ? photos[0] : null
  return (
    <div className="right">
      {!photos.length && (
        <div className="card soft" style={{ lineHeight: 1.5 }}>
          Click a photo to select it. Ctrl-click adds, Shift-click selects a range. Double-click opens it large.
          <br /><br />Keys: {keysOf('rate0')[0] ?? '–'}–{keysOf('rate5')[0] ?? '–'} rating, {keysOf('reject')[0] ?? '–'} reject, {keysOf('label0')[0] ?? '–'}–{keysOf('label4')[0] ?? '–'} labels, {keysOf('edit')[0] ?? '–'} edit in darktable. Change them with the keyboard button at the top.
        </div>
      )}
      {one && (
        <>
          <Thumb p={one} size="l" className="preview" />
          <div className="col" style={{ gap: 2 }}>
            <b style={{ fontSize: 15 }} className="ellipsis">{one.filename}</b>
            <div className="row" style={{ gap: 6 }}>
              <span className="small muted ellipsis grow">{one.folder || '/'}</span>
              {!(s.filters.folder === one.folder && (s.view === 'grid' || s.view === 'list')) &&
                <button className="btn ghost tiny" title="Open its folder in the Library, scrolled to this photo" onClick={() => s.reveal(one.id)}>Show in folder</button>}
            </div>
          </div>
          <div className="kv">
            <span>Taken</span><span>{one.taken ? fmtDay(one.taken) + ' ' + one.taken.slice(11, 16) : '—'}</span>
            <span>Camera</span><span>{one.camera || '—'}</span>
            <span>Lens</span><span>{one.lens || '—'}</span>
            <span>Exposure</span><span>{exifLine(one) || '—'}</span>
            <span>darktable</span><span>{one.edited ? 'edited' : 'not edited'}</span>
            {one.title && <><span>Title</span><span>{one.title}</span></>}
          </div>
        </>
      )}
      {photos.length > 1 && (
        <>
          <b style={{ fontSize: 15 }}>{photos.length} photos selected</b>
          <div className="row wrap" style={{ gap: 4 }}>
            {photos.slice(0, 15).map(p => <div key={p.id} style={{ width: 52, height: 35, overflow: 'hidden', borderRadius: 3, background: '#222226' }}><Thumb p={p} style={{ width: '100%', height: '100%', objectFit: 'cover' }} /></div>)}
          </div>
        </>
      )}
      {photos.length > 0 && (
        <>
          <RatingLabels photos={photos} />
          <button className="btn primary" onClick={() => s.editInDarktable(photos.map(p => p.id))}>
            {photos.length > 1 ? `Edit ${photos.length} in darktable` : 'Edit in darktable'}
          </button>
          {s.isLocal && <button className="btn" onClick={() => { s.setSendIds(photos.map(p => p.id)); s.setView('send') }}>
            Send {photos.length > 1 ? `${photos.length} photos` : 'this photo'} to the NAS…
          </button>}
          <SeriesPicker ids={photos.map(p => p.id)} />
          <HideButton photos={photos} />
        </>
      )}
      <div className="hr" />
      <TagPanel photos={photos} />
    </div>
  )
}
