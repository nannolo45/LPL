// SPDX-License-Identifier: GPL-3.0-or-later
// A "Not found" note left by Import from Miro: look for its photo full screen (by picture in chosen folders /
// dates, or by name) and put it where the note is. Server: miro.py, /api/miro/missing/…
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, Filters, Target } from '../api'
import { useStore } from '../store'
import { useBackLayer } from '../back'
import { WhereToLook } from './AddPhotos'

let lastLook: Filters | null = null   // the next note opens with the criteria used last

export function MiroFind({ noteKey, target, onClose, onPlaced }: { noteKey: string; target: Target; onClose: () => void; onPlaced: () => void }) {
  const s = useStore()
  const [info, setInfo] = useState<{ title: string; has_picture: boolean } | null>(null)
  const [err, setErr] = useState('')
  const [picking, setPicking] = useState(true)
  const [found, setFound] = useState<{ id: number; dist: number | null }[]>([])
  const [total, setTotal] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [at, setAt] = useState(0)
  const [flip, setFlip] = useState(false)
  useBackLayer(true, () => { if (picking) setPicking(false); else onClose() })

  useEffect(() => {
    api.miroMissing(noteKey).then(r => { setInfo(r); setFound(r.alts.map(id => ({ id, dist: null }))) }).catch(e => setErr(e.message))
  }, [noteKey])

  const search = async (filters: Filters) => {
    setBusy(true); setErr(''); lastLook = filters
    try {
      const r = await api.miroMissingSearch(noteKey, { filters })
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
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
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
  return createPortal(
    <div className="mreview" role="dialog" aria-label="Find the missing photo">
      <div className="mreview-top" style={{ flexWrap: 'wrap' }}>
        <span className="display" style={{ fontSize: 16 }}>Find the missing photo{info?.title ? `: ${info.title}` : ''}</span>
        <button className="btn primary" onClick={() => setPicking(true)}>{busy ? 'Looking…' : total == null ? 'Where to look…' : 'Look elsewhere…'}</button>
        {total != null && <span className="small muted">{total} photo{total === 1 ? '' : 's'} compared, the closest first</span>}
        <span className="grow" />
        <button className="btn" onClick={onClose}>Close</button>
      </div>
      {err && <div className="small" style={{ color: '#ff8b8e', padding: '6px 14px' }}>{err}</div>}
      <div className="mreview-main">
        <figure>{info?.has_picture ? <img src={pic} alt="Miro picture" /> : <div className="center muted">{info ? 'Miro’s picture was not kept for this note' : 'Loading…'}</div>}<figcaption>Miro</figcaption></figure>
        <figure>
          {flip && info?.has_picture ? <img src={pic} alt="Miro picture" /> : cur ? <img src={`/api/preview/${cur.id}?s=l`} alt="Library photo" />
            : <div className="center muted" style={{ padding: 20, textAlign: 'center' }}>{busy ? 'Comparing…' : 'Say where to look (folders, dates or names): the closest photos come first.'}</div>}
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
      {picking && <div className="mfind-look">
        <WhereToLook target={target} start={lastLook} onClose={() => setPicking(false)} onPick={f => { setPicking(false); search(f) }} />
      </div>}
    </div>,
    document.body,
  )
}
