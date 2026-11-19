// SPDX-License-Identifier: GPL-3.0-or-later
// "last edited on Desktop · 18 Nov 14:05 · exposure" with the list of edits behind it (R15), and the progress
// bar of a device that downloads or syncs a photo (R16). See docs/android-edit-record-progress.md.
import { useEffect, useState } from 'react'
import { api, EditEvent, Photo } from '../api'
import { useStore } from '../store'
import { allModules, changeLines, editWhere, entryWhen, lastEditLine } from '../edits'

/** Re-render every few seconds while on, so a progress bar goes away when its device stops reporting
 *  even if this page stops getting answers from the server. */
export function useTick(on: boolean, ms = 2000) {
  const [, setN] = useState(0)
  useEffect(() => {
    if (!on) return
    const t = window.setInterval(() => setN(n => n + 1), ms)
    return () => window.clearInterval(t)
  }, [on, ms])
}

/** A thin bar under the step in words: "on Phone: downloading · 42%". fraction null: busy, amount unknown. */
export function ProgressBar({ label, fraction }: { label: string; fraction: number | null }) {
  const pct = fraction == null ? null : Math.round(fraction * 100)
  return (
    <span className="progress" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined}>
      <span className="small soft">{label}{pct != null ? ` · ${pct}%` : ''}</span>
      <span className={'progress-track' + (pct == null ? ' busy' : '')}><i style={pct != null ? { width: `${pct}%` } : undefined} /></span>
    </span>
  )
}

/** The photo's last edit; a tap shows every edit, newest first. Nothing when LPL has no record of an edit. */
export function LastEdit({ p }: { p: Photo }) {
  const s = useStore()
  const [last, setLast] = useState<EditEvent | null>(null)
  const [list, setList] = useState<EditEvent[] | null>(null)
  const [open, setOpen] = useState(false)
  // fetched again when the photo changes, and when a device reports that it synced this photo
  const synced = Math.max(0, ...s.agents.flatMap(a => (a.state?.working_set || []).filter(w => w.id === p.id).map(w => w.synced_ts || 0)))
  const key = `${p.id}:${p.change_ts}:${p.preview_ver}:${p.edited}:${synced}`
  useEffect(() => { setOpen(false); setList(null); setLast(null) }, [p.id])
  useEffect(() => {
    let alive = true
    api.photo(p.id).then(d => { if (alive) setLast(d.last_edit ?? null) }).catch(() => {})
    if (open) api.edits(p.id).then(r => { if (alive) setList(r.edits) }).catch(() => {})
    return () => { alive = false }
  }, [key])
  if (!last) return null
  const toggle = () => {
    if (!open) api.edits(p.id).then(r => setList(r.edits)).catch(e => s.toast(e.message))
    setOpen(!open)
  }
  return (
    <div className="col editrec">
      <button className="linkbtn small soft" aria-expanded={open} title={open ? 'Hide the list of edits' : 'Show every edit of this photo'} onClick={toggle}>
        {lastEditLine(last, undefined, s.agents)} <span className="muted">{open ? '▴' : '▾'}</span>
      </button>
      {open && <EditList edits={list} />}
    </div>
  )
}

export function EditList({ edits }: { edits: EditEvent[] | null }) {
  const s = useStore()
  if (!edits) return <span className="small muted">Loading…</span>
  return (
    <ol className="editlist" aria-label="Edits of this photo, newest first">
      {edits.map(e => <EditEntry key={e.id} e={e} agents={s.agents} />)}
    </ol>
  )
}

function EditEntry({ e, agents }: { e: EditEvent; agents: Parameters<typeof editWhere>[1] }) {
  const [all, setAll] = useState(false)
  return (
    <li>
      <span className="small">{entryWhen(e)} · edited {editWhere(e, agents)}</span>
      {changeLines(e).map((l, i) => <span key={i} className="small muted">{l}</span>)}
      {e.modules.length > 0 && <button className="linkbtn" style={{ fontSize: 11, color: 'var(--muted)' }} aria-expanded={all} onClick={() => setAll(!all)}>
        {all ? 'hide the modules' : `all modules (${e.modules.length})`} {all ? '▴' : '▾'}</button>}
      {all && <span className="tiny muted" aria-label="All modules of this edit">{allModules(e).join(', ')}</span>}
    </li>
  )
}
