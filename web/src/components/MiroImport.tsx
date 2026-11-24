// SPDX-License-Identifier: GPL-3.0-or-later
// "Import from Miro…": read a Miro board, show what matched, then make a new series laid out like it (server: miro.py).
import { useEffect, useState } from 'react'
import { api, MiroPreview } from '../api'
import { useStore } from '../store'
import { useBackLayer } from '../back'

const HOW: Record<string, string> = { name: 'by file name', 'name+picture': 'by name, told apart by the picture', 'name (guess)': 'by name, best guess among several', picture: 'by picture' }

export function MiroImport({ onClose, onDone }: { onClose: () => void; onDone: (id: number) => void }) {
  const s = useStore()
  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [hasToken, setHasToken] = useState<boolean | null>(null)
  const [busy, setBusy] = useState<'' | 'read' | 'make'>('')
  const [err, setErr] = useState('')
  const [p, setP] = useState<MiroPreview | null>(null)
  const [name, setName] = useState('')
  const [missingNotes, setMissingNotes] = useState(true)
  useBackLayer(true, onClose)
  useEffect(() => { api.miroState().then(r => setHasToken(r.has_token)).catch(() => setHasToken(false)) }, [])
  const big = s.isMobile ? ' big' : ''

  const read = async () => {
    setBusy('read'); setErr('')
    try {
      const r = await api.miroPreview(url.trim(), token.trim() || undefined)
      setP(r); setName(r.name_taken ? r.name + ' (Miro)' : r.name); setHasToken(true)
    } catch (e: any) { setErr(e?.message || String(e)) }
    setBusy('')
  }
  const make = async () => {
    if (!p) return
    setBusy('make'); setErr('')
    try { const r = await api.miroImport(p.key, name.trim(), missingNotes); onDone(r.id) } catch (e: any) { setErr(e?.message || String(e)); setBusy('') }
  }
  const skipped = p ? Object.entries(p.skipped) : []

  return (
    <>
      <div className="backdrop" onClick={busy ? undefined : onClose} />
      <form className={(s.isMobile ? 'sheet' : 'dialog') + ' mirodlg'} role="dialog" aria-label="Import from Miro"
        onSubmit={e => { e.preventDefault(); if (busy) return; if (p) make(); else if (url.trim() && (hasToken || token.trim())) read() }}>
        {s.isMobile && <span className="grab" />}
        <span className="display" style={{ fontSize: 19, fontWeight: 500 }}>Import from Miro</span>
        {!p && <>
          <span className="soft" style={{ lineHeight: 1.5 }}>A new series laid out like the Miro board: its pictures become the library's photos (found by file name, so DSCF4821.jpg finds DSCF4821.RAF), frames stay frames (also inside each other), sticky notes become notes and connectors arrows. Nothing on Miro changes.</span>
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
        </>}
        {p && <>
          <div className="col" style={{ gap: 6, lineHeight: 1.5 }}>
            <span><b>{p.matched}</b> of {p.pictures} pictures found in the library{p.duplicates ? ` (${p.duplicates} more were the same photo twice; it goes on the board once)` : ''}.</span>
            {Object.entries(p.how).map(([k, n]) => <span key={k} className="small muted">· {n} {HOW[k] || k}</span>)}
            <span className="small">{p.frames} frame{p.frames === 1 ? '' : 's'}, {p.notes} note{p.notes === 1 ? '' : 's'}, {p.arrows} arrow{p.arrows === 1 ? '' : 's'}.</span>
            {skipped.length > 0 && <span className="small muted">Left out: {skipped.map(([k, n]) => `${n} ${k.replace(/_/g, ' ')}`).join(', ')}.</span>}
            {p.sample.length > 0 && <div className="row" style={{ gap: 4, flexWrap: 'wrap' }}>{p.sample.map(id => <img key={id} src={`/api/preview/${id}?s=t`} alt="" style={{ height: 44, borderRadius: 3 }} />)}</div>}
          </div>
          {p.unmatched.length > 0 && (
            <details className="col" style={{ gap: 4 }}>
              <summary className="small" style={{ cursor: 'pointer' }}>{p.unmatched.length} picture{p.unmatched.length === 1 ? '' : 's'} not found</summary>
              <div className="small muted" style={{ maxHeight: 140, overflow: 'auto', lineHeight: 1.6 }}>{p.unmatched.map(u => <div key={u.miro_id}>{u.title}</div>)}</div>
              <label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={missingNotes} onChange={e => setMissingNotes(e.target.checked)} /> Leave a grey “Not found” note where each one was</label>
            </details>
          )}
          <label className="col" style={{ gap: 4 }}><span className="small muted">Name of the new series</span>
            <input className="input" value={name} onChange={e => setName(e.target.value)} /></label>
        </>}
        {err && <span className="small" style={{ color: 'var(--danger, #e5636a)' }}>{err}</span>}
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          {p && <button type="button" className={'btn' + big} disabled={!!busy} onClick={() => { setP(null) }}>Back</button>}
          <button type="button" className={'btn' + big} disabled={busy === 'make'} onClick={onClose}>Cancel</button>
          {!p
            ? <button type="submit" className={'btn primary' + big} disabled={!!busy || !url.trim() || !(hasToken || token.trim())}>{busy ? 'Reading the board…' : 'Read the board'}</button>
            : <button type="submit" className={'btn primary' + big} disabled={!!busy || !name.trim() || !p.matched}>{busy ? 'Making the series…' : `Make the series (${p.matched} photos)`}</button>}
        </div>
      </form>
    </>
  )
}
