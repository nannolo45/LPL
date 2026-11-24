// SPDX-License-Identifier: GPL-3.0-or-later
// A "Not found" note left by Import from Miro: look for its photo full screen (by picture in chosen folders /
// dates, or by name) and put it where the note is. Server: miro.py, /api/miro/missing/…
import { useEffect, useState } from 'react'
import { api, Target } from '../api'
import { useStore } from '../store'
import { useBackLayer } from '../back'
import { FolderPicker } from './FolderPicker'

export function MiroFind({ noteKey, target, onClose, onPlaced }: { noteKey: string; target: Target; onClose: () => void; onPlaced: () => void }) {
  const s = useStore()
  const [info, setInfo] = useState<{ title: string; has_picture: boolean } | null>(null)
  const [err, setErr] = useState('')
  const [folders, setFolders] = useState<string[]>([])
  const [picking, setPicking] = useState(false)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [names, setNames] = useState('')
  const [found, setFound] = useState<{ id: number; dist: number | null }[]>([])
  const [total, setTotal] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [at, setAt] = useState(0)
  const [flip, setFlip] = useState(false)
  useBackLayer(true, () => { if (picking) setPicking(false); else onClose() })

  useEffect(() => {
    api.miroMissing(noteKey).then(r => { setInfo(r); setFound(r.alts.map(id => ({ id, dist: null }))) }).catch(e => setErr(e.message))
  }, [noteKey])

  const search = async () => {
    setBusy(true); setErr('')
    try {
      const r = await api.miroMissingSearch(noteKey, { folders: folders.length ? folders : undefined, date_from: from || undefined, date_to: to || undefined, names: names.trim() || undefined })
      setFound(r.photos); setTotal(r.total); setAt(0)
    } catch (e: any) { setErr(e.message) }
    setBusy(false)
  }
  const cur = found[Math.min(at, found.length - 1)]
  const place = async () => {
    if (!cur || busy) return
    setBusy(true)
    try {
      const r = await api.miroMissingPlace(noteKey, target, cur.id)
      s.toast(r.warning || 'Put the photo where the note was')
      onPlaced()
    } catch (e: any) { setErr(e.message); setBusy(false) }
  }

  useEffect(() => {
    if (picking) {
      const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setPicking(false) } }
      window.addEventListener('keydown', esc, true)
      return () => window.removeEventListener('keydown', esc, true)
    }
    const down = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) {
        if (e.key === 'Enter') { e.preventDefault(); search() }
        if (e.key === 'Escape') (e.target as HTMLElement).blur()
        return
      }
      const k = e.key.toLowerCase()
      let used = true
      if (k === 'escape') onClose()
      else if (k === 'arrowright' || k === 'arrowdown') setAt(a => Math.min(found.length - 1, a + 1))
      else if (k === 'arrowleft' || k === 'arrowup') setAt(a => Math.max(0, a - 1))
      else if (k === 'y' || k === 'enter') place()
      else if (/^[1-9]$/.test(k) && found[+k - 1]) setAt(+k - 1)
      else if (k === ' ') setFlip(true)
      else used = false
      if (used) { e.preventDefault(); e.stopPropagation() }
    }
    const up = (e: KeyboardEvent) => { if (e.key === ' ') setFlip(false) }
    window.addEventListener('keydown', down, true)
    window.addEventListener('keyup', up, true)
    return () => { window.removeEventListener('keydown', down, true); window.removeEventListener('keyup', up, true) }
  })

  const pic = `/api/miro/missing/${noteKey}/picture`
  return (
    <div className="mreview" role="dialog" aria-label="Find the missing photo">
      <div className="mreview-top" style={{ flexWrap: 'wrap' }}>
        <span className="display" style={{ fontSize: 16 }}>Find the missing photo{info?.title ? `: ${info.title}` : ''}</span>
        <button className="btn" onClick={() => setPicking(true)}>{folders.length ? `${folders.length} folder${folders.length > 1 ? 's' : ''}…` : 'Choose folders…'}</button>
        <label className="row small muted" style={{ gap: 4 }}>taken from <input className="input" type="date" value={from} onChange={e => setFrom(e.target.value)} /></label>
        <label className="row small muted" style={{ gap: 4 }}>to <input className="input" type="date" value={to} onChange={e => setTo(e.target.value)} /></label>
        <input className="input" style={{ width: 200 }} placeholder="or file names" aria-label="File names" value={names} onChange={e => setNames(e.target.value)} />
        <button className="btn primary" disabled={busy || !(folders.length || from || to || names.trim())} onClick={search}>{busy ? 'Looking…' : 'Look'}</button>
        <span className="grow" />
        <button className="btn" onClick={onClose}>Close</button>
      </div>
      {err && <div className="small" style={{ color: '#ff8b8e', padding: '6px 14px' }}>{err}</div>}
      <div className="mreview-main">
        <figure>{info?.has_picture ? <img src={pic} alt="Miro picture" /> : <div className="center muted">{info ? 'Miro’s picture was not kept for this note' : 'Loading…'}</div>}<figcaption>Miro</figcaption></figure>
        <figure>
          {flip && info?.has_picture ? <img src={pic} alt="Miro picture" /> : cur ? <img src={`/api/preview/${cur.id}?s=l`} alt="Library photo" />
            : <div className="center muted" style={{ padding: 20, textAlign: 'center' }}>Choose the folders or dates it could be in (or type its name), then Look. The closest photos come first.</div>}
          <figcaption>{flip ? 'Miro (Space held)' : cur ? `Candidate ${at + 1} of ${found.length}${total != null && total > found.length ? ` (closest of ${total})` : ''}` : ''}</figcaption>
        </figure>
      </div>
      <div className="mreview-bottom">
        {found.length > 0 && (
          <div className="mreview-alts mfind-alts">
            {found.map((c, n) => (
              <button key={c.id} className={'malt' + (n === at ? ' on' : '')} onClick={() => setAt(n)} onDoubleClick={() => { setAt(n); place() }} title={n < 9 ? `Show (${n + 1})` : 'Show'}>
                <img src={`/api/preview/${c.id}?s=t`} alt="" loading="lazy" />{n < 9 && <span>{n + 1}</span>}
              </button>
            ))}
          </div>
        )}
        <div className="row" style={{ gap: 6 }}>
          <button className="btn" onClick={() => setAt(a => Math.max(0, a - 1))} disabled={!found.length || at === 0}>← Previous</button>
          <button className="btn primary" onClick={place} disabled={!cur || busy}>Put this one where the note is (Y)</button>
          <button className="btn" onClick={() => setAt(a => Math.min(found.length - 1, a + 1))} disabled={at >= found.length - 1}>Next →</button>
        </div>
        <span className="tiny muted">← → or 1–9 go through the candidates · Y / Enter put it on the board · hold Space to see Miro’s picture in its place · Esc close</span>
      </div>
      {picking && <>
        <div className="backdrop" style={{ zIndex: 60 }} onClick={() => setPicking(false)} />
        <div className="dialog mirofolders" style={{ zIndex: 61 }} role="dialog" aria-label="Folders to look in">
          <span className="display" style={{ fontSize: 17, fontWeight: 500 }}>Folders to look in</span>
          <FolderPicker picked={folders} sub hideSub onPicked={setFolders} onSub={() => {}} />
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn ghost" onClick={() => setFolders([])}>Clear</button>
            <button type="button" className="btn primary" onClick={() => { setPicking(false) }}>Done{folders.length ? ` (${folders.length})` : ''}</button>
          </div>
        </div>
      </>}
    </div>
  )
}
