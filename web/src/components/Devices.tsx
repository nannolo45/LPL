// SPDX-License-Identifier: GPL-3.0-or-later
// The Devices page (desktop: its own tab; phone: a page behind the header's indicator): every device as a row
// that folds, with what is being sent to it (overall bar, time left, Stop), what a stop left (Resume, remove
// the copies), and its photos. What used to sit on this page and is rarely needed (the NAS library, a
// device's details) is in Settings > Devices & library. See docs/devices-stop-estimate.md.
import { useEffect, useState } from 'react'
import { Agent, api, WorkItem } from '../api'
import { useStore } from '../store'
import { agentLabel, devicePills, hasUnsavedEdits, isPhoneAgent, itemProgress, serverNow, STATUS } from '../devices'
import { ago, deviceCounts, deviceRows, FailedItem, filterCounts, filterRows, isFolded, onlineCount, reporting, RowFilter, stoppedLines, TransferItem, transferLines } from '../transfer'
import { Bar, Chevron, CrossIcon, PlayIcon, StopIcon } from './DevicesBits'
import { devicesApi, refreshStatus } from '../devicesApi'
import { SharedSection } from './Shared'
import { ProgressBar, useTick } from './EditRecord'
import { SettingsDialog } from './Shortcuts'
import { boxState, bulkActions, prune, rangeIds, RowKind, Selection, SelRow, toggleGroup, toggleId } from '../selection'
import { DeviceFullView, FullView } from './DeviceFullView'
import { MenuItem, RowMenu } from './RowMenu'

/** What the page and the top bar's popover do with a device; each asks the server and fetches the result at once. */
export function useDeviceActions() {
  const s = useStore()
  const label = (id: string) => { const a = s.agents.find(x => x.id === id); return a ? agentLabel(a, s.agents) : id }
  const run = (p: Promise<unknown>, done?: string) => p.then(() => { if (done) s.toast(done) }).catch(e => s.toast(e.message || String(e))).finally(refreshStatus)
  return {
    label,
    job: (kind: string, agent: string, ids?: number[]) => run(api.job(kind, ids ? { ids } : { all: true }, agent), `Sent to ${label(agent)}`),
    stop: (id: string) => run(devicesApi.stop(id)),
    resume: (id: string) => run(devicesApi.resume(id)),
    dontSend: (id: string, ids: number[]) => run(devicesApi.dontSend(id, ids)),
    dismiss: (id: string) => run(devicesApi.dismiss(id)),
    /** The copies a stopped batch brought, by the usual way (edits are saved to the NAS first); the rest is forgotten. */
    removeCopies: (id: string, ids: number[]) => run(api.job('release', { ids }, id).then(() => devicesApi.dismiss(id)), `Sent to ${label(id)}`),
    cancelJob: (job: number) => run(devicesApi.cancelJob(job), 'Taken back'),
    /** Photos it could not copy: send them again, or take them off the page. */
    retry: (id: string, ids: number[]) => run(devicesApi.retry(id, ids), `Sending ${ids.length === 1 ? 'it' : ids.length + ' photos'} to ${label(id)} again`),
    clear: (id: string, ids: number[]) => run(devicesApi.clear(id, ids), `Cleared from the queue`),
    /** Off the device's list: keep = the copy stays on it, untracked; discard = deleted without saving its edits. */
    forget: (id: string, ids: number[], mode: 'keep' | 'discard') => run(api.job('forget', { ids, mode }, id), mode === 'keep' ? 'Forgotten; the copy stays' : 'Discarded'),
  }
}
export type Actions = ReturnType<typeof useDeviceActions>

/** "Sending 38 of 120 photos to Laptop · 1.4 GB of 4.6 GB · about 6 min left", the bar, and Stop. */
export function TransferBox({ a, name, now, act, compact, phone }: { a: Agent; name: string; now: number; act: Actions; compact?: boolean; phone?: boolean }) {
  const t = a.transfer!
  const l = transferLines(t, name, a, now)
  const queued = t.state === 'queued'
  const button = t.state === 'stopping'
    ? <button className={'btn stop' + (phone ? ' big' : '')} disabled><StopIcon />Stopping…</button>
    : t.can_stop
      ? <button className={'btn stop' + (phone ? ' big' : '')} onClick={() => act.stop(a.id)}
          title={queued ? `Take them back: nothing is sent to ${name}. You can still send them later.` : `Stop sending. The ${t.photos_done} photos already on ${name} stay there.`}>
          {queued ? <CrossIcon /> : <StopIcon />}{queued ? 'Don’t send any' : 'Stop'}</button>
      : null
  const title = <span className="xfer-title">{l.title}</span>
  const detail = (l.detail || l.eta) && <span className="xfer-detail">{l.detail}{l.detail && l.eta ? ' · ' : ''}{l.eta && <b>{l.eta}</b>}</span>
  const bar = !queued && <Bar fraction={l.fraction} label={`Sending to ${name}`} still={!l.moving} />
  const note = queued ? null : !t.can_stop && t.state === 'running' ? `${name} cannot stop a copy that is under way; it finishes this batch.`
    : compact ? null : phone ? `Stop keeps the ${t.photos_done} photos that already arrived.`
      : `Stop keeps the ${t.photos_done} photos that already arrived. The time left is worked out from the photos sent so far; for the first few it says “estimating…”.`
  if (phone) return (
    <div role="group" aria-label={`Sending photos to ${name}`} className="col" style={{ gap: 8 }}>
      {title}{bar}{detail}{button}
      {note && <span className="small muted">{note}</span>}
    </div>
  )
  return (
    <div role="group" aria-label={`Sending photos to ${name}`} className={'xferbox' + (compact ? ' compact' : '')}>
      <div className="row" style={{ gap: compact ? 12 : 16 }}>
        <div className="col grow" style={{ gap: 3 }}>{title}{detail}</div>
        {button}
      </div>
      {bar}
      {note && <span className="tiny muted">{note}</span>}
    </div>
  )
}

/** "Stopped · 38 of 120 on Laptop" with Resume and the red "Remove the 38 copies". */
export function StoppedBox({ a, name, act, compact, phone }: { a: Agent; name: string; act: Actions; compact?: boolean; phone?: boolean }) {
  const st = a.stopped!
  const l = stoppedLines(st, name)
  const here = new Set((a.state?.working_set || []).map(w => w.id))
  const copies = st.arrived.filter(id => here.has(id))
  const big = phone ? ' big' : ''
  const resume = st.unsent.length > 0 && (
    <button className={'btn primary icon' + big} title={`Send the other ${st.unsent.length} photo${st.unsent.length === 1 ? '' : 's'} to ${name}`} onClick={() => act.resume(a.id)}><PlayIcon />Resume</button>
  )
  const remove = copies.length > 0 && !compact && (
    <button className={'btn danger' + big} onClick={() => act.removeCopies(a.id, copies)}
      title={`Delete the ${copies.length} working cop${copies.length === 1 ? 'y' : 'ies'} this batch put on ${name}. Edits that are not on the NAS yet are saved first. The originals on the NAS are never touched.`}>
      Remove the {copies.length} cop{copies.length === 1 ? 'y' : 'ies'}</button>
  )
  const keep = `Close this note. ${st.photos_done ? `The ${st.photos_done} photo${st.photos_done === 1 ? '' : 's'} already on ${name} stay there` : 'Nothing is sent'}; the rest is not sent.`
  const close = phone
    ? <button className="btn big" title={keep} onClick={() => act.dismiss(a.id)}>Leave it at that</button>
    : <button className="btn ghost icon" aria-label={keep} title={keep} onClick={() => act.dismiss(a.id)}><CrossIcon /></button>
  const title = <span className="xfer-title">{l.title}</span>
  const detail = <span className="xfer-detail" style={compact || phone ? undefined : { fontSize: 12 }}>{l.detail}</span>
  const bar = <Bar fraction={l.fraction} label={`Sent to ${name} before stopping`} still />
  if (phone) return (
    <div role="group" aria-label={`Sending to ${name} was stopped`} className="col" style={{ gap: 8 }}>
      {title}{bar}{detail}{resume}{remove}{close}
    </div>
  )
  return (
    <div role="group" aria-label={`Sending to ${name} was stopped`} className={'xferbox' + (compact ? ' compact' : '')}>
      <div className="row" style={{ gap: 12 }}>
        <div className="col grow" style={{ gap: 3 }}>{title}{detail}</div>
        {resume}{remove}{close}
      </div>
      {bar}
    </div>
  )
}

const LIMIT = 3  // rows of a long group shown before "N more · show them"

/** One group of rows, cut to a few with "79 more waiting · show them". */
function useCut<T>(rows: T[], limit: number, more: (n: number) => string, key: string, cols: number) {
  const [all, setAll] = useState(false)
  if (limit && rows.length <= limit + 2) limit = rows.length  // not worth folding one or two rows away
  const shown = all ? rows : rows.slice(0, limit)
  const rest = rows.length - limit
  const toggle = rest > 0 && (
    <tr key={key}><td colSpan={cols} style={{ padding: '6px 8px' }}>
      <button className="devmore" aria-expanded={all} onClick={() => setAll(!all)}><Chevron open={all} />{all ? 'show fewer' : `${more(rest)} · show them`}</button>
    </td></tr>
  )
  return { shown, toggle }
}

function Thumb({ id, v, name, onClick }: { id: number; v?: number; name: string; onClick: () => void }) {
  return <button type="button" className="devthumbbtn" aria-label={`Full view of ${name}`} title="Full view (Space)" onClick={onClick}>
    <img className="devthumb" src={`/api/preview/${id}?s=t&v=${v || 0}`} alt=""
      onError={e => ((e.target as HTMLImageElement).style.visibility = 'hidden')} /></button>
}

/** A checkbox; state 'some' shows the dash. */
function Check({ state, label, onClick }: { state: 'none' | 'some' | 'all'; label: string; onClick: (e: React.MouseEvent) => void }) {
  return <input type="checkbox" className="devcheck" aria-label={label} checked={state === 'all'} onChange={() => {}} onClick={onClick}
    ref={el => { if (el) el.indeterminate = state === 'some' }} />
}

/** The bar under a device's table while photos are selected: only what applies to at least one of them, each with its number. */
function SelectionBar({ a, name, act, sel, rows, clear, phone, onView }: { a: Agent; name: string; act: Actions; sel: number[]; rows: SelRow[]; clear: () => void; phone?: boolean; onView: () => void }) {
  const s = useStore()
  const b = bulkActions(rows, sel)
  const big = phone ? ' big' : ''
  const n = (k: number) => `${k} photo${k === 1 ? '' : 's'}`
  const stuck = b.stuck > 0 ? `${n(b.stuck)} cannot be taken out now: ${name} has already taken ${b.stuck === 1 ? 'it' : 'them'} and cannot give up a copy that is under way.` : ''
  return (
    <div className={'selbar' + (phone ? ' m' : '')} role="toolbar" aria-label={`Selected photos on ${name}`}>
      <b className="selcount" aria-live="polite">{sel.length} selected</b>
      <button className={'btn' + big} title="Look at them full size, one after the other (Space)" onClick={onView}>Full view</button>
      {b.retry.length > 0 && <button className={'btn primary' + big} title={`Send ${n(b.retry.length)} that could not be copied to ${name} again`} onClick={() => { act.retry(a.id, b.retry); clear() }}>Retry {b.retry.length}</button>}
      {b.clear.length > 0 && <button className={'btn' + big} title={`Take ${n(b.clear.length)} that could not be copied off this page. Nothing of ${b.clear.length === 1 ? 'it' : 'them'} is on ${name}; the originals on the NAS are not touched.`} onClick={() => { act.clear(a.id, b.clear); clear() }}>Clear {b.clear.length} from queue</button>}
      {b.drop.length > 0 && <button className={'btn icon' + big} title={stuck || `Take ${n(b.drop.length)} out of what is sent to ${name}`}
        onClick={() => { act.dontSend(a.id, b.drop); clear() }}><CrossIcon />Don’t send {b.drop.length}</button>}
      {b.drop.length === 0 && b.stuck > 0 && <span className="tiny muted" title={stuck}>{b.stuck} cannot be taken out now</span>}
      {b.open.length > 0 && <button className={'btn' + big} title={`Open the copies on ${name} in darktable`} onClick={() => act.job('open', a.id, b.open)}>Open in darktable ({b.open.length})</button>}
      {b.save.length > 0 && <button className={'btn' + big} title={`Copy the edits (XMP) of ${n(b.save.length)} from ${name} back to the NAS now. The photos stay on ${name}.`} onClick={() => act.job('sync', a.id, b.save)}>Save edits to NAS ({b.save.length})</button>}
      {b.show.length > 0 && <button className={'btn' + big} title="Find them in LightPhotoLibrary: only these photos" onClick={() => s.showPhotos(b.show)}>Show in library ({b.show.length})</button>}
      {b.done.length > 0 && <button className={'btn danger' + big} title={`Done with ${b.done.length === 1 ? 'it' : 'them'}: save the edits to the NAS, close in darktable and delete the working cop${b.done.length === 1 ? 'y' : 'ies'} on ${name}. The originals on the NAS are never touched.`}
        onClick={() => (b.done.length < 2 || window.confirm(`Save the edits of ${b.done.length} photos to the NAS and delete their working copies on ${name}? The originals on the NAS are never touched.`)) && act.job('release', a.id, b.done)}>Done · remove {b.done.length} from {name}</button>}
      <button className={'btn ghost' + big} title="Unselect everything (Esc)" onClick={clear}>Clear</button>
    </div>
  )
}

/** What a row is, for the selection, the full view and the menu. */
export type RowInfo = { id: number; filename: string; kind: RowKind; w?: WorkItem; f?: FailedItem; droppable?: boolean }

/** Every row of a device, in the order shown (for the selection, Shift-click and the full view). */
export function rowInfos(a: Agent, filter: RowFilter): RowInfo[] {
  const rows = filterRows(deviceRows(a), filter)
  const skip = !!a.transfer?.can_skip
  return [
    ...rows.failed.map(f => ({ id: f.id, filename: f.filename || `photo ${f.id}`, kind: 'failed' as const, f })),
    ...rows.copying.map(w => ({ id: w.id, filename: w.filename, kind: 'copying' as const, w, droppable: skip && a.transfer?.current?.id === w.id })),
    ...rows.waiting.map(w => ({ id: w.id, filename: w.filename || `photo ${w.id}`, kind: 'waiting' as const, droppable: skip || !a.transfer })),
    ...rows.arrived.map(w => ({ id: w.id, filename: w.filename, kind: 'arrived' as const, w })),
    ...rows.unsent.map(w => ({ id: w.id, filename: w.filename || `photo ${w.id}`, kind: 'unsent' as const, droppable: true })),
  ]
}

/** The photos of a device: what could not be copied first, then copying, waiting, what is on it, what a stop left unsent. */
function PhotoTable({ a, name, now, act, phone, sel, setSel, filter, onView }: { a: Agent; name: string; now: number; act: Actions; phone?: boolean; sel: Selection | null; setSel: (ids: number[], anchor: number | null) => void; filter: RowFilter; onView: (id: number) => void }) {
  const s = useStore()
  const rows = filterRows(deviceRows(a), filter)
  const busy = !!a.transfer || !!a.stopped
  const skip = !!a.transfer?.can_skip
  const failed = useCut(rows.failed, filter === 'failed' ? 0 : LIMIT, n => `${n} more that could not be sent`, 'more-f', 6)
  const waiting = useCut(rows.waiting, LIMIT, n => `${n} more waiting`, 'more-w', 6)
  const arrived = useCut(rows.arrived, filter !== 'all' ? 0 : busy ? LIMIT : 8, n => `${n} more on ${name}`, 'more-a', 6)
  const unsent = useCut(rows.unsent, 0, n => `${n} not sent`, 'more-u', 6)
  const big = phone ? ' big' : ''
  const infos = rowInfos(a, filter)
  const order = infos.map(r => r.id)
  const selIds = sel?.ids ?? []
  const selRows: SelRow[] = infos.map(r => ({ id: r.id, kind: r.kind, droppable: r.droppable, unsaved: r.w ? hasUnsavedEdits(r.w) : undefined }))
  const pick = (id: number, shift: boolean) => setSel(shift ? rangeIds(order, selIds, sel?.anchor ?? null, id) : toggleId(selIds, id), id)
  const all = boxState(order, selIds)
  const headBox = <Check state={all} label={`Select all ${order.length} photos on ${name}`} onClick={() => setSel(all === 'all' ? [] : order, all === 'all' ? null : order[0])} />
  const heading = (text: string, group: { id: number }[]) => {
    const ids = group.map(g => g.id), full = boxState(ids, selIds) === 'all'
    return <tr key={'h-' + text} className="devgroup"><td colSpan={6}><div className="row" style={{ gap: 10 }}>
      <span className="small soft">{text} · {ids.length}</span>
      <button className="linkbtn tiny soft" style={{ textDecoration: 'underline' }} title={full ? 'Unselect this group' : `Select all ${ids.length}, also the ones that are not shown`}
        onClick={() => setSel(toggleGroup(selIds, ids), full ? null : ids[0])}>{full ? 'Unselect' : 'Select all'}</button>
    </div></td></tr>
  }
  const dont = (id: number, filename: string, title: string) => (
    <button className={'btn icon' + big} aria-label={`Don’t send ${filename}`} title={title} onClick={() => act.dontSend(a.id, [id])}><CrossIcon />Don’t send</button>
  )
  const cells = (id: number, filename: string, v: number | undefined, status: React.ReactNode, when: string, actions: React.ReactNode, error?: string, bad?: boolean) => {
    const chosen = selIds.includes(id)
    const box = <Check state={chosen ? 'all' : 'none'} label={`Select ${filename}`} onClick={e => pick(id, e.shiftKey)} />
    const cls = [chosen ? 'sel' : '', bad ? 'bad' : ''].filter(Boolean).join(' ') || undefined
    return phone ? (
    <tr key={id} className={cls}><td colSpan={6}>
      <div className="row" style={{ gap: 10 }}>
        <label className="devcheckcell">{box}</label>
        <Thumb id={id} v={v} name={filename} onClick={() => onView(id)} />
        <div className="col grow" style={{ gap: 4, alignItems: 'flex-start' }}>
          <span className="ellipsis" style={{ maxWidth: '100%' }}>{filename}</span>
          {status}
          {error && <span className="tiny" style={{ color: '#ff9a9d' }}>{error}</span>}
        </div>
      </div>
      <div className="devacts">{actions}</div>
    </td></tr>
  ) : (
    <tr key={id} className={cls}>
      <td className="devcheckcell">{box}</td>
      <td style={{ width: 64 }}><Thumb id={id} v={v} name={filename} onClick={() => onView(id)} /></td>
      <td><button className="linkbtn" title="Show in the library" onClick={() => s.reveal(id)}>{filename}</button>{error && <div className="tiny" style={{ color: '#ff9a9d' }}>{error}</div>}</td>
      <td><div className="col" style={{ gap: 6, alignItems: 'flex-start' }}>{status}</div></td>
      <td className="muted nowrap">{when}</td>
      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}><div className="row" style={{ display: 'inline-flex', gap: 6 }}>{actions}</div></td>
    </tr>
    )
  }
  const pill = (text: string, bg = '#2a2a2e', fg = '#c8c8cc') => <span className="status nowrap" style={{ background: bg, color: fg }}>{text}</span>
  const item = (w: WorkItem) => {
    const st = STATUS[w.status] || [w.status, '#2a2a2e', '#c8c8cc']
    const pr = itemProgress(a, w, now)
    const copying = w.status === 'downloading'
    const unsaved = hasUnsavedEdits(w)
    const primary = copying ? (skip && a.transfer?.current?.id === w.id ? dont(w.id, w.filename, 'Give up this photo; the copy carries on with the next one') : null)
      : unsaved ? <button className={'btn primary' + big} title={`Copy the edits (XMP) from ${name} back to the NAS now. The photo stays on ${name} so you can keep editing.`} onClick={() => act.job('sync', a.id, [w.id])}>Save to NAS</button>
        : <button className={'btn' + big} title={`Open the copy on ${name} in darktable's darkroom`} onClick={() => act.job('open', a.id, [w.id])}>Open in darktable</button>
    return cells(w.id, w.filename, w.synced_ts,
      <>{pill(w.status === 'ready' ? `on ${name} · ready to edit` : st[0], st[1], st[2])}{pr && <ProgressBar label={pr.step} fraction={pr.fraction} />}</>,
      w.checkout_ts ? ago(w.checkout_ts, now) : '',
      <>{primary}{!copying && <RowMenu label={`More for ${w.filename}`} big={!!phone} items={arrivedMenu(a, name, act, s, w, () => onView(w.id))} />}</>,
      w.status === 'error' ? w.error : undefined)
  }
  const bad = (f: FailedItem) => cells(f.id, f.filename || `photo ${f.id}`, 0, pill('couldn’t send', '#3a1f22', '#ff9a9d'), f.time ? ago(f.time, now) : '',
    <>
      <button className={'btn' + big} title={`Send it to ${name} again`} onClick={() => act.retry(a.id, [f.id])}>Retry</button>
      <button className={'btn' + big} title={`Take it off this page. Nothing of it is on ${name}; the original on the NAS is not touched.`} onClick={() => act.clear(a.id, [f.id])}>Clear</button>
      <RowMenu label={`More for ${f.filename}`} big={!!phone} items={[
        { label: 'Full view', hint: 'Space', run: () => onView(f.id) },
        { label: 'Show in library', run: () => s.reveal(f.id) },
      ]} />
    </>, f.error || 'The copy from the NAS did not complete.', true)
  const queued = (w: TransferItem, words: string, title: string) => cells(w.id, w.filename || `photo ${w.id}`, 0, pill(words), '',
    <>{(skip || !a.transfer || words === 'not sent') && dont(w.id, w.filename || `photo ${w.id}`, title)}
      <RowMenu label={`More for ${w.filename}`} big={!!phone} items={[{ label: 'Full view', hint: 'Space', run: () => onView(w.id) }, { label: 'Show in library', run: () => s.reveal(w.id) }]} /></>)
  if (!order.length)
    return <div className="small muted">{filter !== 'all' ? 'Nothing here.' : isPhoneAgent(a) ? `No photos on ${name}.` : `Nothing checked out on ${name}.`}</div>
  const keys = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).closest('.rowmenu')) return
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') { e.preventDefault(); setSel(order, order[0] ?? null) }
    else if (e.key === 'Escape' && selIds.length) { e.preventDefault(); setSel([], null) }
    else if (e.key === ' ' && selIds.length && (e.target as HTMLElement).tagName !== 'BUTTON') { e.preventDefault(); onView(selIds[0]) }
  }
  return (
    <div className="devsel" onKeyDown={keys}>
    {phone && <label className="devselall row"><span className="devcheckcell">{headBox}</span><span className="small soft">Select all {order.length}</span></label>}
    <div style={{ overflowX: 'auto' }}>
      <table className="wtable devtable">
        {!phone && <thead><tr><th className="devcheckcell">{headBox}</th><th /><th>Photo</th><th>Status</th><th>Checked out</th><th /></tr></thead>}
        <tbody>
          {rows.failed.length > 0 && heading('Couldn’t send', rows.failed)}
          {failed.shown.map(bad)}
          {failed.toggle}
          {rows.copying.map(item)}
          {rows.waiting.length > 0 && heading('Waiting', rows.waiting)}
          {waiting.shown.map(w => queued(w, 'waiting', 'Take this photo out of the queue'))}
          {waiting.toggle}
          {rows.arrived.length > 0 && heading(`On ${name}`, rows.arrived)}
          {arrived.shown.map(item)}
          {arrived.toggle}
          {rows.unsent.length > 0 && heading('Not sent', rows.unsent)}
          {unsent.toggle}
          {unsent.shown.map(w => queued(w, 'not sent', 'Leave this photo out when you resume'))}
        </tbody>
      </table>
    </div>
    {selIds.length > 0 && <SelectionBar a={a} name={name} act={act} sel={selIds} rows={selRows} clear={() => setSel([], null)} phone={phone} onView={() => onView(selIds[0])} />}
    </div>
  )
}

/** The ⋯ menu of a photo that is on the device. */
export function arrivedMenu(a: Agent, name: string, act: Actions, s: ReturnType<typeof useStore>, w: WorkItem, view?: () => void): MenuItem[] {
  const unsaved = hasUnsavedEdits(w)
  return [
    ...(view ? [{ label: 'Full view', hint: 'Space', run: view }] : []),
    { label: `Open in darktable on ${name}`, run: () => act.job('open', a.id, [w.id]) },
    ...(unsaved ? [{ label: 'Save edits to NAS', run: () => act.job('sync', a.id, [w.id]) }] : []),
    { label: 'Show in library', run: () => s.reveal(w.id) },
    { sep: true as const },
    { label: `Forget, keep the copy on ${name}`, title: `LPL stops tracking it; the files stay on ${name}.`, run: () => act.forget(a.id, [w.id], 'keep') },
    { label: unsaved ? `Save edits, then remove from ${name}` : `Remove from ${name}`, danger: true,
      title: `Edits are saved to the NAS first, then the working copy on ${name} is deleted. The original on the NAS is never touched.`,
      run: () => act.job('release', a.id, [w.id]) },
    ...(unsaved ? [{ label: 'Discard edits and remove…', danger: true,
      run: () => window.confirm(`Delete ${w.filename} on ${name} WITHOUT saving its edits to the NAS? The NAS keeps the version it has.`) && act.forget(a.id, [w.id], 'discard') }] : []),
  ]
}

const CHIPS: [RowFilter, string][] = [['all', 'All'], ['failed', 'Couldn’t send'], ['sending', 'Sending'], ['ready', 'Ready to edit'], ['unsaved', 'Edited, not saved'], ['saved', 'Saved to NAS'], ['unsent', 'Not sent']]

/** All · Couldn't send · Sending · Ready to edit · Edited, not saved · Saved to NAS · Not sent, each with its number (empty ones hidden). */
function FilterChips({ a, filter, setFilter, phone }: { a: Agent; filter: RowFilter; setFilter: (f: RowFilter) => void; phone?: boolean }) {
  const n = filterCounts(deviceRows(a))
  const shown = CHIPS.filter(([k]) => k === 'all' || n[k] > 0 || k === filter)
  if (shown.length <= 2 && filter === 'all') return null  // one kind of row only: nothing to filter
  return (
    <div className={'devchips' + (phone ? ' m' : '')} role="group" aria-label="Show">
      {shown.map(([k, l]) => <button key={k} className={'devchip ' + k + (filter === k ? ' on' : '')} aria-pressed={filter === k} onClick={() => setFilter(k)}>{l} <b>{n[k]}</b></button>)}
    </div>
  )
}

/** "2 photos couldn't be sent to Laptop" with Look at them, Retry and Clear from queue. */
function FailedBanner({ a, name, act, look, phone, compact }: { a: Agent; name: string; act: Actions; look: () => void; phone?: boolean; compact?: boolean }) {
  const f = deviceRows(a).failed
  if (!f.length) return null
  const ids = f.map(x => x.id), one = f.length === 1, big = phone ? ' big' : ''
  const why = f.find(x => x.error)?.error
  return (
    <div role="alert" className={'devfail' + (phone ? ' m' : '') + (compact ? ' compact' : '')}>
      <div className="col grow" style={{ gap: 2, minWidth: 0 }}>
        <b>{one ? `1 photo couldn’t be sent to ${name}` : `${f.length} photos couldn’t be sent to ${name}`}</b>
        {!compact && <span className="small">{why ? `${why}. ` : ''}Nothing of {one ? 'it' : 'them'} is on {name}{a.transfer ? '; the rest of the batch carries on' : ''}.</span>}
      </div>
      <div className="row" style={{ gap: 8, flexWrap: phone ? 'wrap' : 'nowrap' }}>
        <button className={'btn' + big} onClick={look}>Look at {one ? 'it' : 'them'}</button>
        <button className={'btn primary' + big} title={`Send ${one ? 'it' : 'them'} to ${name} again`} onClick={() => act.retry(a.id, ids)}>Retry{one ? '' : ` ${f.length}`}</button>
        <button className={'btn' + big} title={`Take ${one ? 'it' : 'them'} off this page. The originals on the NAS are not touched.`} onClick={() => act.clear(a.id, ids)}>Clear from queue</button>
      </div>
    </div>
  )
}

function DeviceCard({ a, folded, onFold, now, act, phone, sel, setSel, onView }: { a: Agent; folded: boolean; onFold: (f: boolean) => void; now: number; act: Actions; phone?: boolean; sel: Selection | null; setSel: (ids: number[], anchor: number | null) => void; onView: (id: number, order: number[]) => void }) {
  const s = useStore()
  const name = agentLabel(a, s.agents)
  const [filter, setFilterRaw] = useState<RowFilter>('all')
  const counts = filterCounts(deviceRows(a))
  const shownFilter: RowFilter = filter !== 'all' && counts[filter] === 0 ? 'all' : filter  // its last photo left: back to All
  const setFilter = (f: RowFilter) => { setFilterRaw(f); setSel([], null) }
  const view = (id: number) => onView(id, rowInfos(a, shownFilter).map(r => r.id))
  const look = () => { setFilter('failed'); if (folded) onFold(false) }
  const table = <>
    <FilterChips a={a} filter={shownFilter} setFilter={setFilter} phone={phone} />
    <PhotoTable a={a} name={name} now={now} act={act} phone={phone} sel={sel} setSel={setSel} filter={shownFilter} onView={view} />
  </>
  const ws = a.state?.working_set || []
  const unsaved = ws.filter(hasUnsavedEdits).map(w => w.id)
  const synced = ws.filter(w => w.status === 'synced').map(w => w.id)
  const pills = devicePills(a, ago(a.last_seen, now))
  const t = a.transfer, st = t ? null : a.stopped
  const big = phone ? ' big' : ''
  const save = unsaved.length > 0 && (
    <button className={'btn primary' + big} title="Copy the edits of every changed photo back to the NAS" onClick={() => act.job('sync', a.id, unsaved)}>Save {unsaved.length} edited to NAS</button>
  )
  const remove = synced.length > 0 && (
    <button className={'btn danger' + big} title={`Delete the working copies on ${name} whose edits are already on the NAS`} onClick={() => act.job('release', a.id, synced)}>Remove saved copies from {name}</button>
  )
  const fold = (
    <button className="devfold" aria-expanded={!folded} aria-label={`${folded ? 'Unfold' : 'Fold'} ${name}`} onClick={() => onFold(!folded)}>
      <Chevron open={!folded} /><span className="display">{name}</span>
      {phone && pills.slice(0, 1).map(([ok, text]) => <span key={text} className={ok ? 'pill-ok' : 'pill-off'}><i />{text}</span>)}
    </button>
  )
  const lines = !folded && (
    <>
      {a.state?.message && <div className="small soft">{a.state.message}</div>}
      {isPhoneAgent(a) && !a.online && <div className="small muted">The darktable app reports only while it is open on {name}: what you start here waits until it is opened there.</div>}
    </>
  )
  if (phone) return (
    <section className={'devcard m' + (folded ? '' : ' open')} aria-label={name}>
      {fold}
      {!folded && pills.length > 1 && <div className="row wrap" style={{ gap: 6 }}>{pills.slice(1).map(([ok, text]) => <span key={text} className={ok ? 'pill-ok' : 'pill-off'}><i />{text}</span>)}</div>}
      {lines}
      {t ? <TransferBox a={a} name={name} now={now} act={act} phone /> : <span className="soft" style={{ fontSize: 13 }}>{deviceCounts(a)}</span>}
      {st && <StoppedBox a={a} name={name} act={act} phone />}
      <FailedBanner a={a} name={name} act={act} look={look} phone compact={folded} />
      {save}{remove}
      {!folded && table}
    </section>
  )
  return (
    <section className={'devcard' + (folded ? '' : ' open')} aria-label={name}>
      <div className="row wrap devhead">
        {fold}
        <span className="small muted">{a.os}</span>
        {(folded ? pills.slice(0, 1) : pills).map(([ok, text]) => <span key={text} className={ok ? 'pill-ok' : 'pill-off'}><i />{text}</span>)}
        <span className="small soft nowrap">{deviceCounts(a)}</span>
        <div className="row devbtns">
        {folded && t && t.can_stop && t.state !== 'stopping' && (
          <button className="btn stop small" onClick={() => act.stop(a.id)}
            title={t.state === 'queued' ? `Take them back: nothing is sent to ${name}.` : `Stop sending. The ${t.photos_done} photos already on ${name} stay there.`}>
            {t.state === 'queued' ? <CrossIcon /> : <StopIcon />}{t.state === 'queued' ? 'Don’t send any' : 'Stop'}</button>
        )}
        {folded && st && st.unsent.length > 0 && <button className="btn primary icon" title={`Send the other ${st.unsent.length} photos to ${name}`} onClick={() => act.resume(a.id)}><PlayIcon />Resume</button>}
        {save}{remove}
        </div>
      </div>
      {folded && t && t.state !== 'queued' && (() => { const l = transferLines(t, name, a, now); return <Bar thin fraction={l.fraction} label={`Sending to ${name}`} still={!l.moving} /> })()}
      {lines}
      {!folded && t && <TransferBox a={a} name={name} now={now} act={act} />}
      {!folded && st && <StoppedBox a={a} name={name} act={act} />}
      <FailedBanner a={a} name={name} act={act} look={look} compact={folded} />
      {!folded && table}
    </section>
  )
}

type Job = { id: number; agent_id: string | null; kind: string; payload: any; created: number; status: string }

/** The last jobs, folded: "120 photos to Laptop · 3 min ago". A job no device has taken yet can be taken back. */
function RecentHandoffs({ now, act, phone }: { now: number; act: Actions; phone?: boolean }) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const t = () => api.jobs().then(r => setJobs(r.jobs)).catch(() => {})
    t(); const i = window.setInterval(t, 4000); return () => window.clearInterval(i)
  }, [])
  const who = (j: Job) => (j.agent_id ? act.label(j.agent_id) : 'waiting for a PC')
  const n = (j: Job) => (j.payload?.ids ? `${j.payload.ids.length} photo${j.payload.ids.length === 1 ? '' : 's'}` : 'all')
  const last = jobs[0]
  const summary = !last ? 'None yet.' : `${last.kind === 'edit' ? `${n(last)} to ${last.agent_id ? act.label(last.agent_id) : 'a PC'}` : `${last.kind} · ${n(last)} · ${who(last)}`}${['stopped', 'cancelled', 'failed'].includes(last.status) ? ` · ${last.status}` : ''} · ${ago(last.created, now)}`
  return (
    <section className="devcard" style={{ padding: 0 }} aria-label="Recent hand-offs">
      <button className="devblock" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Chevron open={open} /><span className="display nowrap" style={{ fontSize: phone ? 16 : 17 }}>Recent hand-offs</span>
        <span className="grow small muted ellipsis" style={phone ? { textAlign: 'right' } : undefined}>{summary}</span>
      </button>
      {open && (
        <div className="col" style={{ padding: '0 16px 14px', gap: 6 }}>
          {jobs.length ? jobs.slice(0, 12).map(j => (
            <div key={j.id} className="row small">
              <span className="muted" style={{ width: 80, flex: '0 0 auto' }}>{ago(j.created, now)}</span>
              <span style={{ width: 56, flex: '0 0 auto' }}>{j.kind}</span>
              <span className="grow muted ellipsis">{n(j)} · {who(j)}</span>
              <span className={j.status === 'queued' ? 'soft' : 'muted'}>{j.status}</span>
              {j.status === 'queued' && <button className="btn" title="Take this back: no device has started on it yet" onClick={() => act.cancelJob(j.id).then(() => api.jobs().then(r => setJobs(r.jobs)).catch(() => {}))}>Cancel</button>}
            </div>
          )) : <span className="small muted">None yet.</span>}
        </div>
      )}
    </section>
  )
}

/** The page. On the desktop it fills a tab (view id 'work', as before); on the phone it is a page with a back arrow. */
export function DevicesView() {
  const s = useStore()
  const act = useDeviceActions()
  const phone = s.isMobile
  const now = serverNow(s.status)
  const [settings, setSettings] = useState(false)
  // The photos he ticked: of one device at a time; photos that leave the device's list leave the selection.
  const [selection, setSelection] = useState<Selection | null>(null)
  const [full, setFull] = useState<FullView | null>(null)
  useEffect(() => {
    if (!selection) return
    const a = s.agents.find(x => x.id === selection.agent)
    const r = a ? deviceRows(a) : null
    const ids = r ? prune(selection.ids, [...r.failed, ...r.copying, ...r.waiting, ...r.arrived, ...r.unsent].map(x => x.id)) : []
    if (ids !== selection.ids) setSelection(ids.length ? { ...selection, ids } : null)
  }, [s.agents])
  // His fold choices are kept on the server, so every browser agrees; the phone layout keeps its own (under
  // "phone:<id>"), because there every device starts folded: a folded card already shows what matters.
  // mine: his own click, shown at once; forgotten when the server says the same.
  const key = (a: Agent) => (phone ? 'phone:' : '') + a.id
  const chosen = (a: Agent) => (phone ? a.folded_phone : a.folded)
  const [mine, setMine] = useState<Record<string, boolean>>({})
  useEffect(() => {
    const done = Object.keys(mine).filter(k => { const a = s.agents.find(x => key(x) === k); return !a || chosen(a) === mine[k] })
    if (done.length) setMine(m => Object.fromEntries(Object.entries(m).filter(([k]) => !done.includes(k))))
  }, [s.agents])
  const folded = (a: Agent) => mine[key(a)] ?? (phone ? chosen(a) ?? true : isFolded(a))
  const setFolds = (f: Record<string, boolean>) => {
    setMine(m => ({ ...m, ...f }))
    devicesApi.fold(f).then(refreshStatus).catch(e => s.toast(e.message))
  }
  const foldAll = (v: boolean) => setFolds(Object.fromEntries(s.agents.map(a => [key(a), v])))
  // while something moves: tick, so bars and "ago" follow the clock and stop claiming progress when a device goes quiet
  useTick(s.agents.some(a => a.transfer || (a.online && (a.state?.working_set || []).some(w => w.step))))
  const allFolded = s.agents.length > 0 && s.agents.every(folded)
  const total = s.agents.reduce((n, a) => n + (a.state?.working_set || []).filter(w => w.status !== 'downloading').length, 0)
  const settingsLink = <button className="linkbtn small soft" style={{ textDecoration: 'underline', whiteSpace: 'nowrap' }} title="A device's details, the library on the NAS, and what happens when photos have arrived" onClick={() => setSettings(true)}>Device settings</button>
  const none = !s.agents.length && (
    <div className="card soft" style={{ lineHeight: 1.6 }}>
      No PC agent has connected yet. Install it on your Windows (or Linux) PC: see <b>agent/README</b> in the download.
      Once it runs, it appears here and “Edit in darktable” opens photos on that PC.
    </div>
  )
  const cards = s.agents.map(a => <DeviceCard key={a.id} a={a} folded={folded(a)} onFold={f => setFolds({ [key(a)]: f })} now={now} act={act} phone={phone}
    sel={selection?.agent === a.id ? selection : null} setSel={(ids, anchor) => setSelection(ids.length ? { agent: a.id, ids, anchor } : null)}
    onView={(id, order) => setFull({ agent: a.id, order, id })} />)
  const fullAgent = full && s.agents.find(x => x.id === full.agent)
  return (
    <div className="scroll">
      {phone ? (
        <div className="col devpage" style={{ padding: '0 14px', gap: 10 }}>
          <div className="row" style={{ gap: 10 }}>
            <span className="grow soft" style={{ fontSize: 13 }}>{s.agents.length ? `${onlineCount(s.agents)} · ${total} photo${total === 1 ? '' : 's'} on your devices` : 'No devices yet'}</span>
            {s.agents.length > 0 && <button className="btn big" onClick={() => foldAll(!allFolded)}>{allFolded ? 'Unfold all' : 'Fold all'}</button>}
          </div>
          {none}{cards}
          <SharedSection phone now={now} />
          <RecentHandoffs now={now} act={act} phone />
          <div style={{ padding: '4px 2px' }}>{settingsLink}</div>
        </div>
      ) : (
        <div className="col devpage" style={{ gap: 12, maxWidth: 1100 }}>
          <div className="row" style={{ alignItems: 'flex-end', gap: 16 }}>
            <div className="col grow" style={{ gap: 4 }}>
              <span className="display" style={{ fontSize: 24, fontWeight: 500 }}>Devices</span>
              <span className="soft">Photos copied to your devices so darktable can edit them at SSD speed. The NAS is read once and only the XMP goes back.</span>
            </div>
            {settingsLink}
            <div className="seg" role="group" aria-label="Fold or unfold every device" style={{ flexWrap: 'nowrap' }}>
              <button onClick={() => foldAll(true)}>Fold all</button>
              <button onClick={() => foldAll(false)}>Unfold all</button>
            </div>
          </div>
          {none}{cards}
          <SharedSection phone={false} now={now} />
          <RecentHandoffs now={now} act={act} />
        </div>
      )}
      {settings && <SettingsDialog section="devices" onClose={() => setSettings(false)} />}
      {full && fullAgent && <DeviceFullView a={fullAgent} view={full} setView={setFull} act={act} now={now}
        sel={selection?.agent === full.agent ? selection.ids : []}
        setSel={ids => setSelection(ids.length ? { agent: full.agent, ids, anchor: ids[ids.length - 1] } : null)} />}
    </div>
  )
}

/** Is a device's transfer something the page should poll faster for? (used by the indicator) */
export const anythingMoving = (agents: Agent[], now: number) => agents.some(a => a.transfer && (a.transfer.state !== 'queued' || reporting(a, now)))
