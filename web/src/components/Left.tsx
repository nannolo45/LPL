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

const RATINGS = [['all', 'all'], ['notrej', 'not rejected'], ['r1', '★1+'], ['r2', '★2+'], ['r3', '★3+'], ['r4', '★4+'], ['r5', '★5'], ['unr', 'unrated'], ['rej', 'rejected']]

export function FilterPanel() {
  const s = useStore()
  const f = s.filters
  const labels = f.labels || []
  return (
    <div className="col">
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <span className="caps">Filters</span>
        <button className="btn ghost tiny" onClick={s.clearFilters}>reset</button>
      </div>
      <span className="small soft">Rating</span>
      <div className="row wrap" style={{ gap: 4 }}>
        {RATINGS.map(([v, l]) => <button key={v} className={'chip' + ((f.rating || 'all') === v ? ' on' : '')} onClick={() => s.patchFilters({ rating: v })}>{l}</button>)}
      </div>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <span className="small soft">Color labels</span>
        <button className="chip" onClick={() => s.patchFilters({ label_mode: f.label_mode === 'and' ? 'or' : 'and' })}>match {f.label_mode === 'and' ? 'all' : 'any'}</button>
      </div>
      <div className="row" style={{ gap: 8 }}>
        {LABELS.map(l => {
          const on = labels.includes(l.i)
          return <button key={l.i} aria-label={`Filter ${l.name}`} className={'dot' + (on ? ' on' : labels.length ? ' dim' : '')} style={{ background: l.c }}
            onClick={() => s.patchFilters({ labels: on ? labels.filter(x => x !== l.i) : [...labels, l.i] })} />
        })}
      </div>
      <label className="col" style={{ gap: 4 }}><span className="small soft">Lens</span>
        <select className="input" value={f.lens || ''} onChange={e => s.patchFilters({ lens: e.target.value })}>
          <option value="">all lenses</option>
          {s.facets.lenses.map(l => <option key={l.v} value={l.v}>{l.v} ({l.n})</option>)}
        </select>
      </label>
      <label className="col" style={{ gap: 4 }}><span className="small soft">Camera</span>
        <select className="input" value={f.camera || ''} onChange={e => s.patchFilters({ camera: e.target.value })}>
          <option value="">all cameras</option>
          {s.facets.cameras.map(l => <option key={l.v} value={l.v}>{l.v} ({l.n})</option>)}
        </select>
      </label>
      <span className="small soft">darktable</span>
      <div className="row" style={{ gap: 4 }}>
        {[['', 'all'], ['yes', 'edited'], ['no', 'not edited']].map(([v, l]) => (
          <button key={v} className={'chip' + ((f.edited || '') === v ? ' on' : '')} onClick={() => s.patchFilters({ edited: v as any })}>{l}</button>
        ))}
      </div>
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
