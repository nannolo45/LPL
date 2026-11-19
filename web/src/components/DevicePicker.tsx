// SPDX-License-Identifier: GPL-3.0-or-later
import { useState } from 'react'
import { useStore } from '../store'
import { agentLabel, defaultAgent, isPhoneAgent } from '../devices'
import { useBackLayer } from '../back'

/** "Edit in darktable" when there is more than one device: pick where the photos open. Remembers the last
 *  choice on this browser; otherwise suggests a PC agent. A device that is offline gets the photos when it's back. */
export function DevicePicker() {
  const s = useStore()
  const ids = s.devicePick || []
  const [pick, setPick] = useState(() => {
    let last: string | null = null
    try { last = localStorage.getItem('lpl.editDevice') } catch { /* ignore */ }
    return defaultAgent(s.agents, last)?.id ?? ''
  })
  const go = () => { if (pick) s.editOn(ids, pick) }
  useBackLayer(s.isMobile, s.closeDevicePick)  // the phone's Back closes it
  return (
    <>
      <div className="backdrop" onClick={s.closeDevicePick} />
      <form className="dialog" role="dialog" aria-label="Edit in darktable on which device" onSubmit={e => { e.preventDefault(); go() }}
        onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); s.closeDevicePick() } }}>
        <b style={{ fontSize: 15 }}>Edit {ids.length > 1 ? `${ids.length} photos` : 'in darktable'} on which device?</b>
        <div className="col" style={{ gap: 6 }}>
          {s.agents.map(a => (
            <label key={a.id} className="row" style={{ gap: 8, cursor: 'pointer' }}>
              <input type="radio" name="device" checked={pick === a.id} onChange={() => setPick(a.id)} />
              <span className="grow">{agentLabel(a, s.agents)}</span>
              {a.online ? <span className="pill-ok"><i />online</span> : <span className="pill-off"><i />offline</span>}
            </label>
          ))}
        </div>
        {(() => { const a = s.agents.find(x => x.id === pick); return a && !a.online && <span className="small muted">
          {isPhoneAgent(a) ? 'It opens the next time the darktable app is opened on that device.' : 'It opens as soon as that device is online.'}</span> })()}
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="btn" onClick={s.closeDevicePick}>Cancel</button>
          <button type="submit" className="btn primary" autoFocus disabled={!pick}>Edit in darktable</button>
        </div>
      </form>
    </>
  )
}
