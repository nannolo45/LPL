// SPDX-License-Identifier: GPL-3.0-or-later
// Settings > Devices & library: what the Devices page does not need every day. Two switches (open photos in
// darktable when they have arrived; the time left in the top bar), each device's details (system, the
// darktable bridge's status, its local library, taking an offline device off the list) and the library on
// the NAS (photo count, scanner, rescans, version).
import { useState } from 'react'
import { Agent, api } from '../api'
import { useStore } from '../store'
import { agentLabel, devicePills, serverNow } from '../devices'
import { ago } from '../transfer'
import { devicesApi, refreshStatus } from '../devicesApi'
import { setSetting, Settings, useSettings } from '../settings'
import { Chevron } from './DevicesBits'

function Switch({ k, label, help }: { k: 'devicesAutoOpen' | 'devicesEtaInBar' | 'devicesAutoMerge'; label: string; help: string }) {
  const st: Settings = useSettings()
  return (
    <div className="setrow">
      <div className="col" style={{ gap: 2, minWidth: 0, flex: '1 1 240px' }}>
        <span className="small">{label}</span>
        <span className="tiny muted">{help}</span>
      </div>
      <div className="seg" role="group" aria-label={label}>
        {([[true, 'On'], [false, 'Off']] as [boolean, string][]).map(([v, l]) => <button key={l} className={st[k] === v ? 'on' : ''} aria-pressed={st[k] === v} onClick={() => setSetting(k, v)}>{l}</button>)}
      </div>
    </div>
  )
}

/** R18: where exports from the darktable app are stored on the NAS. */
function ExportsFolder() {
  const st: Settings = useSettings()
  const [v, setV] = useState(st.exportsFolder || '')
  return (
    <div className="setrow">
      <div className="col" style={{ gap: 2, minWidth: 0, flex: '1 1 240px' }}>
        <span className="small">Exports folder</span>
        <span className="tiny muted">Where exports from the darktable app are stored, in the photo's folder path. Empty: the “exports” folder in the library. Not scanned as photos.</span>
      </div>
      <input value={v} placeholder="exports" aria-label="Exports folder" onChange={e => setV(e.target.value)} onBlur={() => v !== (st.exportsFolder || '') && setSetting('exportsFolder', v.trim())} />
    </div>
  )
}

/** R11: what happens when the same style, preset or layout was changed on a PC and on another device. */
function SharedPolicy() {
  const st: Settings = useSettings()
  const label = 'Same item changed on two devices'
  return (
    <div className="setrow">
      <div className="col" style={{ gap: 2, minWidth: 0, flex: '1 1 240px' }}>
        <span className="small">{label}</span>
        <span className="tiny muted">For darktable styles, presets and module layouts shared through LPL, on PCs. Ask me: the Devices page offers keep / take / keep both. Newest wins: the one changed last is kept. The phone app has its own setting.</span>
      </div>
      <div className="seg" role="group" aria-label={label}>
        {([['ask', 'Ask me'], ['newest', 'Newest wins']] as ['ask' | 'newest', string][]).map(([v, l]) =>
          <button key={v} className={st.sharedPolicy === v ? 'on' : ''} aria-pressed={st.sharedPolicy === v} onClick={() => setSetting('sharedPolicy', v)}>{l}</button>)}
      </div>
    </div>
  )
}

function DeviceDetails({ a, now }: { a: Agent; now: number }) {
  const s = useStore()
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<string | null>(null)
  const name = agentLabel(a, s.agents)
  const pill = devicePills(a, ago(a.last_seen, now))[0]
  const forget = () => window.confirm(`Remove “${name}” from this list? It comes back if it connects again.`)
    && devicesApi.forget(a.id).then(() => { s.toast('Removed'); refreshStatus() }).catch(e => s.toast(e.message))
  const save = () => {
    const v = (editing || '').trim()
    if (!v || v === a.name) return setEditing(null)
    devicesApi.rename(a.id, v).then(() => { setEditing(null); refreshStatus() }).catch(e => s.toast(e.message))
  }
  // the same computer under another id: this entry stopped reporting, the other is the one in use
  const twin = (a.same_machine || []).map(id => s.agents.find(x => x.id === id)).find(x => x && (x.online || x.last_seen > a.last_seen))
  const merge = () => twin && devicesApi.merge(a.id, twin.id).then(() => { s.toast(`Merged into ${agentLabel(twin, s.agents)}`); refreshStatus() }).catch(e => s.toast(e.message))
  return (
    <div className={'col devset' + (twin && !a.online ? ' twin' : '')} style={{ gap: 6, padding: '9px 0', borderBottom: '1px solid var(--line)' }}>
      <div className="row wrap">
        {editing != null ? (
          <form className="row" style={{ gap: 6 }} onSubmit={e => { e.preventDefault(); save() }}>
            <input className="input" aria-label={`New name for ${name}`} autoFocus value={editing} maxLength={60}
              onChange={e => setEditing(e.target.value)} onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); setEditing(null) } }} />
            <button className="btn primary" type="submit">Save name</button>
            <button className="btn ghost" type="button" onClick={() => setEditing(null)}>Cancel</button>
          </form>
        ) : <>
          <span className="display" style={{ fontSize: 15 }}>{name}</span>
          <button className="linkbtn tiny soft" style={{ textDecoration: 'underline' }} onClick={() => setEditing(a.name || '')}>Rename</button>
        </>}
        <span className={pill[0] ? 'pill-ok' : 'pill-off'}><i />{pill[1]}</span>
        <div className="grow" />
        <button className="devmore" aria-expanded={open} aria-label={`Details of ${name}`} onClick={() => setOpen(!open)}><Chevron open={open} />Details</button>
      </div>
      {a.renaming && <span className="tiny muted">{name} takes the new name the next time it reports{a.os === 'android' ? ' (the phone app keeps the name given here)' : ''}.</span>}
      {(a.merged_from || []).length > 0 && <span className="tiny muted">Earlier entries of this computer merged into it: {a.merged_from!.join(', ')}.</span>}
      {twin && !a.online && (
        <div role="alert" className="devfail" style={{ marginTop: 4 }}>
          <div className="col grow" style={{ gap: 2, minWidth: 0 }}>
            <b>Same computer as {agentLabel(twin, s.agents)}</b>
            <span className="small">The agent was installed again and came back under a new entry. Merging keeps {agentLabel(twin, s.agents)}'s name and moves over what is still linked to “{name}” (batches, the edit log).</span>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn primary" onClick={merge}>Merge into {agentLabel(twin, s.agents)}</button>
            <button className="btn danger" onClick={forget}>Remove “{name}”</button>
          </div>
        </div>
      )}
      {open && (
        <div className="col small" style={{ gap: 6, paddingLeft: 4 }}>
          <span className="soft">System: {a.os || 'unknown'}{a.state?.version ? ` · version ${a.state.version}` : ''}</span>
          <span className="soft">{(a.caps || []).includes('batch')
            ? `Reports how far a batch is${(a.caps || []).includes('cancel') ? ' and can stop one that is under way' : ''}.`
            : 'Does not report batches yet: no overall figure or Stop for a copy that is under way (photos it has not taken yet can still be taken back).'}</span>
          {a.state?.bridge_status
            ? <pre className="tiny muted" style={{ margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'ui-monospace, Consolas, monospace' }}>{'darktable bridge:\n' + a.state.bridge_status}</pre>
            : <span className="muted">darktable bridge: no status reported.</span>}
          {a.state?.local_url && <span className="soft">{name} also has a local library: on {name}, open <a href={a.state.local_url} target="_blank" rel="noreferrer">{a.state.local_url}</a></span>}
          {a.state?.local_error && <span className="muted">{a.state.local_error}</span>}
          {!a.online && <div><button className="btn danger" onClick={forget}>Remove {name} from this list</button></div>}
        </div>
      )}
    </div>
  )
}

export function DevicesSettings() {
  const s = useStore()
  const now = serverNow(s.status)
  const scan = s.status?.scan
  return (
    <div className="col" style={{ gap: 4 }}>
      {!s.isLocal && <>
        <span className="caps">When photos are sent to a device</span>
        <Switch k="devicesAutoOpen" label="Open photos in darktable when they have arrived"
          help="Off: they are copied and wait on the device until you open one. Devices that do not know this setting yet (older agents, the phone app) open them as before." />
        <Switch k="devicesEtaInBar" label="Show the time-left estimate in the top bar"
          help="Off: the top bar shows only how many photos have arrived. The Devices page always shows the estimate." />
        <Switch k="devicesAutoMerge" label="Merge a reinstalled agent into its old entry"
          help="On: when the agent is installed again on a computer LPL knows, the old entry is folded into the new one without asking. Off: both are listed and you merge them here." />
        <ExportsFolder />
        <SharedPolicy />
        <span className="caps" style={{ marginTop: 10 }}>Devices</span>
        {s.agents.length ? s.agents.map(a => <DeviceDetails key={a.id} a={a} now={now} />)
          : <span className="small muted" style={{ padding: '6px 0' }}>No device has connected yet.</span>}
      </>}
      <span className="caps" style={{ marginTop: 10 }}>{s.isLocal ? 'Library on this PC' : 'Library on the NAS'}</span>
      <div className="small soft" style={{ padding: '6px 0' }}>{s.status?.photos?.toLocaleString() ?? '…'} photos indexed · scanner {scan?.state || '…'}{scan?.phase ? ` (${scan.phase} ${scan.done}/${scan.total})` : ''} · previews pending {scan?.previews_pending ?? 0}</div>
      {scan?.error && <div className="small" style={{ color: '#ff8b8e' }}>{scan.error}</div>}
      <div className="row wrap">
        <button className="btn" onClick={() => api.scan(false).then(() => s.toast('Rescan started')).catch(e => s.toast(e.message))}>Rescan for new photos</button>
        <button className="btn" onClick={() => window.confirm('Re-read metadata of every photo? This takes a while on 20,000 photos.') && api.scan(true).then(() => s.toast('Full rescan started')).catch(e => s.toast(e.message))}>Full rescan</button>
        <span className="tiny muted">v{s.status?.version}</span>
      </div>
      <span className="tiny muted" style={{ marginTop: 10 }}>These switches are saved in the library, so every PC and the phone get the same.</span>
    </div>
  )
}
