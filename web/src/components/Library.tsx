import { useEffect, useMemo, useRef, useState } from 'react'
import { Photo, thumbUrl } from '../api'
import { useStore } from '../store'
import { RetryImg } from './RetryImg'
import { captionValue, field, fmtMonth, groupLabel, LABELS, leaf, stars } from '../fields'

export function Thumb({ p, size = 't', className, style }: { p: Photo; size?: 't' | 'l'; className?: string; style?: React.CSSProperties }) {
  if (p.preview_state < 0) return <div className="ph">no preview</div>
  return <RetryImg className={className} style={style} src={thumbUrl(p, size)} alt={p.filename} placeholder={<div className="ph">preview…</div>} />
}

export function useGroups(items: Photo[]) {
  const s = useStore()
  return useMemo(() => {
    const l1 = s.sort[0]
    const canGroup = s.group && l1 && field(l1.f).t !== 'none'
    const groups: { label: string; items: Photo[] }[] = []
    for (const p of items) {
      const lab = canGroup ? groupLabel(p, l1.f, s.gran) : ''
      const last = groups[groups.length - 1]
      if (!last || last.label !== lab) groups.push({ label: lab, items: [p] })
      else last.items.push(p)
    }
    return { groups, canGroup }
  }, [items, s.sort, s.group, s.gran])
}

function Sentinel() {
  const s = useStore()
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) s.loadMore() }, { rootMargin: '800px' })
    io.observe(el)
    return () => io.disconnect()
  }, [s.loadMore])
  return <div ref={ref} style={{ height: 1 }} />
}

export function ContextBar() {
  const s = useStore()
  const f = s.filters
  const chips: { label: string; clear: () => void }[] = []
  if (f.folder) chips.push({ label: 'folder: ' + f.folder, clear: () => s.patchFilters({ folder: null }) })
  if (f.folders && f.folders.length) chips.push({ label: f.folders.length === 1 ? 'folder: ' + f.folders[0] : `${f.folders.length} folders: ` + f.folders.map(x => x.split('/').pop()).join(', '), clear: () => s.patchFilters({ folders: [] }) })
  if (f.month) chips.push({ label: 'month: ' + fmtMonth(f.month + '-01'), clear: () => s.patchFilters({ month: null }) })
  if (f.tag) chips.push({ label: 'tag: ' + f.tag, clear: () => s.patchFilters({ tag: null }) })
  if (f.series) {
    const se = s.series.find(x => x.id === f.series)
    chips.push({ label: 'series: ' + (se?.name || f.series), clear: () => s.patchFilters({ series: null }) })
  }
  return (
    <div className="ctxbar">
      <b>{s.total.toLocaleString()}</b><span className="muted">photos</span>
      {s.sel.length > 0 && <span className="muted">· {s.sel.length} selected</span>}
      {chips.map(c => <button key={c.label} className="chip on" onClick={c.clear}>{c.label} ×</button>)}
      <div className="grow" />
      {!s.isMobile && <span className="tiny muted ellipsis" style={{ maxWidth: 380 }}>sorted: {s.sort.map(l => field(l.f).l + (l.d === 'asc' ? ' ↑' : ' ↓')).join(' › ')}</span>}
      {!s.isMobile && s.view === 'grid' && (
        <input type="range" min={110} max={320} value={s.thumb} aria-label="Thumbnail size" onChange={e => s.setThumb(+e.target.value)} style={{ width: 90, accentColor: '#E8A33D' }} />
      )}
      <button className="btn" onClick={s.selectAll}>select all</button>
      <button className="btn" onClick={() => s.setSel([])}>none</button>
    </div>
  )
}

// after “Show in library”: scroll the photo into the middle of the view and make it blink once
function useReveal() {
  const s = useStore()
  useEffect(() => {
    if (s.revealId == null) return
    const id = s.revealId
    const t = window.setTimeout(() => {
      const el = document.querySelector(`[data-pid="${id}"]`) as HTMLElement | null
      if (el) {
        el.scrollIntoView({ block: 'center' })
        el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash')
      }
      s.clearReveal()
    }, 60)
    return () => window.clearTimeout(t)
  }, [s.revealId, s.items])
}

export function GridView() {
  const s = useStore()
  useReveal()
  const { groups, canGroup } = useGroups(s.items)
  const capFields = (canGroup ? s.sort.slice(1, 3) : s.sort.slice(0, 2)).map(l => l.f)
  const selSet = new Set(s.sel)
  return (
    <div className="scroll" style={{ ['--thumb' as any]: s.thumb + 'px' }}>
      {groups.map((g, gi) => (
        <div className="group" key={gi + g.label}>
          {canGroup && <div className="group-h"><span className="display">{g.label}</span><span className="small muted">{g.items.length}{g.items.length === 1 ? ' photo' : ' photos'}</span></div>}
          <div className="grid">
            {g.items.map(p => (
              <div key={p.id} data-pid={p.id} className={'tile' + (selSet.has(p.id) ? ' sel' : '') + (p.rating < 0 ? ' rej' : '')}>
                <button className="img" onClick={e => s.clickSelect(p.id, e)} onDoubleClick={() => s.setViewer(p.id)} aria-label={p.filename}>
                  <Thumb p={p} />
                  <span className="badges">{p.labels.map(l => <span key={l} style={{ background: LABELS[l].c }} />)}</span>
                  {p.rating > 0 && <span className="stars">{stars(p.rating)}</span>}
                  {!!p.edited && <span className="dt">dt</span>}
                </button>
                <span className="name ellipsis">{p.filename}</span>
                <span className="cap ellipsis">{capFields.map(f => captionValue(p, f)).filter(Boolean).join(' · ')}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
      {!s.loading && s.items.length === 0 && <div className="muted" style={{ padding: 40, textAlign: 'center' }}>No photos match these filters.</div>}
      <Sentinel />
      {s.loading && <div className="muted small" style={{ padding: 16, textAlign: 'center' }}>loading…</div>}
    </div>
  )
}

const COLS: [string, string][] = [['name', 'filename'], ['taken', 'taken'], ['path', 'folder'], ['camera', 'camera'], ['lens', 'lens'], ['focal', 'focal'], ['aperture', 'f'], ['exposure', 'shutter'], ['iso', 'iso'], ['rating', 'rating'], ['label', 'labels'], ['tag', 'tags'], ['edited', 'dt']]

export function ListView() {
  const s = useStore()
  useReveal()
  const { groups, canGroup } = useGroups(s.items)
  const selSet = new Set(s.sel)
  const sortBy = (f: string) => {
    const cur = s.sort
    if (cur[0]?.f === f) s.setSort([{ f, d: cur[0].d === 'asc' ? 'desc' : 'asc' }, ...cur.slice(1)])
    else s.setSort([{ f, d: 'asc' }, ...cur.filter(l => l.f !== f)])
  }
  const addLevel = (f: string) => { if (!s.sort.some(l => l.f === f)) s.setSort([...s.sort, { f, d: 'asc' }]) }
  return (
    <div className="scroll">
      <div className="list">
        <table>
          <thead>
            <tr>
              <th />
              {COLS.map(([f, l]) => {
                const i = s.sort.findIndex(x => x.f === f)
                return (
                  <th key={f} className={i >= 0 ? 'on' : ''}>
                    <button onClick={() => sortBy(f)} title="Sort by this column">{l} {i >= 0 ? `${i + 1}${s.sort[i].d === 'asc' ? '↑' : '↓'}` : ''}</button>
                    <button className="add" onClick={() => addLevel(f)} aria-label={`Add ${l} as next sort level`}>+</button>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {groups.map((g, gi) => [
              canGroup ? <tr className="gh" key={'g' + gi}><td colSpan={COLS.length + 1}>{g.label} <span className="muted">· {g.items.length}</span></td></tr> : null,
              ...g.items.map(p => (
                <tr key={p.id} data-pid={p.id} className={selSet.has(p.id) ? 'sel' : ''} onClick={e => s.clickSelect(p.id, e)} onDoubleClick={() => s.setViewer(p.id)} style={{ cursor: 'pointer', opacity: p.rating < 0 ? .5 : 1 }}>
                  <td><Thumb p={p} /></td>
                  <td>{p.filename}</td>
                  <td>{p.taken ? p.taken.replace('T', ' ').slice(0, 16) : ''}</td>
                  <td title={p.folder}>{p.folder}</td>
                  <td>{p.camera}</td>
                  <td title={p.lens || ''}>{p.lens}</td>
                  <td>{captionValue(p, 'focal')}</td>
                  <td>{captionValue(p, 'aperture')}</td>
                  <td>{captionValue(p, 'exposure')}</td>
                  <td>{p.iso || ''}</td>
                  <td style={{ color: '#f3e3b0' }}>{stars(p.rating)}</td>
                  <td>{p.labels.map(l => <span key={l} style={{ display: 'inline-block', width: 9, height: 9, borderRadius: 5, background: LABELS[l].c, marginRight: 3 }} />)}</td>
                  <td title={p.tags.join(', ')}>{p.tags.map(leaf).join(', ')}</td>
                  <td>{p.edited ? '●' : ''}</td>
                </tr>
              )),
            ])}
          </tbody>
        </table>
      </div>
      <Sentinel />
    </div>
  )
}
