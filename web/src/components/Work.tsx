import { useEffect, useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'
import { agentLabel, devicePills, isPhoneAgent, itemProgress, serverNow, STATUS } from '../devices'
import { ProgressBar, useTick } from './EditRecord'


const ago = (t: number, now: number) => { const s = Math.max(0, Math.round(now - t)); return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago` }

export function WorkView() {
  const s = useStore()
  const [jobs, setJobs] = useState<any[]>([])
  useEffect(() => {
    const t = () => api.jobs().then(r => setJobs(r.jobs)).catch(() => {})
    t(); const i = window.setInterval(t, 4000); return () => window.clearInterval(i)
  }, [])
  const now = serverNow(s.status)
  // while a device downloads or syncs: tick, so its bar goes away when it stops reporting
  useTick(s.agents.some(a => a.online && (a.state?.working_set || []).some(w => w.step)))
  const label = (id: string) => { const a = s.agents.find(x => x.id === id); return a ? agentLabel(a, s.agents) : id }
  const job = (kind: string, agent: string, ids?: number[]) =>
    api.job(kind, ids ? { ids } : { all: true }, agent).then(() => s.toast(`Sent to ${label(agent)}`)).catch(e => s.toast(e.message))
  const scan = s.status?.scan
  return (
    <div className="scroll">
      <div className="col" style={{ gap: 18, maxWidth: 1100, padding: s.isMobile ? '0 14px' : 0 }}>
        <div className="col" style={{ gap: 4 }}>
          <span className="display" style={{ fontSize: 24, fontWeight: 500 }}>Working set</span>
          <span className="soft">Photos copied to your devices so darktable can edit them at SSD speed. The NAS is read once and only the XMP goes back.</span>
        </div>
        {!s.agents.length && (
          <div className="card soft" style={{ lineHeight: 1.6 }}>
            No PC agent has connected yet. Install it on your Windows (or Linux) PC: see <b>agent/README</b> in the download.
            Once it runs, it appears here and “Edit in darktable” opens photos on that PC.
          </div>
        )}
        {s.agents.map(a => {
          const ws = a.state?.working_set || []
          const name = agentLabel(a, s.agents)
          const dirty = ws.filter(w => w.status === 'dirty' || w.status === 'editing').map(w => w.id)
          return (
            <div className="card" key={a.id}>
              <div className="row wrap">
                <span className="display" style={{ fontSize: 18 }}>{a.name || a.id}</span>
                <span className="small muted">{a.os}</span>
                {devicePills(a, ago(a.last_seen, now)).map(([ok, text]) => <span key={text} className={ok ? 'pill-ok' : 'pill-off'}><i />{text}</span>)}
                <div className="grow" />
                {!a.online && <button className="btn ghost" onClick={() => window.confirm(`Remove “${name}” from this list? It comes back if it connects again.`) && fetch(`/api/agents/${encodeURIComponent(a.id)}`, { method: 'DELETE' }).then(() => s.toast('Removed'))}>Remove</button>}
                <button className="btn primary" disabled={!dirty.length} title="Copy the edits of every changed photo back to the NAS" onClick={() => job('sync', a.id, dirty)}>Save {dirty.length || ''} edited to NAS</button>
                <button className="btn danger" disabled={!ws.some(w => w.status === 'synced')} title={`Delete the working copies on ${name} whose edits are already on the NAS`} onClick={() => job('release', a.id, ws.filter(w => w.status === 'synced').map(w => w.id))}>Remove saved copies from {name}</button>
              </div>
              {a.state?.message && <div className="small soft">{a.state.message}</div>}
              {isPhoneAgent(a) && !a.online && <div className="small muted">The darktable app reports only while it is open on {name}: what you start here waits until it is opened there.</div>}
              {a.online && a.state?.local_url && <span className="small soft">{name} also has a local library: on {name}, open <a href={a.state.local_url} target="_blank" rel="noreferrer">{a.state.local_url}</a></span>}
              {a.online && a.state?.local_error && <span className="small muted">{a.state.local_error}</span>}
              {a.online && a.state?.bridge_status && <pre className="tiny muted" style={{ margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'ui-monospace, Consolas, monospace' }}>{'darktable bridge:\n' + a.state.bridge_status}</pre>}
              {ws.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                  <table className="wtable">
                    <thead><tr><th /><th>Photo</th><th>Status</th><th>Checked out</th><th /></tr></thead>
                    <tbody>
                      {ws.map(w => {
                        const st = STATUS[w.status] || [w.status, '#2a2a2e', '#c8c8cc']
                        const pr = itemProgress(a, w, now)
                        return (
                          <tr key={w.id}>
                            <td style={{ width: 70 }}><img src={`/api/preview/${w.id}?s=t&v=${w.synced_ts || 0}`} alt="" title="Show in the library" onClick={() => s.reveal(w.id)} style={{ width: 64, height: 42, objectFit: 'cover', borderRadius: 4, cursor: 'pointer' }} onError={e => ((e.target as HTMLImageElement).style.visibility = 'hidden')} /></td>
                            <td><button className="linkbtn" title="Show in the library" onClick={() => s.reveal(w.id)}>{w.filename}</button>{w.error && <div className="tiny" style={{ color: '#ff8b8e' }}>{w.error}</div>}</td>
                            <td><div className="col" style={{ gap: 6, alignItems: 'flex-start' }}>
                              <span className="status" style={{ background: st[1], color: st[2] }}>{st[0]}</span>
                              {pr && <ProgressBar label={pr.step} fraction={pr.fraction} />}
                            </div></td>
                            <td className="muted">{w.checkout_ts ? ago(w.checkout_ts, now) : ''}</td>
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                              <button className="btn" title="Find it in LightPhotoLibrary: its folder, scrolled to it" onClick={() => s.reveal(w.id)}>Show in library</button>{' '}
                              <button className="btn" title={`Open the copy on ${name} in darktable's darkroom`} onClick={() => job('open', a.id, [w.id])}>Open in darktable</button>{' '}
                              <button className="btn" title={`Copy the edits (XMP) from ${name} back to the NAS now. The photo stays on ${name} so you can keep editing.`} onClick={() => job('sync', a.id, [w.id])}>Save edits to NAS</button>{' '}
                              <button className="btn danger" title={`Done with it: save the edits to the NAS, close it in darktable and delete the working copy on ${name}. The original on the NAS is never touched.`} onClick={() => job('release', a.id, [w.id])}>Done · remove from {name}</button>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              ) : <div className="small muted">{isPhoneAgent(a) ? `No photos on ${name}.` : `Nothing checked out on ${name}.`}</div>}
            </div>
          )
        })}

        <div className="card">
          <span className="display" style={{ fontSize: 17 }}>Recent hand-offs</span>
          {jobs.length ? jobs.slice(0, 12).map(j => (
            <div key={j.id} className="row small">
              <span className="muted" style={{ width: 90 }}>{ago(j.created, now)}</span>
              <span style={{ width: 70 }}>{j.kind}</span>
              <span className="grow muted ellipsis">{j.payload?.ids ? `${j.payload.ids.length} photo(s)` : 'all'} · {j.agent_id ? label(j.agent_id) : 'waiting for a PC'}</span>
              <span className={j.status === 'queued' ? 'soft' : 'muted'}>{j.status}</span>
            </div>
          )) : <span className="small muted">None yet.</span>}
        </div>

        <div className="card">
          <span className="display" style={{ fontSize: 17 }}>Library on the NAS</span>
          <div className="small soft">{s.status?.photos?.toLocaleString() ?? '…'} photos indexed · scanner {scan?.state || '…'}{scan?.phase ? ` (${scan.phase} ${scan.done}/${scan.total})` : ''} · previews pending {scan?.previews_pending ?? 0}</div>
          {scan?.error && <div className="small" style={{ color: '#ff8b8e' }}>{scan.error}</div>}
          <div className="row">
            <button className="btn" onClick={() => api.scan(false).then(() => s.toast('Rescan started'))}>Rescan for new photos</button>
            <button className="btn" onClick={() => window.confirm('Re-read metadata of every photo? This takes a while on 20,000 photos.') && api.scan(true).then(() => s.toast('Full rescan started'))}>Full rescan</button>
            <span className="tiny muted">v{s.status?.version}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
