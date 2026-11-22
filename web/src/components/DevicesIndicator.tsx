// SPDX-License-Identifier: GPL-3.0-or-later
// The top bar's devices indicator: a device icon with a ring that fills while photos are sent
// ("38 / 120 · about 6 min left"), "stopped" after a stop, otherwise how many devices are online.
// A click opens a popover with one line per device, Stop / Resume and "Open Devices" (desktop), or the
// Devices page itself (phone). See docs/devices-stop-estimate.md.
import { useEffect, useRef, useState } from 'react'
import { Agent } from '../api'
import { Store, useStore } from '../store'
import { agentLabel, devicePills, hasUnsavedEdits, serverNow } from '../devices'
import { ago, deviceCounts, onlineCount, overall, Overall } from '../transfer'
import { refreshStatus } from '../devicesApi'
import { useSettings } from '../settings'
import { useTick } from './EditRecord'
import { StoppedBox, TransferBox, useDeviceActions } from './Devices'

/** Open the Devices page in a tab of the tab strip; if one is open already, go to it instead. */
export function openDevices(s: Pick<Store, 'tabs' | 'activeTab' | 'view' | 'switchTab' | 'newTab'>) {
  if (s.view === 'work') return
  const t = s.tabs.find(t => t.id !== s.activeTab && t.st.view === 'work')
  if (t) s.switchTab(t.id)
  else s.newTab({ view: 'work' })
}
const devicesTabOpen = (s: Pick<Store, 'tabs' | 'activeTab' | 'view'>) => s.view === 'work' || s.tabs.some(t => t.id !== s.activeTab && t.st.view === 'work')

const RING = 2 * Math.PI * 11
/** The device icon inside the progress ring. */
function Ring({ o, size }: { o: Overall; size: number }) {
  const moving = o.kind === 'running'
  return (
    <svg width={size} height={size} viewBox="0 0 26 26" fill="none" aria-hidden="true" style={{ flex: '0 0 auto' }}>
      <circle cx="13" cy="13" r="11" stroke={o.fraction == null ? 'transparent' : '#3c3c44'} strokeWidth="2.4" />
      {o.fraction != null && o.fraction > 0 && (
        <circle cx="13" cy="13" r="11" stroke={moving ? '#E8A33D' : '#8c8c94'} strokeWidth="2.4" strokeLinecap="round"
          strokeDasharray={`${(Math.max(.02, o.fraction) * RING).toFixed(1)} ${RING.toFixed(1)}`} transform="rotate(-90 13 13)" style={{ transition: 'stroke-dasharray .4s' }} />
      )}
      <rect x="8.2" y="8.6" width="9.6" height="6.6" rx="1" stroke="currentColor" strokeWidth="1.4" />
      <path d="M10.8 18.2h4.4M13 15.2v3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

/** What the indicator shows now; while photos are on their way the devices' state is fetched every two
 *  seconds (instead of four), so the numbers here, in the popover and on the page follow the device closely. */
function useOverall(): { o: Overall; now: number } {
  const s = useStore()
  const now = serverNow(s.status)
  const o = overall(s.agents, now)
  const busy = o.kind === 'running' || o.kind === 'stopping'
  useTick(busy || o.kind === 'offline')
  useEffect(() => {
    if (!busy) return
    const t = window.setInterval(refreshStatus, 2000)
    return () => window.clearInterval(t)
  }, [busy])
  return { o, now }
}

/** Desktop: the pill in the top bar and its popover. */
export function DevicesIndicator() {
  const s = useStore()
  const st = useSettings()
  const { o, now } = useOverall()
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const btn = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    const out = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false) }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false); btn.current?.focus() } }
    window.addEventListener('mousedown', out)
    window.addEventListener('keydown', key, true)
    return () => { window.removeEventListener('mousedown', out); window.removeEventListener('keydown', key, true) }
  }, [open])
  const sub = o.kind === 'running' && !st.devicesEtaInBar ? '' : o.sub
  return (
    <div className="dev-ind-wrap" ref={wrap}>
      <button ref={btn} className={'dev-ind' + (open ? ' on' : '')} aria-label={o.label} aria-expanded={open} aria-haspopup="dialog"
        title={s.agents.length ? 'Devices' : 'Devices: start the LightPhotoLibrary agent on your PC'} onClick={() => setOpen(!open)}>
        <Ring o={o} size={26} />
        <b>{o.text}</b>{sub && <span>{sub}</span>}
      </button>
      {open && <Popover now={now} onClose={() => setOpen(false)} />}
    </div>
  )
}

function Popover({ now, onClose }: { now: number; onClose: () => void }) {
  const s = useStore()
  const act = useDeviceActions()
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => { ref.current?.focus() }, [])
  const has = devicesTabOpen(s)
  const line = (a: Agent) => {
    const name = agentLabel(a, s.agents)
    const unsaved = (a.state?.working_set || []).filter(hasUnsavedEdits).map(w => w.id)
    return (
      <div key={a.id} className="dev-pop-line">
        <i className={'dev-dot' + (a.online ? ' on' : '')} />
        <div className="col grow" style={{ gap: 2 }}>
          <span><span className="display" style={{ fontSize: 14, color: '#fff' }}>{name}</span> <span className="small muted">· {devicePills(a, ago(a.last_seen, now))[0][1]}</span></span>
          <span className="small soft">{deviceCounts(a)}</span>
        </div>
        {unsaved.length > 0 && <button className="btn primary" title={`Copy the edits of the ${unsaved.length} changed photo${unsaved.length === 1 ? '' : 's'} back to the NAS`} onClick={() => act.job('sync', a.id, unsaved)}>Save {unsaved.length} to NAS</button>}
      </div>
    )
  }
  return (
    <div className="dev-pop" role="dialog" aria-label="Devices" tabIndex={-1} ref={ref}>
      <div className="row" style={{ alignItems: 'baseline' }}>
        <span className="display" style={{ fontSize: 16, fontWeight: 500, color: '#fff' }}>Devices</span>
        <span className="small muted">{s.agents.length ? onlineCount(s.agents) : 'none yet'}</span>
      </div>
      {s.agents.filter(a => a.transfer).map(a => <TransferBox key={a.id} a={a} name={agentLabel(a, s.agents)} now={now} act={act} compact />)}
      {s.agents.filter(a => a.stopped && !a.transfer).map(a => <StoppedBox key={a.id} a={a} name={agentLabel(a, s.agents)} act={act} compact />)}
      {s.agents.length > 0
        ? <div className="col" style={{ gap: 0 }}>{s.agents.map(line)}</div>
        : <span className="small soft">No PC agent has connected yet. Install it on your Windows (or Linux) PC: see <b>agent/README</b> in the download.</span>}
      <button className="dev-open" onClick={() => { openDevices(s); onClose() }}>
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M14 4h6v6M20 4l-9 9" /><path d="M19 14v4.5A1.5 1.5 0 0117.5 20h-12A1.5 1.5 0 014 18.5v-12A1.5 1.5 0 015.5 5H10" /></svg>
        Open Devices
      </button>
      <span className="tiny muted" style={{ textAlign: 'center', marginTop: -6 }}>{s.view === 'work' ? 'You are on the Devices page.' : has ? 'Goes to the Devices tab that is open already.' : 'Opens the full page in a new tab, next to your other tabs.'}</span>
    </div>
  )
}

/** Phone: the round 44 px button in the header; a tap opens the Devices page (pressed while it is open). */
export function DevicesIndicatorPhone({ pressed, onOpen }: { pressed: boolean; onOpen: () => void }) {
  const { o } = useOverall()
  return (
    <button className={'dev-ind round' + (pressed ? ' on' : '')} aria-pressed={pressed} aria-label={o.label} title="Devices" onClick={onOpen}>
      <Ring o={o} size={30} />
    </button>
  )
}
