import { useEffect, useMemo, useRef, useState } from 'react'
import { AddTarget, api, coverUrl, Filters, Frame, LaneInfo, LastAdd, MatchResult, Target } from '../api'
import { useStore } from '../store'
import { LABELS } from '../fields'
import { RATINGS, toggle } from './Left'
import { FolderPicker } from './FolderPicker'
import { RetryImg } from './RetryImg'

// Criteria the panel edits itself; anything else carried over from the library is shown as one removable line.
const OWN = ['names', 'folders', 'subfolders', 'ratings', 'labels', 'label_mode', 'tags', 'tag_mode', 'date_from', 'date_to', 'edited'] as const
const EMPTY: Filters = { names: '', folders: [], subfolders: true, ratings: [], labels: [], label_mode: 'or', tags: [], tag_mode: 'or', date_from: null, date_to: null, edited: '' }

function clean(f: Filters): Filters {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(f)) {
    if (v == null || v === '' || (Array.isArray(v) && !v.length)) continue
    out[k] = v
  }
  return out as Filters
}
const hasOther = (f: Filters) => Object.keys(clean(f)).some(k => !['names', 'subfolders', 'label_mode', 'tag_mode'].includes(k))
const hasCriteria = (f: Filters) => Object.keys(clean(f)).some(k => !['subfolders', 'label_mode', 'tag_mode'].includes(k))

function extras(f: Filters): string[] {
  const x: string[] = []
  if (f.lenses?.length) x.push('lens: ' + f.lenses.join(', '))
  if (f.cameras?.length) x.push('camera: ' + f.cameras.join(', '))
  if (f.q) x.push(`search “${f.q}”`)
  if (f.month) x.push('month ' + f.month)
  if (f.folder) x.push('folder ' + f.folder)
  if (f.tag) x.push('tag ' + f.tag)
  return x
}

// where the photos go: a frame of a series' board ('f<id>'), a series of a folder ('s<id>'), a new one, one per folder
const targetKey = (t?: AddTarget | null) => (t?.frame ? 'f' + t.frame : t?.series ? 's' + t.series : t?.per_folder ? 'per' : 'new')
const keyTarget = (k: string, label?: string): AddTarget => (k.startsWith('f') ? { frame: +k.slice(1) } : k.startsWith('s') ? { series: +k.slice(1) } : k === 'per' ? { per_folder: true } : { new: true, label: label || undefined })
const tagLeaf = (t: string) => t.slice(t.lastIndexOf('|') + 1) || t

/** "+ Add photos" for a series (into one of its frames, a new frame, a frame per folder) or for a folder of
 *  series (into one of its series, a new series, a series per folder). It reads the board first, for the
 *  frames or series to choose from and last time's criteria. */
export function AddPhotosDialog({ target, name, onClose, onAdded }: { target: Target; name: string; onClose: () => void; onAdded: (n: number) => void }) {
  const s = useStore()
  const [b, setB] = useState<{ frames: Frame[]; lanes: LaneInfo[]; lastAdd: LastAdd | null } | null>(null)
  useEffect(() => {
    api.tboard(target).then(r => setB({ frames: r.frames, lanes: r.lanes || [], lastAdd: r.last_add || null })).catch(e => { s.toast(e.message); onClose() })
  }, [target.kind, target.id])
  if (!b) return <div className="backdrop" />
  return <AddPhotosInner where={target} name={name} frames={b.frames} lanes={b.lanes} lastAdd={b.lastAdd} onClose={onClose} onAdded={onAdded} />
}

/** The same panel to say where to look for something (Import from Miro's missing photos): criteria only, no
 *  place to put them; "Look" hands the criteria back. Opens with the last ones used. */
export function WhereToLook({ target, start, onClose, onPick }: { target: Target; start?: Filters | null; onClose: () => void; onPick: (f: Filters) => void }) {
  const [last, setLast] = useState<LastAdd | null | undefined>(start ? { filters: start } as LastAdd : undefined)
  useEffect(() => {
    if (start) return
    api.tboard(target).then(r => setLast(r.last_add || null)).catch(() => setLast(null))
  }, [target.kind, target.id])
  if (last === undefined) return <div className="backdrop" />
  return <AddPhotosInner where={target} name="" frames={[]} lanes={[]} lastAdd={last} onClose={onClose} onAdded={() => {}} pick={onPick} />
}

function AddPhotosInner({ where, name, frames, lanes, lastAdd, onClose, onAdded, pick }: {
  where: Target; name: string; frames: Frame[]; lanes: LaneInfo[]; lastAdd: LastAdd | null | undefined
  onClose: () => void; onAdded: (n: number) => void; pick?: (f: Filters) => void
}) {
  const s = useStore()
  const isFolder = where.kind === 'folder'
  const [crit, setCrit] = useState<Filters>(() => ({ ...EMPTY, ...(lastAdd?.filters || {}) }))
  // what can be chosen: a folder's series, or a series' frames
  const places = isFolder ? lanes.map(l => 's' + l.id) : frames.filter(f => f.id).map(f => 'f' + f.id)
  const first = isFolder ? places[0] : (() => { const f = frames.find(x => !x.maybe && x.id); return f ? 'f' + f.id : undefined })()
  const [target, setTarget] = useState<string>(() => {
    const k = targetKey(lastAdd?.target)
    if ((k.startsWith('f') || k.startsWith('s')) && !places.includes(k)) return first || 'new'
    return lastAdd ? k : first || 'new'
  })
  const [newName, setNewName] = useState('')
  const [tagText, setTagText] = useState('')
  const [m, setM] = useState<MatchResult | null>(null)
  const [mErr, setMErr] = useState('')
  const [busy, setBusy] = useState(false)
  const reqId = useRef(0)


  const set = (p: Partial<Filters>) => setCrit(c => ({ ...c, ...p }))
  const picked = crit.folders || []
  const sub = crit.subfolders !== false

  // live count, a moment after the last change
  useEffect(() => {
    const f = clean(crit)
    if (!hasCriteria(f)) { setM(null); setMErr(''); return }
    const id = ++reqId.current
    const t = window.setTimeout(() => {
      api.tmatch(where, f).then(r => { if (id === reqId.current) { setM(r); setMErr('') } })
        .catch(e => { if (id === reqId.current) setMErr(e.message) })
    }, 250)
    return () => window.clearTimeout(t)
  }, [crit, where.kind, where.id])


  // tags plus their parent levels (picking a parent matches every tag inside it)
  const allTags = useMemo(() => {
    const out = new Set<string>()
    for (const t of s.tags) {
      if (t.path.startsWith('darktable|')) continue
      const parts = t.path.split('|')
      for (let i = 1; i <= parts.length; i++) out.add(parts.slice(0, i).join('|'))
    }
    return [...out].sort((a, b) => a.localeCompare(b))
  }, [s.tags])
  const addTag = (v: string) => {
    const t = v.trim()
    if (!t) return
    const hit = allTags.find(p => p.toLowerCase() === t.toLowerCase()) || allTags.find(p => tagLeaf(p).toLowerCase() === t.toLowerCase())
    if (!hit) { s.toast(`No tag “${t}”`); return }
    if (!(crit.tags || []).includes(hit)) set({ tags: [...(crit.tags || []), hit] })
    setTagText('')
  }

  const fromLibrary = () => {
    const lf = s.filters
    const next: Filters = { ...EMPTY, ...lf, series: undefined, show_hidden: undefined }
    if (lf.folder) { next.folders = [...(lf.folders || []), lf.folder]; next.folder = null }
    if (lf.tag) { next.tags = [lf.tag]; next.tag = null }
    setCrit(clean(next))
  }
  const extra = extras(crit)
  const dropExtras = () => setCrit(c => { const n: Filters = {}; OWN.forEach(k => { (n as any)[k] = (c as any)[k] }); return n })

  const go = async () => {
    if (!m || !m.new) return
    const n = Math.min(m.new, m.limit)
    if (n > 400 && !window.confirm(`Add ${n} photos to “${name}”? That's a lot for one board.`)) return
    setBusy(true)
    try {
      const r = await api.taddMatching(where, clean(crit), keyTarget(target, newName.trim()))
      onAdded(r.added)
    } catch (e: any) { s.toast(e.message); setBusy(false) }
  }

  const ratings = crit.ratings || [], labels = crit.labels || [], tags = crit.tags || []
  const frameName = (f: Frame) => (f.maybe ? f.label || 'Maybe pile' : 'Frame ' + f.label)

  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="dialog addp" role="dialog" aria-label={pick ? 'Where to look' : `Add photos to ${name}`}>
        <div className="row">
          <span className="display grow" style={{ fontSize: 19 }}>{pick ? 'Where to look for the missing photo' : <>Add photos to “{name}”</>}</span>
          <button className="btn ghost" onClick={fromLibrary} title="Start from the filters and open folder you have in the Library">Use library filters</button>
          <button className="btn ghost" onClick={() => setCrit(EMPTY)}>Clear</button>
        </div>
        {pick ? <div className="tiny muted">Pick the folders, dates or names it could be in; the photos that match are compared with Miro's picture, the closest first.</div>
          : lastAdd && <div className="tiny muted">Opened with last time's criteria. Adding again brings in only photos that aren't on the board yet.</div>}

        <div className="addp-cols">
          <div className="col" style={{ minWidth: 0 }}>
            <div className="row"><span className="caps grow">Folders</span>
              {picked.length > 0 && <button className="btn ghost tiny" onClick={() => set({ folders: [] })}>clear {picked.length}</button>}</div>
            <FolderPicker picked={picked} sub={sub} onPicked={folders => set({ folders })} onSub={v => set({ subfolders: v })} />
          </div>

          <div className="col" style={{ gap: 12, minWidth: 0 }}>
            <div className="col" style={{ gap: 6 }}>
              <div className="row"><span className="caps grow">File names</span>
                {crit.names && <button className="btn ghost tiny" onClick={() => set({ names: '' })}>clear</button>}</div>
              <textarea className="input addp-names" rows={crit.names && crit.names.includes('\n') ? 4 : 1} spellCheck={false}
                placeholder="DSCF3215, DSCF3216… or paste a list" aria-label="File names" value={crit.names || ''}
                onChange={e => set({ names: e.target.value })} />
              {m?.names_missing?.length ? <span className="tiny" style={{ color: '#ff8b8e' }}>Not found{hasOther(crit) ? ' with the other criteria' : ''}: {m.names_missing.slice(0, 12).join(', ')}{m.names_missing.length > 12 ? ` and ${m.names_missing.length - 12} more` : ''}</span>
                : <span className="tiny muted">Any of them, without the extension: DSCF3215.jpg finds DSCF3215.RAF. Folders and the rest below narrow it further.</span>}
            </div>
            <div className="col" style={{ gap: 6 }}>
              <span className="caps">Rating <span className="tiny muted" style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 400 }}>· any of</span></span>
              <div className="row wrap" style={{ gap: 4 }}>
                {RATINGS.map(([v, l]) => <button key={v} className={'chip' + (ratings.includes(v) ? ' on' : '')} onClick={() => set({ ratings: toggle(ratings, v) })}>{l}</button>)}
              </div>
            </div>
            <div className="col" style={{ gap: 6 }}>
              <div className="row"><span className="caps grow">Color labels</span>
                {labels.length > 1 && <button className="chip" onClick={() => set({ label_mode: crit.label_mode === 'and' ? 'or' : 'and' })}>{crit.label_mode === 'and' ? 'all of them' : 'any of them'}</button>}</div>
              <div className="row" style={{ gap: 8 }}>
                {LABELS.map(l => <button key={l.i} aria-label={`${l.name} label`} className={'dot' + (labels.includes(l.i) ? ' on' : labels.length ? ' dim' : '')} style={{ background: l.c }}
                  onClick={() => set({ labels: toggle(labels, l.i) })} />)}
              </div>
            </div>
            <div className="col" style={{ gap: 6 }}>
              <div className="row"><span className="caps grow">Tags</span>
                {tags.length > 1 && <button className="chip" onClick={() => set({ tag_mode: crit.tag_mode === 'and' ? 'or' : 'and' })}>{crit.tag_mode === 'and' ? 'all of them' : 'any of them'}</button>}</div>
              <div className="row wrap" style={{ gap: 4 }}>
                {tags.map(t => <button key={t} className="chip on" title={t + ' (and the tags inside it). Click to remove'} onClick={() => set({ tags: tags.filter(x => x !== t) })}>{tagLeaf(t)} ×</button>)}
                <input className="input" list="addp-tags" placeholder="Add a tag" value={tagText} style={{ width: 150 }}
                  onChange={e => { const v = e.target.value; if (allTags.includes(v)) addTag(v); else setTagText(v) }}
                  onKeyDown={e => { if (e.key === 'Enter') addTag(tagText) }} />
                <datalist id="addp-tags">{allTags.slice(0, 2000).map(t => <option key={t} value={t} />)}</datalist>
              </div>
            </div>
            <div className="col" style={{ gap: 6 }}>
              <span className="caps">Taken</span>
              <div className="row wrap" style={{ gap: 6 }}>
                <input className="input" type="date" aria-label="Taken from" value={crit.date_from || ''} onChange={e => set({ date_from: e.target.value || null })} />
                <span className="small muted">to</span>
                <input className="input" type="date" aria-label="Taken until" value={crit.date_to || ''} onChange={e => set({ date_to: e.target.value || null })} />
              </div>
            </div>
            <div className="col" style={{ gap: 6 }}>
              <span className="caps">darktable</span>
              <div className="row" style={{ gap: 4 }}>
                {[['', 'all'], ['yes', 'edited'], ['no', 'not edited']].map(([v, l]) => (
                  <button key={v} className={'chip' + ((crit.edited || '') === v ? ' on' : '')} onClick={() => set({ edited: v as Filters['edited'] })}>{l}</button>))}
              </div>
            </div>
            {extra.length > 0 && (
              <div className="row small soft" style={{ gap: 6 }}>
                <span className="grow">Also from the library: {extra.join(' · ')}</span>
                <button className="btn ghost tiny" onClick={dropExtras}>drop</button>
              </div>
            )}
            {!pick && <><label className="col" style={{ gap: 6 }}>
              <span className="caps">Put them in</span>
              <select className="input" value={target} onChange={e => setTarget(e.target.value)}>
                {isFolder ? lanes.map(l => <option key={l.id} value={'s' + l.id}>{l.path?.length ? l.path.join(' › ') + ' › ' : ''}{l.name}</option>)
                  : frames.filter(f => f.id).map(f => <option key={f.id} value={'f' + f.id}>{frameName(f)}</option>)}
                <option value="new">A new {isFolder ? 'series' : 'frame'}</option>
                <option value="per">A new {isFolder ? 'series' : 'frame'} for each folder{m && m.folders > 1 ? ` (${m.folders})` : ''}</option>
              </select>
            </label>
            {target === 'new' && <input className="input" placeholder={isFolder ? 'Name of the new series' : 'Name of the new frame (else the next number)'} aria-label={isFolder ? 'Name of the new series' : 'Name of the new frame'} value={newName} onChange={e => setNewName(e.target.value)} />}</>}
          </div>
        </div>

        <div className="addp-foot">
          <div className="addp-strip">
            {m?.sample.slice(0, 10).map(c => <RetryImg key={c.id} src={coverUrl(c)} alt="" />)}
          </div>
          <span className="small soft grow" aria-live="polite">
            {mErr ? <span style={{ color: '#ff8b8e' }}>{mErr}</span>
              : !hasCriteria(clean(crit)) ? 'Type file names, or pick folders or criteria'
              : !m ? 'Counting…'
              : pick ? <>{m.total} photo{m.total === 1 ? '' : 's'} to compare</>
              : <>{m.total} match{m.total !== m.new ? ` · ${m.total - m.new} already ${isFolder ? 'in ' + name : 'on the board'}` : ''}{m.new > m.limit ? ` · first ${m.limit} are added` : ''}</>}
          </span>
          <button className="btn" onClick={onClose}>Cancel</button>
          {pick ? <button className="btn primary" disabled={!m || !m.total} onClick={() => pick(clean(crit))}>Look among {m ? m.total : '…'}</button> :
          <button className="btn primary" disabled={busy || !m || !m.new} onClick={go}>
            {busy ? 'Adding…' : m && m.new ? `Add ${Math.min(m.new, m.limit)} photo${m.new === 1 ? '' : 's'}` : 'Add photos'}
          </button>}
        </div>
      </div>
    </>
  )
}
