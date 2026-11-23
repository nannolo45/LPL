// SPDX-License-Identifier: GPL-3.0-or-later
// Full view from the Devices page: look at a photo full size before saving it to the NAS, removing it from the
// device, sending it again or clearing it from the queue. It steps through the rows the page shows (the filter
// chip that is on), keeps the page's selection (X, or the boxes in the strip) and offers what fits the photo's
// state. Keys: ← → step, X select, S save, O open in darktable, R retry, Delete clear / remove, Esc back.
// Phone: swipe left/right, a sheet with the same buttons.
import { useEffect, useRef, useState } from 'react'
import { Agent, api, Photo } from '../api'
import { useStore } from '../store'
import { agentLabel, hasUnsavedEdits, STATUS } from '../devices'
import { ago } from '../transfer'
import { exifLine, fmtDay } from '../fields'
import type { Actions } from './Devices'
import { rowInfos } from './Devices'

export interface FullView { agent: string; order: number[]; id: number }

export function DeviceFullView({ a, view, setView, act, now, sel, setSel }: { a: Agent; view: FullView; setView: (v: FullView | null) => void; act: Actions; now: number; sel: number[]; setSel: (ids: number[]) => void }) {
  const s = useStore()
  const phone = s.isMobile
  const name = agentLabel(a, s.agents)
  const rows = rowInfos(a, 'all')
  const row = rows.find(r => r.id === view.id)
  const i = view.order.indexOf(view.id)
  const [photo, setPhoto] = useState<Photo | null>(null)
  const touch = useRef<{ x: number; y: number } | null>(null)
  const strip = useRef<HTMLDivElement>(null)

  const close = () => setView(null)
  const go = (d: number) => {
    const n = i + d
    if (n >= 0 && n < view.order.length) setView({ ...view, id: view.order[n] })
  }
  /** After an action that takes the photo off the list: the next one, else the one before, else back. */
  const after = () => {
    const rest = view.order.filter(x => x !== view.id)
    if (!rest.length) return close()
    setView({ ...view, order: rest, id: rest[Math.min(i, rest.length - 1)] })
  }
  const toggle = () => setSel(sel.includes(view.id) ? sel.filter(x => x !== view.id) : [...sel, view.id])

  useEffect(() => {
    let live = true
    setPhoto(null)
    api.photo(view.id).then(p => { if (live) setPhoto(p) }).catch(() => {})
    return () => { live = false }
  }, [view.id])
  useEffect(() => {
    strip.current?.querySelector<HTMLElement>('.on')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [view.id])

  const w = row?.w
  const unsaved = !!w && hasUnsavedEdits(w)
  type Act = { label: string; key?: string; keyLabel?: string; primary?: boolean; danger?: boolean; title?: string; run: () => unknown }
  const acts: Act[] = !row ? [] : row.kind === 'failed' ? [
    { label: 'Retry', key: 'r', keyLabel: 'R', primary: true, title: `Send it to ${name} again`, run: () => { act.retry(a.id, [row.id]); after() } },
    { label: 'Clear from queue', key: 'Delete', keyLabel: 'Del', title: `Take it off the Devices page. Nothing of it is on ${name}.`, run: () => { act.clear(a.id, [row.id]); after() } },
  ] : row.kind === 'arrived' ? [
    ...(unsaved ? [
      { label: 'Save edits to NAS', key: 's', keyLabel: 'S', primary: true, run: () => act.job('sync', a.id, [row.id]) },
      { label: `Save to NAS and remove from ${name}`, key: 'S', keyLabel: '⇧S', title: `Edits go to the NAS first; then the working copy on ${name} is deleted.`, run: () => { act.job('release', a.id, [row.id]); after() } },
    ] : []),
    { label: `Open in darktable on ${name}`, key: 'o', keyLabel: 'O', primary: !unsaved, run: () => act.job('open', a.id, [row.id]) },
    ...(unsaved ? [
      { label: `Discard edits and remove from ${name}…`, key: 'Delete', keyLabel: 'Del', danger: true,
        run: () => { if (window.confirm(`Delete ${row.filename} on ${name} WITHOUT saving its edits to the NAS? The NAS keeps the version it has.`)) { act.forget(a.id, [row.id], 'discard'); after() } } },
    ] : [
      { label: `Remove from ${name}`, key: 'Delete', keyLabel: 'Del', danger: true, title: `Its edits are on the NAS already; the working copy on ${name} is deleted.`, run: () => { act.job('release', a.id, [row.id]); after() } },
      { label: `Forget, keep the copy on ${name}`, title: `LPL stops tracking it; the files stay on ${name}.`, run: () => { act.forget(a.id, [row.id], 'keep'); after() } },
    ]),
  ] : row.droppable ? [
    { label: row.kind === 'unsent' ? 'Leave it out when resuming' : 'Don’t send', key: 'Delete', keyLabel: 'Del', run: () => { act.dontSend(a.id, [row.id]); after() } },
  ] : []

  useEffect(() => {
    const keys = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t.closest('input, textarea, select, [contenteditable=true]') || e.ctrlKey || e.metaKey || e.altKey) return
      const k = e.key
      if (k === 'Escape') { e.preventDefault(); e.stopPropagation(); close() }
      else if (k === 'ArrowRight' || k === 'ArrowDown') { e.preventDefault(); go(1) }
      else if (k === 'ArrowLeft' || k === 'ArrowUp') { e.preventDefault(); go(-1) }
      else if (k === 'x' || k === 'X') { e.preventDefault(); toggle() }
      else {
        const hit = acts.find(x => x.key === k || (x.key === 'Delete' && k === 'Backspace'))
        if (hit) { e.preventDefault(); hit.run() }
      }
    }
    window.addEventListener('keydown', keys, true)
    return () => window.removeEventListener('keydown', keys, true)
  })

  const st = !row ? null : row.kind === 'failed' ? { cls: 'bad', head: `Couldn’t send to ${name}`, text: row.f?.error || 'The copy from the NAS did not complete. Nothing of it is on ' + name + '.' }
    : row.kind === 'arrived' ? (unsaved
      ? { cls: 'warn', head: `Edited on ${name}, not saved to the NAS`, text: `The picture is the version on the NAS: ${name}'s edits show here once they are saved (darktable sends its preview with them).` }
      : w!.status === 'synced' ? { cls: 'ok', head: `On ${name} · edits saved to the NAS`, text: w!.synced_ts ? `Saved ${ago(w!.synced_ts, now)}.` : '' }
        : { cls: 'ok', head: `On ${name} · ${STATUS[w!.status]?.[0] || w!.status}`, text: w!.status === 'error' ? w!.error || '' : w!.checkout_ts ? `Arrived ${ago(w!.checkout_ts, now)}.` : '' })
      : row.kind === 'copying' ? { cls: '', head: `Being copied to ${name}`, text: '' }
        : row.kind === 'waiting' ? { cls: '', head: `Waiting to be sent to ${name}`, text: '' }
          : { cls: '', head: 'Not sent (the batch was stopped)', text: 'Resume sends it.' }
  const v = w?.synced_ts || photo?.preview_ver || 0
  const img = <img className="dfv-img" src={`/api/preview/${view.id}?s=l&v=${v}`} alt={row?.filename || ''} draggable={false} />
  const info = photo && <div className="col" style={{ gap: 3 }}>
    <span className="small soft">{photo.folder}{photo.taken ? ` · ${fmtDay(photo.taken)}` : ''}</span>
    {exifLine(photo) && <span className="tiny muted">{exifLine(photo)}</span>}
  </div>
  const buttons = <div className="col" style={{ gap: 8 }}>
    {acts.map(x => <button key={x.label} className={'btn dfv-act' + (x.primary ? ' primary' : '') + (x.danger ? ' danger' : '') + (phone ? ' big' : '')} title={x.title} onClick={x.run}>
      <span>{x.label}</span>{!phone && x.keyLabel && <kbd>{x.keyLabel}</kbd>}</button>)}
    {!phone && <button className="btn dfv-act" onClick={() => { close(); s.reveal(view.id) }}><span>Show in library</span></button>}
  </div>
  const chosen = sel.includes(view.id)
  const count = view.order.length
  const film = !phone && (
    <div className="dfv-strip" ref={strip}>
      {view.order.map(id => {
        const on = sel.includes(id)
        return <div key={id} className={'dfv-cell' + (id === view.id ? ' on' : '')}>
          <button type="button" className="dfv-thumb" aria-label={`Show photo ${rows.find(r => r.id === id)?.filename || id}`} onClick={() => setView({ ...view, id })}>
            <img src={`/api/preview/${id}?s=t`} alt="" draggable={false} /></button>
          <input type="checkbox" className="devcheck dfv-check" aria-label="Select" checked={on} onChange={() => setSel(on ? sel.filter(x => x !== id) : [...sel, id])} />
        </div>
      })}
    </div>
  )
  const swipe = {
    onTouchStart: (e: React.TouchEvent) => { touch.current = { x: e.touches[0].clientX, y: e.touches[0].clientY } },
    onTouchEnd: (e: React.TouchEvent) => {
      const t = touch.current; touch.current = null
      if (!t) return
      const dx = e.changedTouches[0].clientX - t.x, dy = e.changedTouches[0].clientY - t.y
      if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) go(dx < 0 ? 1 : -1)
    },
  }
  const where = `${i + 1} of ${count}`
  if (phone) return (
    <div className="dfv m" role="dialog" aria-modal="true" aria-label={`Full view: ${row?.filename || ''}`}>
      <div className="row dfv-top">
        <button className="btn ghost big" aria-label="Back to Devices" onClick={close}>←</button>
        <div className="col grow" style={{ minWidth: 0 }}><span className="ellipsis">{row?.filename || `photo ${view.id}`}</span><span className="tiny muted">{name} · {where}</span></div>
        <button className={'btn ghost big dfv-pick' + (chosen ? ' on' : '')} aria-pressed={chosen} aria-label={chosen ? 'Unselect' : 'Select'} onClick={toggle}>✓</button>
      </div>
      <div className="dfv-stage" {...swipe}>{img}</div>
      <div className="dfv-sheet">
        {st && <div className={'dfv-state ' + st.cls}><b>{st.head}</b>{st.text && <span className="small">{st.text}</span>}</div>}
        {info}
        {buttons}
      </div>
    </div>
  )
  return (
    <div className="dfv" role="dialog" aria-modal="true" aria-label={`Full view: ${row?.filename || ''}`}>
      <div className="row dfv-top">
        <button className="btn ghost" onClick={close}>← Devices</button>
        <span className="display" style={{ fontSize: 16 }}>{name}</span>
        <span className="small muted">{where}{sel.length ? ` · ${sel.length} selected` : ''}</span>
        <div className="grow" />
        <span className="tiny muted"><kbd>←</kbd> <kbd>→</kbd> step · <kbd>X</kbd> select · <kbd>Esc</kbd> back</span>
        <button className="btn ghost" aria-label="Close full view" onClick={close}>×</button>
      </div>
      <div className="row dfv-body">
        <div className="col grow" style={{ minWidth: 0, minHeight: 0 }}>
          <div className="dfv-stage">
            <button className="btn dfv-nav" aria-label="Previous photo" disabled={i <= 0} onClick={() => go(-1)}>‹</button>
            {img}
            <button className="btn dfv-nav" aria-label="Next photo" disabled={i >= count - 1} onClick={() => go(1)}>›</button>
          </div>
          {film}
        </div>
        <aside className="dfv-side" aria-label="About this photo">
          <div className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
            <span className="display grow" style={{ fontSize: 19, wordBreak: 'break-all' }}>{row?.filename || `photo ${view.id}`}</span>
            <label className="row tiny soft" style={{ gap: 6 }}><input type="checkbox" className="devcheck" checked={chosen} onChange={toggle} />Selected</label>
          </div>
          {info}
          {st ? <div className={'dfv-state ' + st.cls}><b>{st.head}</b>{st.text && <span className="small">{st.text}</span>}</div>
            : <div className="dfv-state"><b>No longer on {name}'s list</b></div>}
          {buttons}
        </aside>
      </div>
    </div>
  )
}
