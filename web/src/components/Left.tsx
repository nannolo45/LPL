import { useState } from 'react'
import { useStore } from '../store'
import { dirText, field, FIELDS, LABELS, PRESETS } from '../fields'

export function SortPanel() {
  const s = useStore()
  const levels = s.sort
  const set = (i: number, patch: Partial<{ f: string; d: 'asc' | 'desc' }>) => {
    const n = levels.slice(); n[i] = { ...n[i], ...patch } as any; s.setSort(n)
    if (patch.f === 'shuffle') s.reshuffle()
  }
  const add = () => {
    const used = levels.map(l => l.f)
    const next = ['lens', 'rating', 'name', 'camera', 'focal', 'taken', 'iso', 'aperture'].find(f => !used.includes(f)) || 'exposure'
    s.setSort([...levels, { f: next, d: 'asc' }])
  }
  const up = (i: number) => { if (!i) return; const n = levels.slice(); [n[i - 1], n[i]] = [n[i], n[i - 1]]; s.setSort(n) }
  const remove = (i: number) => { const n = levels.filter((_, k) => k !== i); s.setSort(n.length ? n : [{ f: 'taken', d: 'asc' }]) }
  const firstIsDate = field(levels[0]?.f).t === 'date'
  return (
    <div className="col">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <span className="caps">Sort by</span><span className="tiny muted">combine as many as you like</span>
      </div>
      {levels.map((lv, i) => (
        <div className="level" key={i}>
          <span className="pre">{i === 0 ? 'first' : 'then'}</span>
          <select className="input" aria-label={`Sort level ${i + 1}`} value={lv.f} onChange={e => set(i, { f: e.target.value })}>
            <optgroup label="darktable sorts">{FIELDS.filter(f => f.dt).map(f => <option key={f.v} value={f.v}>{f.l}</option>)}</optgroup>
            <optgroup label="extra in LightPhotoLibrary">{FIELDS.filter(f => !f.dt).map(f => <option key={f.v} value={f.v}>{f.l}</option>)}</optgroup>
          </select>
          <button className="btn dir" onClick={() => set(i, { d: lv.d === 'asc' ? 'desc' : 'asc' })}>{dirText(lv.f, lv.d)}</button>
          <button className="ic" aria-label="Move this level up" disabled={i === 0} onClick={() => up(i)}>↑</button>
          <button className="ic" aria-label="Remove this level" onClick={() => remove(i)}>×</button>
        </div>
      ))}
      <div className="row wrap">
        <button className="btn dashed" onClick={add}>+ then by…</button>
        {levels.some(l => l.f === 'shuffle') && <button className="btn ghost small" onClick={s.reshuffle}>reshuffle</button>}
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        {PRESETS.map(p => <button key={p.label} className="chip" onClick={() => s.setSort(p.sort)}>{p.label}</button>)}
      </div>
      <label className="row" style={{ cursor: 'pointer' }}>
        <input type="checkbox" checked={s.group} onChange={() => s.setGroup(!s.group)} style={{ accentColor: '#E8A33D' }} />
        <span>Group by the first level</span>
      </label>
      {s.group && firstIsDate && (
        <div className="row" style={{ paddingLeft: 24, gap: 4 }}>
          {(['day', 'month', 'year'] as const).map(g => <button key={g} className={'chip' + (s.gran === g ? ' on' : '')} onClick={() => s.setGran(g)}>{g}</button>)}
        </div>
      )}
    </div>
  )
}

export const RATINGS: [number, string][] = [[-1, 'rejected'], [0, 'unrated'], [1, '★1'], [2, '★2'], [3, '★3'], [4, '★4'], [5, '★5']]

export function toggle<T>(list: T[] | undefined, v: T): T[] {
  const l = list || []
  return l.includes(v) ? l.filter(x => x !== v) : [...l, v]
}

// A filter section that folds away; its open/closed state is remembered. When closed, the heading says what's selected.
function Section({ id, title, summary, right, defaultOpen = true, children }: {
  id: string; title: string; summary?: string; right?: React.ReactNode; defaultOpen?: boolean; children: React.ReactNode
}) {
  const [open, setOpen] = useState<boolean>(() => {
    const v = localStorage.getItem('lpl.sec.' + id)
    return v == null ? defaultOpen : v === '1'
  })
  const flip = () => { setOpen(!open); localStorage.setItem('lpl.sec.' + id, open ? '0' : '1') }
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <button className="sechead" aria-expanded={open} onClick={flip}>
          <span className="chev" style={{ transform: open ? 'rotate(90deg)' : 'none' }}>›</span>
          <span className="small soft">{title}</span>
          {!open && summary ? <span className="tiny" style={{ color: '#E8A33D' }}>{summary}</span> : null}
        </button>
        {right}
      </div>
      {open && children}
    </div>
  )
}

const picked = (n: number) => (n ? `${n} selected` : '')

export function FilterPanel() {
  const s = useStore()
  const f = s.filters
  const labels = f.labels || []
  const ratings = f.ratings || []
  const lenses = f.lenses || []
  const cameras = f.cameras || []
  return (
    <div className="col">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <span className="caps">Filters</span>
        <button className="btn ghost tiny" onClick={s.clearFilters}>reset</button>
      </div>
      <Section id="rating" title="Rating" summary={picked(ratings.length)} right={<span className="tiny muted">{ratings.length ? 'any of the selected' : 'all'}</span>}>
        <div className="row wrap" style={{ gap: 4 }}>
          {RATINGS.map(([v, l]) => <button key={v} className={'chip' + (ratings.includes(v) ? ' on' : '')} onClick={() => s.patchFilters({ ratings: toggle(ratings, v) })}>{l}</button>)}
          <button className="chip" title="everything except rejected" onClick={() => s.patchFilters({ ratings: [0, 1, 2, 3, 4, 5] })}>not rejected</button>
        </div>
      </Section>
      <Section id="labels" title="Color labels" summary={picked(labels.length)}
        right={<button className="chip" onClick={() => s.patchFilters({ label_mode: f.label_mode === 'and' ? 'or' : 'and' })}>photo has {f.label_mode === 'and' ? 'all of them' : 'any of them'}</button>}>
        <div className="row" style={{ gap: 8 }}>
          {LABELS.map(l => {
            const on = labels.includes(l.i)
            return <button key={l.i} aria-label={`Filter ${l.name}`} className={'dot' + (on ? ' on' : labels.length ? ' dim' : '')} style={{ background: l.c }}
              onClick={() => s.patchFilters({ labels: toggle(labels, l.i) })} />
          })}
        </div>
      </Section>
      <Section id="lens" title="Lens" defaultOpen={false} summary={picked(lenses.length)}
        right={lenses.length > 0 && <button className="btn ghost tiny" onClick={() => s.patchFilters({ lenses: [] })}>clear</button>}>
        <div className="row wrap" style={{ gap: 4 }}>
          {s.facets.lenses.map(l => <button key={l.v} className={'chip' + (lenses.includes(l.v) ? ' on' : '')} title={`${l.n} photos`} onClick={() => s.patchFilters({ lenses: toggle(lenses, l.v) })}>{l.v}</button>)}
          {!s.facets.lenses.length && <span className="tiny muted">no lens data yet</span>}
        </div>
      </Section>
      <Section id="camera" title="Camera" defaultOpen={false} summary={picked(cameras.length)}
        right={cameras.length > 0 && <button className="btn ghost tiny" onClick={() => s.patchFilters({ cameras: [] })}>clear</button>}>
        <div className="row wrap" style={{ gap: 4 }}>
          {s.facets.cameras.map(l => <button key={l.v} className={'chip' + (cameras.includes(l.v) ? ' on' : '')} title={`${l.n} photos`} onClick={() => s.patchFilters({ cameras: toggle(cameras, l.v) })}>{l.v}</button>)}
        </div>
      </Section>
      <Section id="darktable" title="darktable" summary={f.edited === 'yes' ? 'edited' : f.edited === 'no' ? 'not edited' : ''}>
        <div className="row" style={{ gap: 4 }}>
          {[['', 'all'], ['yes', 'edited'], ['no', 'not edited']].map(([v, l]) => (
            <button key={v} className={'chip' + ((f.edited || '') === v ? ' on' : '')} onClick={() => s.patchFilters({ edited: v as any })}>{l}</button>
          ))}
        </div>
      </Section>
      <div className="col" style={{ gap: 4 }}>
        <label className="row" style={{ cursor: 'pointer' }}>
          <input type="checkbox" checked={!!f.show_hidden} onChange={() => s.patchFilters({ show_hidden: !f.show_hidden, only_hidden: false })} style={{ accentColor: '#E8A33D' }} />
          <span className="small">Show hidden photos and folders{s.facets.hidden_photos ? ` (${s.facets.hidden_photos} photos)` : ''}</span>
        </label>
        {(f.show_hidden || f.only_hidden) && !!s.facets.hidden_photos && (
          <button className={'chip' + (f.only_hidden ? ' on' : '')} style={{ alignSelf: 'flex-start' }} onClick={() => s.patchFilters({ only_hidden: !f.only_hidden, show_hidden: true })}>only the hidden ones</button>
        )}
      </div>
      <span className="tiny muted">Within one filter, any selected value matches; different filters must all match.</span>
      <label className="row" style={{ cursor: 'pointer' }}>
        <input type="checkbox" checked={f.subfolders !== false} onChange={() => s.patchFilters({ subfolders: f.subfolders === false })} style={{ accentColor: '#E8A33D' }} />
        <span className="small">Include subfolders when a folder is open</span>
      </label>
    </div>
  )
}

export function LeftPanel() {
  return (
    <div className="left">
      <SortPanel />
      <div className="hr" />
      <FilterPanel />
    </div>
  )
}
