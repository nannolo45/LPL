import { useEffect, useMemo, useRef, useState } from 'react'
import { api, SendFolder, SendRequest, SendState } from '../api'
import { useStore } from '../store'
import { fmtMonth } from '../fields'

const mb = (b: number) => (b > 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b > 1e6 ? `${Math.round(b / 1e6)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`)

function FolderPicker({ onPick, onClose }: { onPick: (p: string) => void; onClose: () => void }) {
  const [cur, setCur] = useState<{ path: string; parent: string | null; dirs: string[] } | null>(null)
  const [err, setErr] = useState('')
  const go = (p: string) => api.fsList(p).then(r => { setCur(r); setErr('') }).catch(e => setErr(e.message))
  useEffect(() => { go('') }, [])
  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="dialog" role="dialog" aria-label="Choose a folder" style={{ width: 'min(560px, calc(100vw - 32px))' }}>
        <b>Choose a folder on this PC</b>
        <div className="row small soft" style={{ wordBreak: 'break-all' }}>{cur?.path || 'This PC'}</div>
        {err && <div className="small" style={{ color: '#ff8b8e' }}>{err}</div>}
        <div style={{ maxHeight: 320, overflow: 'auto', border: '1px solid var(--line)', borderRadius: 8 }}>
          {cur?.parent != null && <button className="btn ghost" style={{ display: 'block', width: '100%', textAlign: 'left' }} onClick={() => go(cur.parent || '')}>⬑ up</button>}
          {cur?.dirs.map(d => (
            <button key={d} className="btn ghost" style={{ display: 'block', width: '100%', textAlign: 'left' }} onClick={() => go(d)}>
              {d.split(/[\\/]/).filter(Boolean).pop() || d}
            </button>
          ))}
          {cur && !cur.dirs.length && <div className="small muted" style={{ padding: 10 }}>No subfolders.</div>}
        </div>
        <div className="row">
          <div className="grow" />
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!cur?.path} onClick={() => cur?.path && onPick(cur.path)}>Use this folder</button>
        </div>
      </div>
    </>
  )
}

function SendDialog({ target, onClose, onStarted, deleteDefault }: { target: { folder?: string; ids?: number[]; label: string; descriptor: string; year: string }; onClose: () => void; onStarted: () => void; deleteDefault: boolean }) {
  const s = useStore()
  const [nasFolders, setNasFolders] = useState<string[]>([])
  const [parent, setParent] = useState<string | null>(null)
  const [descriptor, setDescriptor] = useState(target.descriptor)
  const [split, setSplit] = useState(true)
  const [del, setDel] = useState(deleteDefault)
  const [plan, setPlan] = useState<{ count: number; bytes: number; already_sent: number; folders: { path: string; count: number }[] } | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    api.sendNasFolders(target.year).then(r => { setNasFolders(r.folders); setParent(p => (p == null ? r.suggested_parent : p)) })
      .catch(e => { setErr(e.message); setParent('') })
  }, [])
  const req: SendRequest = { folder: target.folder, ids: target.ids, parent: parent || '', descriptor, split_by_day: split, delete_after: del }
  useEffect(() => {
    if (parent == null) return
    const t = window.setTimeout(() => api.sendPlan(req).then(setPlan).catch(e => setErr(e.message)), 250)
    return () => window.clearTimeout(t)
  }, [parent, descriptor, split])
  const start = () => api.sendStart(req).then(() => { onStarted(); onClose() }).catch(e => setErr(e.message))
  const parents = useMemo(() => Array.from(new Set(['', ...(parent ? [parent] : []), ...nasFolders.filter(f => !/\d{4}-\d{2}-\d{2}_/.test(f.split('/').pop() || ''))])).sort(), [nasFolders, parent])
  return (
    <>
      <div className="backdrop" onClick={onClose} />
      <div className="dialog" role="dialog" aria-label="Send to NAS" style={{ width: 'min(620px, calc(100vw - 32px))', maxHeight: '90vh', overflow: 'auto' }}>
        <span className="display" style={{ fontSize: 19 }}>Send “{target.label}” to the NAS</span>
        {err && <div className="small" style={{ color: '#ff8b8e' }}>{err}</div>}
        <label className="col" style={{ gap: 4 }}><span className="small soft">Put the folders inside this NAS folder</span>
          <select className="input" value={parent ?? ''} onChange={e => setParent(e.target.value)}>
            {parents.map(p => <option key={p} value={p}>{p ? p + (nasFolders.includes(p) ? '' : '  (new folder)') : '(top of the photo folder)'}</option>)}
          </select>
        </label>
        <label className="col" style={{ gap: 4 }}><span className="small soft">Descriptor (after the date)</span>
          <input className="input" value={descriptor} onChange={e => setDescriptor(e.target.value)} />
        </label>
        <label className="row" style={{ cursor: 'pointer' }}>
          <input type="checkbox" checked={split} onChange={() => setSplit(!split)} style={{ accentColor: '#E8A33D' }} />
          <span>One folder per capture day</span>
        </label>
        <label className="row" style={{ cursor: 'pointer' }}>
          <input type="checkbox" checked={del} onChange={() => setDel(!del)} style={{ accentColor: '#E8A33D' }} />
          <span>Delete the copies on this PC after the NAS confirmed them</span>
        </label>
        {plan && (
          <div className="card" style={{ padding: 12, gap: 6 }}>
            <span className="small soft">{plan.count} photos · {mb(plan.bytes)} to upload{plan.already_sent ? ` · ${plan.already_sent} already on the NAS (only changed edits are sent)` : ''}</span>
            {plan.folders.map(f => <div key={f.path} className="row small"><span className="grow ellipsis">NAS › {f.path}</span><span className="muted">{f.count}</span></div>)}
          </div>
        )}
        <div className="row">
          <div className="grow" />
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={!plan || !plan.count} onClick={start}>Send {plan?.count ?? ''} photos</button>
        </div>
      </div>
    </>
  )
}

export function SendView() {
  const s = useStore()
  const [roots, setRoots] = useState<{ name: string; path: string; online: boolean; count: number }[]>([])
  const [ov, setOv] = useState<{ folders: SendFolder[]; nas: { ok: boolean; url: string; photos?: number; error?: string }; delete_after: boolean } | null>(null)
  const [st, setSt] = useState<SendState | null>(null)
  const [picker, setPicker] = useState(false)
  const [path, setPath] = useState('')
  const [target, setTarget] = useState<null | { folder?: string; ids?: number[]; label: string; descriptor: string; year: string }>(null)
  const wasRunning = useRef(false)
  const load = () => {
    api.libraryFolders().then(r => setRoots(r.folders)).catch(e => s.toast(e.message))
    api.sendOverview().then(setOv).catch(e => s.toast(e.message))
  }
  useEffect(() => { load() }, [])
  useEffect(() => {
    if (s.sendIds && s.sendIds.length) {
      setTarget({ ids: s.sendIds, label: `${s.sendIds.length} selected photos`, descriptor: 'Photos', year: String(new Date().getFullYear()) })
      s.setSendIds(null)
    }
  }, [s.sendIds])
  useEffect(() => {
    let alive = true
    const tick = () => api.sendStatus().then(r => {
      if (!alive) return
      setSt(r)
      if (wasRunning.current && !r.running) { load(); s.reload(); s.reloadSeries() }
      wasRunning.current = r.running
    }).catch(() => {})
    tick(); const t = window.setInterval(tick, 1000)
    return () => { alive = false; window.clearInterval(t) }
  }, [])
  const add = (p: string) => api.libraryAdd(p).then(r => { s.toast(`Added “${r.name}”. Reading the photos…`); setPath(''); setPicker(false); load(); window.setTimeout(() => { load(); s.reload() }, 3000) }).catch(e => s.toast(e.message))
  const remove = (name: string) => window.confirm(`Remove “${name}” from the local library? The photos on disk are not touched.`) &&
    api.libraryRemove(name).then(() => { load(); s.reload() }).catch(e => s.toast(e.message))
  const open = (f: SendFolder) => { s.patchFilters({ folder: f.folder, month: null, tag: null, series: null }); s.setView('grid') }
  return (
    <div className="scroll">
      <div className="col" style={{ gap: 18, maxWidth: 1100 }}>
        <div className="col" style={{ gap: 4 }}>
          <span className="display" style={{ fontSize: 24, fontWeight: 500 }}>Send to NAS</span>
          <span className="soft">This is the library on this PC. It works without the NAS; send photos (with your latest darktable edits) whenever you're connected.</span>
        </div>

        <div className="card">
          <div className="row wrap">
            <b>NAS</b>
            {ov?.nas.ok ? <span className="pill-ok"><i />connected · {ov.nas.photos?.toLocaleString()} photos</span> : <span className="pill-off"><i />not reachable</span>}
            <span className="small muted">{ov?.nas.url}</span>
            <div className="grow" />
            <button className="btn" onClick={load}>Check again</button>
            {ov?.nas.ok && ov.nas.url && <a className="btn" href={ov.nas.url} target="_blank" rel="noreferrer">Open NAS library ↗</a>}
          </div>
          {!ov?.nas.ok && ov?.nas.error && <span className="small muted">{ov.nas.error}</span>}
        </div>

        {st && (st.running || st.finished) && (
          <div className="card">
            <div className="row"><b>{st.running ? 'Sending…' : 'Last send'}</b><span className="small muted">{st.phase}</span></div>
            <div style={{ height: 8, borderRadius: 4, background: '#2e2e33', position: 'relative' }}>
              <div style={{ position: 'absolute', left: 0, top: 0, height: 8, borderRadius: 4, background: '#E8A33D', width: `${st.total ? Math.round(100 * st.done / st.total) : 0}%` }} />
            </div>
            <span className="small soft">{st.done} of {st.total} photos{st.bytes_total ? ` · ${mb(st.bytes_done)} of ${mb(st.bytes_total)}` : ''} · {st.sent_files} files and {st.sent_xmps} edits uploaded{st.skipped ? ` · ${st.skipped} already there` : ''}{st.deleted ? ` · ${st.deleted} local copies deleted` : ''}</span>
            {!st.running && st.dest_folders.length > 0 && <span className="small muted">NAS › {st.dest_folders.join(', ')}</span>}
            {st.series && st.series.length > 0 && <span className="small muted">Series: {st.series.map(x => `${x.name} (${x.created ? 'new' : `+${x.added}`})`).join(', ')}</span>}
            {st.errors.map((e, i) => <span key={i} className="small" style={{ color: '#ff8b8e' }}>{e}</span>)}
          </div>
        )}

        <div className="card">
          <b>Folders on this PC</b>
          {ov && ov.folders.length ? (
            <table className="wtable">
              <thead><tr><th>Folder</th><th>Dates</th><th>Photos</th><th>On the NAS</th><th>Edits not sent</th><th /></tr></thead>
              <tbody>
                {ov.folders.map(f => (
                  <tr key={f.folder}>
                    <td><button className="btn ghost" style={{ padding: 0 }} onClick={() => open(f)}>{f.folder}</button></td>
                    <td className="muted">{f.first ? (fmtMonth(f.first) === fmtMonth(f.last || f.first) ? fmtMonth(f.first) : `${fmtMonth(f.first)} – ${fmtMonth(f.last!)}`) : ''}</td>
                    <td>{f.n}</td>
                    <td>{f.sent === f.n ? <span style={{ color: '#8fd08f' }}>all</span> : f.sent ? `${f.sent} of ${f.n}` : 'none'}</td>
                    <td>{f.changed ? <span style={{ color: '#8cb8e6' }}>{f.changed}</span> : ''}</td>
                    <td style={{ textAlign: 'right' }}>
                      <button className="btn primary" disabled={!ov.nas.ok || !!st?.running}
                        onClick={() => setTarget({ folder: f.folder, label: f.folder.split('/').pop() || f.folder, descriptor: f.descriptor, year: (f.first || '').slice(0, 4) || String(new Date().getFullYear()) })}>
                        {f.sent === f.n && !f.changed ? 'Send again' : f.sent ? 'Send the rest' : 'Send…'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <span className="small muted">No photos yet. Add a folder below.</span>}
        </div>

        <div className="card">
          <b>Library folders</b>
          <span className="small soft">Folders on this PC that belong to the local library, e.g. where you dump the card each night. New photos are picked up every couple of minutes.</span>
          {roots.map(r => (
            <div key={r.name} className="row">
              <span className={r.online ? 'pill-ok' : 'pill-off'}><i />{r.online ? 'available' : 'not connected'}</span>
              <b>{r.name}</b><span className="small muted grow ellipsis">{r.path} · {r.count} photos</span>
              <button className="btn" onClick={() => remove(r.name)}>Remove</button>
            </div>
          ))}
          <div className="row">
            <input className="input grow" placeholder="Folder path, e.g. D:\Trips\Lisbon" value={path} onChange={e => setPath(e.target.value)} onKeyDown={e => e.key === 'Enter' && path && add(path)} />
            <button className="btn" onClick={() => setPicker(true)}>Browse…</button>
            <button className="btn primary" disabled={!path} onClick={() => add(path)}>Add</button>
            <button className="btn" onClick={() => api.scan(false).then(() => { s.toast('Looking for new photos…'); window.setTimeout(() => { load(); s.reload() }, 3000) })}>Rescan</button>
          </div>
        </div>
      </div>
      {picker && <FolderPicker onClose={() => setPicker(false)} onPick={p => add(p)} />}
      {target && <SendDialog target={target} deleteDefault={!!ov?.delete_after} onClose={() => setTarget(null)} onStarted={() => { wasRunning.current = true; s.toast('Sending to the NAS…') }} />}
    </div>
  )
}
