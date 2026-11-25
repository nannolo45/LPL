// SPDX-License-Identifier: GPL-3.0-or-later
// "Import from Miro…": read a Miro board, show what matched, then make a new series laid out like it (server: miro.py).
import { useEffect, useRef, useState } from 'react'
import { api, MiroPreview, MiroRunning } from '../api'
import { useStore } from '../store'
import { useBackLayer } from '../back'
import { FolderPicker } from './FolderPicker'
import { MiroReview } from './MiroReview'

const HOW: Record<string, string> = { name: 'by file name', 'name+picture': 'by name, told apart by the picture', picture: 'by picture', likely: 'by picture, probably right (below to check)' }
const STAGE: Record<MiroRunning['stage'], string> = {
  board: 'Reading the board…', names: 'Reading file names', library: 'Preparing the library’s photos for comparing (once; later imports reuse it)', pictures: 'Comparing Miro’s pictures with the library',
}

export function MiroImport({ onClose, onDone }: { onClose: () => void; onDone: (id: number) => void }) {
  const s = useStore()
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [hasToken, setHasToken] = useState<boolean | null>(null)
  const [folders, setFolders] = useState<string[]>([])
  const [picking, setPicking] = useState(false)
  const [reviewing, setReviewing] = useState(false)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [run, setRun] = useState<MiroRunning | null>(null)
  const [making, setMaking] = useState(false)
  const [err, setErr] = useState('')
  const [p, setP] = useState<MiroPreview | null>(null)
  const [choice, setChoiceAll] = useState<Record<string, number | null>>({})
  const setChoice = (mid: string, pid: number | null) => setChoiceAll(c => ({ ...c, [mid]: pid }))
  const [name, setName] = useState('')
  const [missingNotes, setMissingNotes] = useState(true)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])
  useBackLayer(true, onClose)
  useEffect(() => { api.miroState().then(r => setHasToken(r.has_token)).catch(() => setHasToken(false)) }, [])
  const big = s.isMobile ? ' big' : ''
  const busy = !!run || making
  useEffect(() => {  // Escape closes the folder picker first (the review has its own keys)
    if (!picking) return
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setPicking(false) } }
    window.addEventListener('keydown', h, true)
    return () => window.removeEventListener('keydown', h, true)
  }, [picking])

  const got = (r: MiroPreview) => {
    setRun(null); setP(r); setName(r.name_taken ? r.name + ' (Miro)' : r.name); setHasToken(true)
    setChoiceAll(Object.fromEntries(r.check.map(c => [c.miro_id, c.placed ? c.photo : null])))
  }
  const read = async () => {
    setErr('')
    try {
      let r = await api.miroPreview(url.trim(), token.trim() || undefined, { folders: folders.length ? folders : undefined, date_from: from || undefined, date_to: to || undefined })
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
    for (const c of p.check) { const was = c.placed ? c.photo : null; if ((choice[c.miro_id] ?? null) !== was) changes[c.miro_id] = choice[c.miro_id] ?? null }
    try { const r = await api.miroImport(p.key, name.trim(), missingNotes, changes); onDone(r.id) } catch (e: any) { setErr(e?.message || String(e)); setMaking(false) }
  }
  const skipped = p ? Object.entries(p.skipped) : []
  const placed = p ? p.matched + p.check.filter(c => !c.placed && choice[c.miro_id] != null).length - p.check.filter(c => c.placed && choice[c.miro_id] == null).length : 0
  const toCheck = p ? p.check.filter(c => c.kind !== 'sure').length : 0

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
              <button type="button" className="btn" onClick={() => setPicking(true)}>{folders.length ? `${folders.length} folder${folders.length > 1 ? 's' : ''} chosen…` : 'Choose folders…'}</button>
              {folders.length > 0 && <button type="button" className="btn ghost tiny" onClick={() => setFolders([])}>clear</button>}
              <label className="row small muted" style={{ gap: 4 }}>taken from <input className="input" type="date" value={from} onChange={e => setFrom(e.target.value)} /></label>
              <label className="row small muted" style={{ gap: 4 }}>to <input className="input" type="date" value={to} onChange={e => setTo(e.target.value)} /></label>
            </div>
            {folders.length > 0 && <span className="small muted ellipsis" title={folders.join('\n')}>{folders.map(f => f.slice(f.lastIndexOf('/') + 1)).join(' · ')}</span>}
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
            {!!p.unreadable && <span className="small" style={{ color: '#f0a35a' }}>{p.unreadable} picture{p.unreadable > 1 ? 's' : ''} could not be downloaded from Miro. Back, then Read the board again: the ones already downloaded are kept, only these are tried again.</span>}
            {skipped.length > 0 && <span className="small muted">Left out: {skipped.map(([k, n]) => `${n} ${k.replace(/_/g, ' ')}`).join(', ')}.</span>}
          </div>
          {p.check.length > 0 && (
            <div className="col" style={{ gap: 6 }}>
              <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                <button type="button" className="btn primary" onClick={() => setReviewing(true)}>{toCheck ? `Check ${toCheck} match${toCheck > 1 ? 'es' : ''} full screen` : 'Look through the matches full screen'}</button>
                <span className="small muted">{p.check.length - toCheck} sure by picture · keyboard: Y take, N leave out</span>
              </div>
              {toCheck > 0 && (
                <div className="miropairs">
                  {p.check.filter(c => c.kind !== 'sure').map(c => (
                    <button type="button" key={c.miro_id} className={'miropair' + (choice[c.miro_id] != null ? ' on' : '')} aria-pressed={choice[c.miro_id] != null}
                      onClick={() => setChoice(c.miro_id, choice[c.miro_id] != null ? null : c.photo)}>
                      <img src={`/api/miro/pic/${p.key}/${c.miro_id}`} alt="Miro picture" loading="lazy" />
                      <img src={`/api/preview/${choice[c.miro_id] ?? c.photo}?s=t`} alt="Library photo" loading="lazy" />
                      <span className="small">{choice[c.miro_id] != null ? '✓ take' : 'leave out'}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {p.unmatched.length > 0 && (
            <details className="col" style={{ gap: 4 }}>
              <summary className="small" style={{ cursor: 'pointer' }}>{p.unmatched.length} picture{p.unmatched.length === 1 ? '' : 's'} not found</summary>
              <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={missingNotes} onChange={e => setMissingNotes(e.target.checked)} /> Leave a grey “Not found” note where each one was</label>
              <span className="small muted">If many are missing, go Back and choose the folders or dates to look in.</span>
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
      {picking && <>
        <div className="backdrop" style={{ zIndex: 40 }} onClick={() => setPicking(false)} />
        <div className="dialog mirofolders" role="dialog" aria-label="Folders to look in">
          <span className="display" style={{ fontSize: 17, fontWeight: 500 }}>Folders to look in</span>
          <span className="small muted">Tick every folder the board's photos come from (a folder includes the ones inside it).</span>
          <FolderPicker picked={folders} sub hideSub onPicked={setFolders} onSub={() => {}} />
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn ghost" onClick={() => setFolders([])}>Clear</button>
            <button type="button" className="btn primary" onClick={() => setPicking(false)}>Done{folders.length ? ` (${folders.length})` : ''}</button>
          </div>
        </div>
      </>}
      {reviewing && p && <MiroReview items={p.check} pkey={p.key} choice={choice} setChoice={setChoice} onClose={() => setReviewing(false)} />}
    </>
  )
}
