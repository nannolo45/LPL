// SPDX-License-Identifier: GPL-3.0-or-later
// R11 on the Devices page: darktable styles, presets and module layouts shared through LPL. Per device a line
// "n styles, presets, layouts waiting"; opened (PCs): the waiting list with checkboxes, conflicts (keep / take / keep
// both), deletions (delete here / keep), Later and "Update <device> · n". Beside it (below on the phone) "Shared through
// LPL": every item, who changed it last, a dot per device. The phone updates itself from its own review sheet.
import { useCallback, useEffect, useState } from 'react'
import { useStore } from '../store'
import { fmtWhen } from '../edits'
import { Chevron } from './DevicesBits'
import {
  conflictChoices, DeleteChoice, DOTS, kindText, lastChange, overviewItems, SharedDevice, SharedOverview,
  updateLabel, updateNote, updatePayload, waitingLine, whatText,
} from '../shared'
import { sharedApi } from '../sharedApi'

function DeviceShared({ d, phone, reload, now }: { d: SharedDevice; phone: boolean; reload: () => void; now: number }) {
  const s = useStore()
  const [open, setOpen] = useState(false)
  const [ticked, setTicked] = useState<Set<number>>(new Set())
  const [dels, setDels] = useState<Record<number, DeleteChoice>>({})
  const ids = d.waiting.map(w => w.id).join(',')
  useEffect(() => { setTicked(new Set(d.waiting.map(w => w.id))) }, [ids])  // everything ticked when the list changes
  const n = d.waiting.length + d.conflicts.length
  const pay = updatePayload(d.id, d.waiting, ticked, dels)
  const count = pay.ids.length + pay.delete.length + pay.keep.length
  const big = phone ? ' big' : ''
  const toggle = (id: number) => setTicked(t => { const x = new Set(t); x.has(id) ? x.delete(id) : x.add(id); return x })
  const resolve = (id: number, choice: 'mine' | 'theirs' | 'both') =>
    sharedApi.resolve(id, d.id, choice).then(() => { s.toast('Done'); reload() }).catch(e => s.toast(e.message))
  const update = () => sharedApi.update(pay).then(() => { s.toast(`Update sent to ${d.name}`); reload() }).catch(e => s.toast(e.message))
  const delSeg = (id: number) => (
    <span className="seg" role="group" aria-label="Deleted elsewhere">
      {([['delete', 'delete here'], ['keep', 'keep']] as [DeleteChoice, string][]).map(([v, l]) =>
        <button key={v} className={(dels[id] || 'delete') === v ? 'on' : ''} aria-pressed={(dels[id] || 'delete') === v} onClick={() => setDels(x => ({ ...x, [id]: v }))}>{l}</button>)}
    </span>
  )
  const conflictRow = (c: SharedDevice['conflicts'][number]) => (
    <div key={'c' + c.id} className="col sharedconf" style={{ gap: 6 }}>
      <span className="small"><b>{c.name}</b> <span className="muted">{kindText(c)}</span> · <span className="sharedwarn">changed here too</span>
        <span className="muted"> · {c.candidate.deleted ? `deleted on ${d.name}` : `${d.name} ${c.candidate.changed_at ? fmtWhen(c.candidate.changed_at, now) : ''}`}, {c.deleted ? 'deleted' : 'changed'} on {c.device_name || c.device_id} {c.changed_at ? fmtWhen(c.changed_at, now) : ''}</span></span>
      <div className="row wrap" style={{ gap: 6 }}>
        {conflictChoices(d.name, c).map(o => <button key={o.choice} className={'btn' + big} disabled={!o.ok} title={o.hint} onClick={() => resolve(c.id, o.choice)}>{o.label}</button>)}
      </div>
    </div>
  )
  const list = phone ? (
    <div className="col" style={{ gap: 8 }}>
      {d.waiting.map(w => (
        <label key={w.id} className="row sharedrow" style={{ gap: 10, alignItems: 'flex-start' }}>
          <input type="checkbox" className="devcheck" checked={ticked.has(w.id)} onChange={() => toggle(w.id)} aria-label={`Take in ${w.name}`} />
          <span className="col grow" style={{ gap: 3, minWidth: 0 }}>
            <span className="ellipsis"><b>{w.name}</b></span>
            <span className="small muted">{kindText(w)} · {whatText(w)} · {lastChange(w, t => fmtWhen(t, now))}</span>
            {w.what === 'deleted' && delSeg(w.id)}
          </span>
        </label>
      ))}
      {d.conflicts.map(conflictRow)}
    </div>
  ) : (
    <div className="col" style={{ gap: 10 }}>
      {d.waiting.length > 0 && (
        <table className="devtable sharedtable" style={{ width: '100%' }}>
          <thead><tr><th className="devcheckcell" /><th>Item</th><th>Kind</th><th>What</th><th>From</th><th>When</th></tr></thead>
          <tbody>
            {d.waiting.map(w => (
              <tr key={w.id}>
                <td className="devcheckcell"><input type="checkbox" className="devcheck" checked={ticked.has(w.id)} onChange={() => toggle(w.id)} aria-label={`Take in ${w.name}`} /></td>
                <td>{w.name}</td><td className="muted">{kindText(w)}</td>
                <td>{w.what === 'deleted' ? <span className="row" style={{ gap: 8 }}><span>{whatText(w)}</span>{delSeg(w.id)}</span> : whatText(w)}</td>
                <td className="muted">{w.device_name || w.device_id}</td><td className="muted">{w.changed_at ? fmtWhen(w.changed_at, now) : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {d.conflicts.map(conflictRow)}
    </div>
  )
  return (
    <div className={'devcard sharedcard' + (open ? ' open' : '') + (phone ? ' m' : '')}>
      <button className="devfold" aria-expanded={open} onClick={() => setOpen(!open)} disabled={d.phone || n === 0}>
        {!d.phone && n > 0 && <Chevron open={open} />}
        <span className="display">{d.name}</span>
        <span className={'small ' + (n ? 'soft' : 'muted')}>{waitingLine(d)}</span>
      </button>
      {open && !d.phone && n > 0 && <>
        {list}
        <div className="small muted">{updateNote(d)}</div>
        {d.waiting.length > 0 && (
          <div className={'row' + (phone ? ' wrap' : '')} style={{ gap: 8, justifyContent: 'flex-end' }}>
            <button className={'btn' + big} onClick={() => setOpen(false)}>Later</button>
            <button className={'btn primary' + big} disabled={!count} onClick={update}>{updateLabel(d.name, count)}</button>
          </div>
        )}
      </>}
      {!open && d.job && <div className="small muted">{updateNote(d)}</div>}
    </div>
  )
}

function Overview({ o, phone, now }: { o: SharedOverview; phone: boolean; now: number }) {
  const items = overviewItems(o)
  const initials = o.devices.map(d => d.name.slice(0, 1).toUpperCase())
  const dots = (i: (typeof items)[number]) => o.devices.map(d => {
    const v = i.devices[d.id] || 'absent'
    return <span key={d.id} className={'shareddot ' + v} title={`${d.name}: ${DOTS[v][1]}`} aria-label={`${d.name}: ${DOTS[v][1]}`}>{DOTS[v][0]}</span>
  })
  return (
    <div className="card sharedpanel">
      <span className="display" style={{ fontSize: 17 }}>Shared through LPL</span>
      <span className="small muted">The newest copy of each item, who changed it last, and which devices have it.</span>
      {!items.length ? <span className="small muted">Nothing shared yet. Styles, presets and module layouts you make in darktable appear here.</span>
        : phone ? (
          <div className="col" style={{ gap: 8 }}>
            {items.map(i => (
              <div key={i.id} className="col sharedrow" style={{ gap: 2 }}>
                <span className="row" style={{ gap: 8 }}><b className="grow ellipsis">{i.name}</b><span className="shareddots">{dots(i)}</span></span>
                <span className="small muted">{i.deleted ? `deleted on ${i.device_name || i.device_id}` : kindText(i)} · {lastChange(i, t => fmtWhen(t, now))}</span>
              </div>
            ))}
          </div>
        ) : (
          <table className="devtable sharedtable" style={{ width: '100%' }}>
            <thead><tr><th>Item</th><th>Last change</th><th title={o.devices.map(d => d.name).join(' · ')}>{initials.join(' · ')}</th></tr></thead>
            <tbody>
              {items.map(i => (
                <tr key={i.id}>
                  <td><span className="col" style={{ gap: 1 }}><span>{i.name}</span><span className="tiny muted">{i.deleted ? `deleted on ${i.device_name || i.device_id}` : kindText(i)}</span></span></td>
                  <td className="muted">{lastChange(i, t => fmtWhen(t, now))}</td>
                  <td className="shareddots nowrap">{dots(i)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      <span className="tiny muted">● has the newest · ○ waiting · ! changed on two devices · – not there (or needs a newer darktable)</span>
    </div>
  )
}

/** The section on the Devices page; nothing when no device shares styles and presets. */
export function SharedSection({ phone, now }: { phone: boolean; now: number }) {
  const [o, setO] = useState<SharedOverview | null>(null)
  const reload = useCallback(() => { sharedApi.overview().then(setO).catch(() => {}) }, [])
  useEffect(() => {
    reload()
    const t = window.setInterval(reload, 10000)
    return () => window.clearInterval(t)
  }, [reload])
  if (!o || !o.devices.length) return null
  const cards = o.devices.map(d => <DeviceShared key={d.id} d={d} phone={phone} reload={reload} now={now} />)
  return (
    <div className="col sharedsection" style={{ gap: 10 }}>
      <span className="display" style={{ fontSize: phone ? 18 : 20 }}>Styles, presets and module layouts</span>
      {phone ? <>{cards}<Overview o={o} phone now={now} /></> : (
        <div className="sharedgrid">
          <div className="col" style={{ gap: 10, minWidth: 0 }}>{cards}</div>
          <Overview o={o} phone={false} now={now} />
        </div>
      )}
    </div>
  )
}
