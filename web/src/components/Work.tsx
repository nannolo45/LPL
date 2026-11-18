import { useEffect, useState } from 'react'
import { api } from '../api'
import { useStore } from '../store'

const STATUS: Record<string, [string, string, string]> = {
  downloading: ['copying to this PC', '#2a2a2e', '#c8c8cc'],
  opening: ['opening in darktable', '#3a2f1c', '#E8A33D'],
  editing: ['open in darktable', '#3a2f1c', '#E8A33D'],
  dirty: ['edited · not synced yet', '#1c2a3a', '#8cb8e6'],
  syncing: ['syncing', '#1c2a3a', '#8cb8e6'],
  synced: ['synced to NAS', '#1d2e1d', '#8fd08f'],
  error: ['error', '#3a1c1c', '#ff8b8e'],
}

const ago = (t: number, now: number) => { const s = Math.max(0, Math.round(now - t)); return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago` }

export function WorkView() {
  const s = useStore()
  const [jobs, setJobs] = useState<any[]>([])
  useEffect(() => {
    const t = () => api.jobs().then(r => setJobs(r.jobs)).catch(() => {})
    t(); const i = window.setInterval(t, 4000); return () => window.clearInterval(i)
  }, [])
  const now = s.status?.server_time || Date.now() / 1000
  const job = (kind: string, agent: string, ids?: number[]) =>
    api.job(kind, ids ? { ids } : { all: true }, agent).then(() => s.toast('Sent to the PC agent')).catch(e => s.toast(e.message))
  const scan = s.status?.scan
  return (
    <div className="scroll">
      <div className="col" style={{ gap: 18, maxWidth: 1100, padding: s.isMobile ? '0 14px' : 0 }}>
        <div className="col" style={{ gap: 4 }}>
          <span className="display" style={{ fontSize: 24, fontWeight: 500 }}>Working set</span>
          <span className="soft">Photos copied to a PC so darktable can edit them at SSD speed. The NAS is read once and only the XMP goes back.</span>
        </div>
        {!s.agents.length && (
          <div className="card soft" style={{ lineHeight: 1.6 }}>
            No PC agent has connected yet. Install it on your Windows (or Linux) PC: see <b>agent/README</b> in the download.
            Once it runs, it appears here and “Edit in darktable” opens photos on that PC.
          </div>
        )}
        {s.agents.map(a => {
          const ws = a.state?.working_set || []
          const dirty = ws.filter(w => w.status === 'dirty' || w.status === 'editing').map(w => w.id)
          return (
            <div className="card" key={a.id}>
              <div className="row wrap">
                <span className="display" style={{ fontSize: 18 }}>{a.name || a.id}</span>
                <span className="small muted">{a.os}</span>
                {a.online ? <span className="pill-ok"><i />online</span> : <span className="pill-off"><i />offline · seen {ago(a.last_seen, now)}</span>}
                {a.online && <span className={a.state?.darktable_running ? 'pill-ok' : 'pill-off'}><i />darktable {a.state?.darktable_running ? 'running' : 'not running'}</span>}
                {a.online && a.state?.darktable_running && <span className={a.state?.bridge_alive ? 'pill-ok' : 'pill-off'}><i />bridge script {a.state?.bridge_alive ? 'active' : 'not seen'}</span>}
                <div className="grow" />
                {!a.online && <button className="btn ghost" onClick={() => window.confirm(`Remove “${a.name}” from this list? It comes back if its agent connects again.`) && fetch(`/api/agents/${encodeURIComponent(a.id)}`, { method: 'DELETE' }).then(() => s.toast('Removed'))}>Remove</button>}
                <button className="btn primary" disabled={!dirty.length} onClick={() => job('sync', a.id, dirty)}>Sync {dirty.length || ''} back to NAS</button>
                <button className="btn" disabled={!ws.some(w => w.status === 'synced')} onClick={() => job('release', a.id, ws.filter(w => w.status === 'synced').map(w => w.id))}>Free synced copies</button>
              </div>
              {a.state?.message && <div className="small soft">{a.state.message}</div>}
              {a.online && a.state?.local_url && <span className="small soft">This PC also has a local library: on that PC, open <a href={a.state.local_url} target="_blank" rel="noreferrer">{a.state.local_url}</a></span>}
              {a.online && a.state?.local_error && <span className="small muted">{a.state.local_error}</span>}
              {a.online && a.state?.bridge_status && <pre className="tiny muted" style={{ margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'ui-monospace, Consolas, monospace' }}>{'darktable bridge:\n' + a.state.bridge_status}</pre>}
              {ws.length > 0 ? (
                <div style={{ overflowX: 'auto' }}>
                  <table className="wtable">
                    <thead><tr><th /><th>Photo</th><th>Status</th><th>Checked out</th><th /></tr></thead>
                    <tbody>
                      {ws.map(w => {
                        const st = STATUS[w.status] || [w.status, '#2a2a2e', '#c8c8cc']
                        return (
                          <tr key={w.id}>
                            <td style={{ width: 70 }}><img src={`/api/preview/${w.id}?s=t&v=${w.synced_ts || 0}`} alt="" style={{ width: 64, height: 42, objectFit: 'cover', borderRadius: 4 }} onError={e => ((e.target as HTMLImageElement).style.visibility = 'hidden')} /></td>
                            <td>{w.filename}{w.error && <div className="tiny" style={{ color: '#ff8b8e' }}>{w.error}</div>}</td>
                            <td><span className="status" style={{ background: st[1], color: st[2] }}>{st[0]}</span></td>
                            <td className="muted">{w.checkout_ts ? ago(w.checkout_ts, now) : ''}</td>
                            <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                              <button className="btn" onClick={() => job('open', a.id, [w.id])}>Open</button>{' '}
                              <button className="btn" onClick={() => job('sync', a.id, [w.id])}>Sync</button>{' '}
                              <button className="btn" onClick={() => job('release', a.id, [w.id])}>Release</button>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              ) : <div className="small muted">Nothing checked out on this PC.</div>}
            </div>
          )
        })}

        <div className="card">
          <span className="display" style={{ fontSize: 17 }}>Recent hand-offs</span>
          {jobs.length ? jobs.slice(0, 12).map(j => (
            <div key={j.id} className="row small">
              <span className="muted" style={{ width: 90 }}>{ago(j.created, now)}</span>
              <span style={{ width: 70 }}>{j.kind}</span>
              <span className="grow muted ellipsis">{j.payload?.ids ? `${j.payload.ids.length} photo(s)` : 'all'} · {j.agent_id || 'waiting for a PC'}</span>
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
