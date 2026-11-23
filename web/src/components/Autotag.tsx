// SPDX-License-Identifier: GPL-3.0-or-later
// Auto-tagging: Settings > Auto-tagging (the B&W rule and its parameters), the preview run (check the unsure photos,
// then apply), and the photo panel's "why" with one-click corrections. The rules themselves run on the server
// (server/lpl/autotag.py); this only shows and sets them.
import { useEffect, useRef, useState } from 'react'
import { api, AutotagCounts, AutotagInfo, AutotagReview, AutotagRule, AutotagSettings, AutotagStatus, Photo, thumbUrl } from '../api'
import { useStore } from '../store'
import { useBackLayer } from '../back'

const n = (x: number) => x.toLocaleString()

/** The auto tags' names, for marking them in the tag list ("auto" badge). Loaded once, refreshed when settings change. */
let known: string[] = []
const listeners = new Set<() => void>()
function setKnown(st: AutotagSettings | null) {
  known = st && st.bnw.enabled ? [st.bnw.tag_bnw, st.bnw.tag_toned] : []
  listeners.forEach(f => f())
}
let loaded = false
export function useAutoTags(): string[] {
  const [, set] = useState(0)
  useEffect(() => {
    const f = () => set(x => x + 1)
    listeners.add(f)
    if (!loaded) { loaded = true; api.autotag().then(r => setKnown(r.settings)).catch(() => { loaded = false }) }
    return () => { listeners.delete(f) }
  }, [])
  return known
}

function Toggle({ on, label, onChange, disabled }: { on: boolean; label: string; onChange: (v: boolean) => void; disabled?: boolean }) {
  return <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled} className={'at-sw' + (on ? ' on' : '')} onClick={() => onChange(!on)}><i /></button>
}

function Source({ num, title, help, on, set }: { num: number; title: string; help: string; on: boolean; set: (v: boolean) => void }) {
  return (
    <div className="at-src">
      <span className="at-num">{num}</span>
      <div className="col grow" style={{ gap: 2, minWidth: 0 }}><span>{title}</span><span className="tiny muted">{help}</span></div>
      <Toggle on={on} label={`Use ${title}`} onChange={set} />
    </div>
  )
}

/** Settings > Auto-tagging. Every change is saved at once (tag names when the field is left). */
export function AutotagSettingsPanel() {
  const s = useStore()
  const [st, setSt] = useState<AutotagSettings | null>(null)
  const [counts, setCounts] = useState<AutotagCounts | null>(null)
  const [status, setStatus] = useState<AutotagStatus | null>(null)
  const [names, setNames] = useState({ tag_bnw: '', tag_toned: '' })
  const [review, setReview] = useState(false)
  const [folder, setFolder] = useState('')
  const [paths, setPaths] = useState<string[]>([])
  const save = useRef(0)
  const load = () => api.autotag().then(r => { setSt(r.settings); setCounts(r.counts); setStatus(r.status); setNames({ tag_bnw: r.settings.bnw.tag_bnw, tag_toned: r.settings.bnw.tag_toned }); setKnown(r.settings) }).catch(e => s.toast(e.message))
  useEffect(() => { load(); api.folders().then(r => setPaths(r.folders.map(f => f.path))).catch(() => {}) }, [])
  if (!st) return <div className="muted small">Loading…</div>
  const b = st.bnw
  const put = (patch: Partial<AutotagRule>, top?: { auto?: boolean }) => {
    const next = { ...st, ...(top || {}), bnw: { ...b, ...patch } }
    setSt(next)
    const id = ++save.current
    api.autotagSave({ ...(top || {}), bnw: patch }).then(r => { if (id === save.current) { setSt(r.settings); setCounts(r.counts); setKnown(r.settings) } }).catch(e => s.toast(e.message))
  }
  const strictWord = b.strictness < 25 ? 'strict' : b.strictness > 75 ? 'lenient' : 'normal'
  const enable = (v: boolean) => {
    if (v && !counts?.checked) { setReview(true); return }  // first time: look at what it would do before anything is written
    put({ enabled: v })
    if (v) api.autotagRun({ apply: true }).then(() => s.toast('Auto-tagging switched on: checking the library…')).catch(e => s.toast(e.message))
  }
  const addFolder = () => { const f = folder.trim().replace(/^\/+|\/+$/g, ''); if (f && !b.folders.includes(f)) put({ folders: [...b.folders, f] }); setFolder('') }
  const last = status?.finished ? new Date(status.finished * 1000) : null
  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="row wrap" style={{ gap: 10 }}>
        <div className="col grow" style={{ gap: 2, minWidth: 220 }}>
          <span className="display" style={{ fontSize: 16 }}>Auto-tagging</span>
          <span className="small muted">LPL tags photos for you. Auto tags are ordinary tags, written to the XMP like any other, so darktable and your filters see them.</span>
        </div>
        <span className="small soft">Run on new and changed photos</span>
        <Toggle on={st.auto} label="Run on new and changed photos" onChange={v => put({}, { auto: v })} />
      </div>

      <div className="at-card">
        <div className="row wrap" style={{ gap: 10 }}>
          <Toggle on={b.enabled} label="Black & white rule" onChange={enable} />
          <span style={{ fontWeight: 600, fontSize: 14 }}>Black &amp; white</span>
          <span className="small muted grow">
            {counts && counts.checked > 0
              ? `${last ? 'last run ' + last.toLocaleString() + ' · ' : ''}${n(counts.bnw)} B&W${counts.camera ? ` (${n(counts.camera)} shot B&W in camera)` : ''} · ${n(counts.toned)} toned · ${n(counts.unsure)} unsure`
              : 'not run yet'}
            {status?.state === 'running' ? ` · checking ${n(status.done)} of ${n(status.total)}${status.rate ? ` (${status.rate}/s)` : ''}…` : ''}
          </span>
          <button type="button" className="btn" onClick={() => setReview(true)}>Preview run…</button>
        </div>

        <div className="at-grid">
          <div className="col" style={{ gap: 8 }}>
            <span className="caps">How LPL decides, in this order</span>
            <Source num={1} title="darktable rendered preview" on={b.use_rendered} set={v => put({ use_rendered: v })}
              help="The JPEG the bridge sends back after you edit. Shows exactly what you see in darktable." />
            <Source num={2} title="darktable edit history" on={b.use_history} set={v => put({ use_history: v })}
              help="The active modules in the XMP: monochrome, color calibration's gray tab, channel mixer gray, color balance rgb at chroma -100%, a B&W LUT or style (Acros, Ilford, Tri-X…). Only the latest state of each module counts. B&W made other ways needs 1." />
            <Source num={3} title="Camera preview (unedited photos)" on={b.use_camera} set={v => put({ use_camera: v })}
              help="The JPEG inside the raw (or the image itself): shows what you shot in B&W in camera. Replaced by 1 and 2 once you edit the photo." />
            <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={b.disagree_review} onChange={e => put({ disagree_review: e.target.checked })} />
              When sources disagree, send the photo to review instead of tagging it</label>
            <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={b.camera_over_edit} onChange={e => put({ camera_over_edit: e.target.checked })} />
              Edited photos shot B&amp;W in camera count as B&amp;W while there's no darktable render to check and no B&amp;W module is found</label>
          </div>
          <div className="col" style={{ gap: 12 }}>
            <span className="caps">Tags</span>
            <div className="at-tags">
              <label className="small muted" htmlFor="at-bnw">Black &amp; white</label>
              <input id="at-bnw" className="input mono" value={names.tag_bnw} onChange={e => setNames({ ...names, tag_bnw: e.target.value })} onBlur={() => names.tag_bnw !== b.tag_bnw && put({ tag_bnw: names.tag_bnw })} />
              <label className="small muted" htmlFor="at-toned">Toned</label>
              <input id="at-toned" className="input mono" value={names.tag_toned} onChange={e => setNames({ ...names, tag_toned: e.target.value })} onBlur={() => names.tag_toned !== b.tag_toned && put({ tag_toned: names.tag_toned })} />
            </div>
            <span className="tiny muted">Colour photos get no tag. Renaming a tag moves it on the photos at the next run.</span>
            <div className="col" style={{ gap: 6 }}>
              <span className="small muted">Sepia and split-toned photos count as</span>
              <div className="seg" role="group" aria-label="Toned photos count as">
                {([['bnw+toned', 'B&W + toned'], ['bnw', 'B&W only'], ['colour', 'colour']] as const).map(([v, l]) =>
                  <button key={v} className={b.toned === v ? 'on' : ''} aria-pressed={b.toned === v} onClick={() => put({ toned: v })}>{l}</button>)}
              </div>
            </div>
            <div className="col" style={{ gap: 4 }}>
              <div className="row"><span className="small muted grow">How strict “no colour” is</span><span className="small" style={{ color: 'var(--accent-text)' }}>{strictWord}</span></div>
              <input type="range" min={0} max={100} value={b.strictness} aria-label="Strictness" className="at-range" onChange={e => put({ strictness: +e.target.value })} />
              <div className="row tiny muted" style={{ justifyContent: 'space-between' }}><span>strict: any tint is colour</span><span>lenient: faint casts are OK</span></div>
              <div className="row tiny muted" style={{ gap: 8, marginTop: 4 }}>
                <span style={{ whiteSpace: 'nowrap' }}>Unsure band</span>
                <input type="range" min={0} max={100} value={b.band} aria-label="Unsure band width" className="at-range grow" onChange={e => put({ band: +e.target.value })} />
                <span>{b.band ? 'near the limit: review' : 'no review band'}</span>
              </div>
            </div>
          </div>
        </div>

        <div className="hr" />
        <div className="row wrap" style={{ gap: 8 }}>
          <span className="caps">Applies to</span>
          <button className={'chip' + (!b.folders.length ? ' on' : '')} onClick={() => put({ folders: [] })}>Whole library</button>
          {b.folders.map(f => <span key={f} className="chip on">{f} <button className="linkbtn" aria-label={`Remove ${f}`} onClick={() => put({ folders: b.folders.filter(x => x !== f) })}>×</button></span>)}
          <input className="input" style={{ width: 200 }} value={folder} placeholder="add a folder, e.g. 2026" aria-label="Add a folder" list="at-folders"
            onChange={e => setFolder(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') addFolder() }} />
          <datalist id="at-folders">{paths.slice(0, 600).map(f => <option key={f} value={f} />)}</datalist>
        </div>
        <label className="row small" style={{ gap: 8 }}><input type="checkbox" checked={b.remove_stale} onChange={e => put({ remove_stale: e.target.checked })} />
          Remove this rule's tags when a photo no longer matches (e.g. you turned a B&amp;W edit back to colour)</label>
        <span className="tiny muted">Corrections you make are kept: LPL never re-adds an auto tag you removed, and never changes a photo you marked yourself.</span>
      </div>

      <span className="caps" style={{ marginTop: 4 }}>More auto-taggers · later</span>
      <div className="at-later">
        <div><span>Night &amp; blue hour</span><span className="tiny muted">capture time and sun position</span></div>
        <div><span>From metadata</span><span className="tiny muted">ISO, shutter, lens, focal length…</span></div>
        <div><span>Scenes &amp; subjects</span><span className="tiny muted">a model running on the NAS</span></div>
      </div>
      {review && <AutotagReviewDialog onClose={() => { setReview(false); load() }} />}
    </div>
  )
}

type Kind = 'unsure' | 'bnw' | 'toned' | 'removals'
const KINDS: [Kind, string][] = [['unsure', 'Unsure'], ['bnw', 'B&W'], ['toned', 'Toned'], ['removals', 'Removals']]
const choiceOf = (p: Photo): 'bnw' | 'toned' | 'colour' | null => {
  const o = p.autotag?.override
  if (!o) return null
  return o.verdict === 'colour' ? 'colour' : o.toned ? 'toned' : 'bnw'
}

/** The preview run: check the library (nothing is written), look at the unsure photos and decide, then apply. */
export function AutotagReviewDialog({ onClose }: { onClose: () => void }) {
  const s = useStore()
  const [kind, setKind] = useState<Kind>('unsure')
  const [data, setData] = useState<AutotagReview | null>(null)
  const [status, setStatus] = useState<AutotagStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [decided, setDecided] = useState(0)
  useBackLayer(true, onClose)
  const load = (k = kind) => api.autotagReview(k).then(r => {
    if (k === 'unsure' && !r.counts.unsure && r.counts.bnw) { setKind('bnw'); load('bnw'); return }  // nothing to decide: show what gets tagged
    setData(r); setStatus(r.status)
  }).catch(e => s.toast(e.message))
  useEffect(() => {
    let stop = false, timer = 0
    const poll = () => api.autotagStatus().then(r => {
      if (stop) return
      setStatus(r.status)
      if (r.status.state === 'running') timer = window.setTimeout(poll, 700)
      else load()
    }).catch(e => s.toast(e.message))
    api.autotagRun({ force: true }).then(poll).catch(() => poll())  // already running (by itself): wait for it
    return () => { stop = true; window.clearTimeout(timer) }
  }, [])
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [])
  const pick = async (p: Photo, v: 'bnw' | 'toned' | 'colour') => {
    const same = choiceOf(p) === v
    try {
      const r = await api.autotagCorrect([p.id], same ? 'clear' : v, false)
      const it = r.items[0]
      if (data) setData({ ...data, items: data.items.map(x => x.id === p.id ? { ...x, ...it, autotag: { ...x.autotag!, override: same ? null : { verdict: v === 'colour' ? 'colour' : 'bnw', toned: v === 'toned' ? 1 : 0 } } } : x) })
      setDecided(d => d + (same ? -1 : choiceOf(p) ? 0 : 1))
    } catch (e: any) { s.toast(e.message) }
  }
  const apply = async () => {
    setBusy(true)
    try {
      const r = await api.autotagApply(true)
      s.toast(`Auto-tagging on: ${n(r.added)} tag${r.added === 1 ? '' : 's'} added${r.removed ? `, ${n(r.removed)} removed` : ''} on ${n(r.photos)} photo${r.photos === 1 ? '' : 's'}.`)
      s.reloadTags()
      onClose()
    } catch (e: any) { s.toast(e.message); setBusy(false) }
  }
  const running = status?.state === 'running'
  const c = data?.counts
  const stat = (v: number | undefined, label: string, hi = false) => <div className={'at-stat' + (hi ? ' hi' : '')}><b>{v == null ? '–' : n(v)}</b><span className="small muted">{label}</span></div>
  return (
    <>
      <div className="backdrop" style={{ zIndex: 40 }} onClick={onClose} />
      <div className={'at-review' + (s.isMobile ? ' phone' : '')} role="dialog" aria-label="Auto-tagging preview run">
        <div className="row" style={{ gap: 10 }}>
          <div className="col grow" style={{ gap: 2 }}>
            <span className="display" style={{ fontSize: 19 }}>Preview: Black &amp; white</span>
            <span className="small muted">{running ? `Checking ${n(status!.done)} of ${n(status!.total)} photos…` : 'Nothing has been written yet. Check the unsure photos, then apply.'}</span>
          </div>
          <button className="btn" onClick={onClose}>Close</button>
        </div>
        {running && <div className="at-bar"><i style={{ width: `${status!.total ? (100 * status!.done / status!.total) : 0}%` }} /></div>}
        <div className="at-stats">
          {stat(c?.bnw, 'B&W')}{stat(c?.toned, 'toned (also B&W)')}{stat(c?.unsure, 'unsure: need you', true)}{stat(c?.adds, 'photos get a tag')}{stat(c?.removals, 'lose a tag')}
        </div>
        <div className="row wrap" style={{ gap: 6 }} role="group" aria-label="Show">
          {KINDS.map(([k, l]) => <button key={k} className={'chip' + (kind === k ? ' on' : '')} aria-pressed={kind === k} onClick={() => { setKind(k); load(k) }}>{l} · {c ? n(c[k]) : '–'}</button>)}
          <div className="grow" />
          {kind === 'unsure' && <span className="tiny muted">closest to the line first</span>}
        </div>
        <div className="at-grid-photos">
          {data?.items.map(p => {
            const ch = choiceOf(p)
            return (
              <div key={p.id} className="at-ph">
                <button type="button" className="at-im" onClick={() => s.setViewer(p.id)} aria-label={`Open ${p.filename} large`}>
                  <img src={thumbUrl(p)} alt="" loading="lazy" />
                </button>
                <span className="small ellipsis">{p.filename}</span>
                <span className="tiny muted ellipsis" title={p.autotag?.detail}>{p.autotag?.detail || p.autotag?.source}</span>
                <div className="at-pick" role="group" aria-label={`${p.filename} is`}>
                  {(['bnw', 'toned', 'colour'] as const).map(v => <button key={v} className={ch === v ? 'on' : ''} aria-pressed={ch === v} onClick={() => pick(p, v)}>{v === 'bnw' ? 'B&W' : v === 'toned' ? 'Toned' : 'Colour'}</button>)}
                </div>
              </div>
            )
          })}
          {data && !data.items.length && !running && <span className="muted small">{kind === 'unsure' ? 'Nothing unsure: every photo was clear.' : 'None.'}</span>}
        </div>
        {data && data.total > data.items.length && <span className="tiny muted">Showing the first {data.items.length} of {n(data.total)}.</span>}
        <div className="at-foot">
          <span className="small muted grow">{decided ? `${decided} decided. ` : ''}Your choices are kept as corrections and never overwritten by later runs.</span>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy || running || !data} onClick={apply}>
            {data ? `Apply: tag ${n(data.counts.adds)}${data.counts.removals ? ` · remove ${n(data.counts.removals)}` : ''}` : 'Apply'}
          </button>
        </div>
      </div>
    </>
  )
}

/** In the photo panel, for one photo: the auto tag it got and why, with one-click corrections. */
export function AutotagLine({ id }: { id: number }) {
  const s = useStore()
  const [info, setInfo] = useState<AutotagInfo | null | undefined>(undefined)
  const ver = s.selPhotos.find(p => p.id === id)?.meta_mtime
  useEffect(() => { let on = true; api.photo(id).then(p => { if (on) setInfo(p.autotag ?? null) }).catch(() => {}); return () => { on = false } }, [id, ver])
  if (!info) return null
  const o = info.override
  const v = o ? o.verdict : info.verdict
  const toned = o ? o.toned : info.toned
  const label = v === 'bnw' ? (toned ? 'B&W, toned' : 'B&W') : v === 'colour' ? 'colour' : v === 'unsure' ? 'unsure' : 'not checked'
  const correct = async (verdict: 'bnw' | 'toned' | 'colour' | 'clear') => {
    try {
      const r = await api.autotagCorrect([id], verdict)
      s.mergePhotos(r.items); s.reloadTags()
      setInfo((await api.photo(id)).autotag ?? null)
    } catch (e: any) { s.toast(e.message) }
  }
  return (
    <div className="at-why">
      <div className="row" style={{ gap: 8 }}>
        <span className="small" style={{ fontWeight: 500 }}>Look (auto): {label}</span>
        {o && <span className="tiny at-mine">your choice</span>}
        <div className="grow" />
        {info.checked && <span className="tiny muted">checked {new Date(info.checked * 1000).toLocaleDateString()}</span>}
      </div>
      {info.detail && <span className="small soft">{o ? `The rule says: ${info.detail}` : info.detail}</span>}
      {info.enabled === false && !o && <span className="tiny muted">Auto-tagging is off (Settings › Auto-tagging). Marking by hand still tags the photo.</span>}
      <div className="row wrap" style={{ gap: 6 }}>
        {v !== 'bnw' || toned ? <button className="btn tiny" onClick={() => correct('bnw')}>Mark B&amp;W</button> : null}
        {!(v === 'bnw' && toned) && <button className="btn tiny" onClick={() => correct('toned')}>Mark toned</button>}
        {v !== 'colour' && <button className="btn tiny" onClick={() => correct('colour')}>Not B&amp;W</button>}
        {o && <button className="btn tiny ghost" onClick={() => correct('clear')}>Let the rule decide</button>}
      </div>
    </div>
  )
}

/** Several photos selected: mark them all as B&W / toned / not B&W (tags them at once), or hand them back to the rule. */
export function AutotagMany({ ids }: { ids: number[] }) {
  const s = useStore()
  const [busy, setBusy] = useState(false)
  const mark = async (verdict: 'bnw' | 'toned' | 'colour' | 'clear') => {
    setBusy(true)
    try {
      const r = await api.autotagCorrect(ids, verdict)
      s.mergePhotos(r.items); s.reloadTags()
      const what = verdict === 'bnw' ? 'B&W' : verdict === 'toned' ? 'B&W, toned' : verdict === 'colour' ? 'not B&W' : 'back to the rule'
      s.toast(`${ids.length} photos: ${what}`)
    } catch (e: any) { s.toast(e.message) } finally { setBusy(false) }
  }
  return (
    <div className="at-why">
      <span className="small" style={{ fontWeight: 500 }}>Look: mark {ids.length} photos</span>
      <div className="row wrap" style={{ gap: 6 }}>
        <button className="btn tiny" disabled={busy} onClick={() => mark('bnw')}>Mark B&amp;W</button>
        <button className="btn tiny" disabled={busy} onClick={() => mark('toned')}>Mark toned</button>
        <button className="btn tiny" disabled={busy} onClick={() => mark('colour')}>Not B&amp;W</button>
        <button className="btn tiny ghost" disabled={busy} onClick={() => mark('clear')}>Let the rule decide</button>
      </div>
    </div>
  )
}
