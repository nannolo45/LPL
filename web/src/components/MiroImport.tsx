// SPDX-License-Identifier: GPL-3.0-or-later
// "Import from Miro…": read a Miro board, show what matched, then make a new series laid out like it (server: miro.py).
import { useEffect, useRef, useState } from 'react'
import { api, MiroPreview, MiroRunning } from '../api'
import { useStore } from '../store'
import { useBackLayer } from '../back'

const HOW: Record<string, string> = { name: 'by file name', 'name+picture': 'by name, told apart by the picture', picture: 'by picture', likely: 'by picture, probably right (below to check)' }
const STAGE: Record<MiroRunning['stage'], string> = {
  board: 'Reading the board…', names: 'Reading file names', library: 'Preparing the library’s photos for comparing (once; later imports reuse it)', pictures: 'Comparing Miro’s pictures with the library',
}

export function MiroImport({ onClose, onDone }: { onClose: () => void; onDone: (id: number) => void }) {
  const s = useStore()
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [hasToken, setHasToken] = useState<boolean | null>(null)
  const [folder, setFolder] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [run, setRun] = useState<MiroRunning | null>(null)
  const [making, setMaking] = useState(false)
  const [err, setErr] = useState('')
  const [p, setP] = useState<MiroPreview | null>(null)
  const [use, setUse] = useState<Record<string, boolean>>({})
  const [name, setName] = useState('')
  const [missingNotes, setMissingNotes] = useState(true)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])
  useBackLayer(true, onClose)
  useEffect(() => { api.miroState().then(r => setHasToken(r.has_token)).catch(() => setHasToken(false)) }, [])
  const big = s.isMobile ? ' big' : ''
  const busy = !!run || making

  const got = (r: MiroPreview) => {
    setRun(null); setP(r); setName(r.name_taken ? r.name + ' (Miro)' : r.name); setHasToken(true)
    setUse(Object.fromEntries(r.check.map(c => [c.miro_id, c.placed])))
  }
  const read = async () => {
    setErr('')
    try {
      let r = await api.miroPreview(url.trim(), token.trim() || undefined, { folder: folder.trim() || undefined, date_from: from || undefined, date_to: to || undefined })
      while (r.state === 'running' && alive.current) {
        setRun(r)
        await new Promise(res => setTimeout(res, 1000))
        r = await api.miroProgress(r.key)
      }
      if (r.state === 'done') got(r)
    } catch (e: any) { setRun(null); setErr(e?.message || String(e)) }
  }
  const make = async () => {
    if (!p) return
    setMaking(true); setErr('')
    const changes: Record<string, number | null> = {}
    for (const c of p.check) if (use[c.miro_id] !== c.placed) changes[c.miro_id] = use[c.miro_id] ? c.photo : null
    try { const r = await api.miroImport(p.key, name.trim(), missingNotes, changes); onDone(r.id) } catch (e: any) { setErr(e?.message || String(e)); setMaking(false) }
  }
  const skipped = p ? Object.entries(p.skipped) : []
  const placed = p ? p.matched + p.check.filter(c => !c.placed && use[c.miro_id]).length - p.check.filter(c => c.placed && !use[c.miro_id]).length : 0

  return (
    <>
      <div className="backdrop" onClick={busy ? undefined : onClose} />
      <form className={(s.isMobile ? 'sheet' : 'dialog') + ' mirodlg'} role="dialog" aria-label="Import from Miro"
        onSubmit={e => { e.preventDefault(); if (busy) return; if (p) make(); else if (url.trim() && (hasToken || token.trim())) read() }}>
        {s.isMobile && <span className="grab" />}
        <span className="display" style={{ fontSize: 19, fontWeight: 500 }}>Import from Miro</span>
        {!p && !run && <>
          <span className="soft" style={{ lineHeight: 1.5 }}>A new series laid out like the Miro board: frames stay frames (also inside each other), sticky notes become notes and connectors arrows. Nothing on Miro changes.</span>
          <label className="col" style={{ gap: 4 }}><span className="small muted">Board link</span>
            <input className="input" autoFocus placeholder="https://miro.com/app/board/…" value={url} onChange={e => setUrl(e.target.value)} /></label>
          {hasToken === false || token ? (
            <label className="col" style={{ gap: 4 }}><span className="small muted">Miro access token</span>
              <input className="input" type="password" autoComplete="off" placeholder="Paste the token" value={token} onChange={e => setToken(e.target.value)} />
              <span className="small muted" style={{ lineHeight: 1.45 }}>In Miro: profile picture → Settings → Your apps → Create new app; give it <b>boards:read</b>, install it on the team that has the board and copy the access token. LPL keeps it on the server for next time.</span>
            </label>
          ) : hasToken && (
            <span className="small muted">Using the Miro token saved earlier. <button type="button" className="linkbtn" onClick={() => { api.miroForget().then(() => setHasToken(false)) }}>Forget it</button></span>
          )}
          <div className="col" style={{ gap: 6 }}>
            <span className="small muted" style={{ lineHeight: 1.45 }}>Miro does not tell other apps the file names of pictures dropped onto a board, so LPL finds the photos by how they look. Telling it where to look is faster and surer (optional):</span>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              <input className="input grow" style={{ minWidth: 160 }} placeholder="Folder name contains…" aria-label="Folder name contains" value={folder} onChange={e => setFolder(e.target.value)} />
              <label className="row small muted" style={{ gap: 4 }}>taken from <input className="input" type="date" value={from} onChange={e => setFrom(e.target.value)} /></label>
              <label className="row small muted" style={{ gap: 4 }}>to <input className="input" type="date" value={to} onChange={e => setTo(e.target.value)} /></label>
            </div>
          </div>
        </>}
        {run && (
          <div className="col" style={{ gap: 8 }}>
            <span className="small">{STAGE[run.stage] || 'Working…'}{run.total ? ` — ${run.done} of ${run.total}` : ''}</span>
            <div className="mirobar"><span style={{ width: run.total ? `${Math.round(100 * run.done / run.total)}%` : '8%' }} /></div>
            <span className="small muted">You can close this; nothing is made until you say so.</span>
          </div>
        )}
        {p && <>
          <div className="col" style={{ gap: 6, lineHeight: 1.5 }}>
            <span><b>{p.matched}</b> of {p.pictures} pictures found in the library{p.duplicates ? ` (${p.duplicates} more were the same photo twice; it goes on the board once)` : ''}.</span>
            {Object.entries(p.how).map(([k, n]) => <span key={k} className="small muted">· {n} {HOW[k] || k}</span>)}
            <span className="small">{p.frames} frame{p.frames === 1 ? '' : 's'}, {p.notes} note{p.notes === 1 ? '' : 's'}, {p.arrows} arrow{p.arrows === 1 ? '' : 's'}.</span>
            {skipped.length > 0 && <span className="small muted">Left out: {skipped.map(([k, n]) => `${n} ${k.replace(/_/g, ' ')}`).join(', ')}.</span>}
          </div>
          {p.check.length > 0 && (
            <details className="col" style={{ gap: 6 }} open>
              <summary className="small" style={{ cursor: 'pointer' }}>{p.check.length} to check: Miro’s picture, then the library photo. Click a pair to take it or leave it out.</summary>
              <div className="miropairs">
                {p.check.map(c => (
                  <button type="button" key={c.miro_id} className={'miropair' + (use[c.miro_id] ? ' on' : '')} aria-pressed={!!use[c.miro_id]}
                    onClick={() => setUse(u => ({ ...u, [c.miro_id]: !u[c.miro_id] }))}>
                    <img src={`/api/miro/pic/${p.key}/${c.miro_id}`} alt="Miro picture" loading="lazy" />
                    <img src={`/api/preview/${c.photo}?s=t`} alt="Library photo" loading="lazy" />
                    <span className="small">{use[c.miro_id] ? '✓ take' : 'leave out'}</span>
                  </button>
                ))}
              </div>
            </details>
          )}
          {p.unmatched.length > 0 && (
            <details className="col" style={{ gap: 4 }}>
              <summary className="small" style={{ cursor: 'pointer' }}>{p.unmatched.length} picture{p.unmatched.length === 1 ? '' : 's'} not found</summary>
              <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={missingNotes} onChange={e => setMissingNotes(e.target.checked)} /> Leave a grey “Not found” note where each one was</label>
              <span className="small muted">If many are missing, go Back and tell it where to look (folder or dates).</span>
            </details>
          )}
          <label className="col" style={{ gap: 4 }}><span className="small muted">Name of the new series</span>
            <input className="input" value={name} onChange={e => setName(e.target.value)} /></label>
        </>}
        {err && <span className="small" style={{ color: 'var(--danger, #e5636a)' }}>{err}</span>}
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          {p && <button type="button" className={'btn' + big} disabled={busy} onClick={() => { setP(null) }}>Back</button>}
          <button type="button" className={'btn' + big} disabled={making} onClick={onClose}>{run ? 'Close' : 'Cancel'}</button>
          {!p
            ? <button type="submit" className={'btn primary' + big} disabled={busy || !url.trim() || !(hasToken || token.trim())}>{run ? 'Reading…' : 'Read the board'}</button>
            : <button type="submit" className={'btn primary' + big} disabled={busy || !name.trim() || !placed}>{making ? 'Making the series…' : `Make the series (${placed} photos)`}</button>}
        </div>
      </form>
    </>
  )
}
