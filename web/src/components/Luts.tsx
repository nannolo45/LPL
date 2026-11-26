// SPDX-License-Identifier: GPL-3.0-or-later
// R19 on the Devices page: "LUT files", darktable's LUT 3D files carried between the PCs' LUT folders and the phone.
// Each device's folder (or why it offers none), every file with who sent it last and a dot per device, and
// "Remove from LPL" (no device loses its copy). See docs/luts-r19.md.
import { useCallback, useEffect, useState } from 'react'
import { useStore } from '../store'
import { fmtWhen } from '../edits'
import { fmtBytes, folderLine, LUT_DOTS, lutCount, lutDot, LutFile, LutOverview, removeQuestion } from '../luts'
import { lutsApi } from '../lutsApi'

function Dots({ f, o }: { f: LutFile; o: LutOverview }) {
  return <>{o.devices.map(d => {
    const v = lutDot(f, d.agent_id)
    return <span key={d.agent_id} className={'shareddot ' + (v === 'newest' ? 'newest' : v === 'other' || v === 'conflict' ? 'conflict' : 'absent')}
      title={`${d.name}: ${LUT_DOTS[v][1]}`} aria-label={`${d.name}: ${LUT_DOTS[v][1]}`}>{LUT_DOTS[v][0]}</span>
  })}</>
}

/** The section on the Devices page; nothing when no device takes part and LPL has no LUT file. */
export function LutSection({ phone, now }: { phone: boolean; now: number }) {
  const s = useStore()
  const [o, setO] = useState<LutOverview | null>(null)
  const reload = useCallback(() => { lutsApi.overview().then(setO).catch(() => {}) }, [])
  useEffect(() => {
    reload()
    const t = window.setInterval(reload, 10000)
    return () => window.clearInterval(t)
  }, [reload])
  if (!o || (!o.devices.length && !o.files.length)) return null
  const files = o.files.filter(f => !f.deleted)
  const remove = (path: string) => {
    if (!window.confirm(removeQuestion(path))) return
    lutsApi.remove(path).then(() => { s.toast(`${path} removed from LPL`); reload() }).catch(e => s.toast(e.message))
  }
  const big = phone ? ' big' : ''
  const last = (f: LutFile) => `${f.device_name || f.device_id || ''} · ${fmtWhen(f.changed_at, now)}`
  return (
    <div className="col sharedsection" style={{ gap: 10 }}>
      <span className="display" style={{ fontSize: phone ? 18 : 20 }}>LUT files</span>
      <span className="small muted">darktable's LUT 3D files (.cube, .3dl, .png, .gmz), from each PC's LUT folder and the phone. A device takes in the files it lacks and never replaces one it has. {lutCount(o)}</span>
      <div className="card sharedpanel">
        {o.devices.map(d => (
          <span key={d.agent_id} className="small"><b>{d.name}</b> <span className={d.folder ? 'muted' : 'sharedwarn'}>{folderLine(d)}</span></span>
        ))}
      </div>
      <div className="card sharedpanel">
        {!files.length ? <span className="small muted">No LUT files yet. Set darktable's LUT folder (preferences › processing) on a PC, or add files in the phone app.</span>
          : phone ? (
            <div className="col" style={{ gap: 8 }}>
              {files.map(f => (
                <div key={f.path} className="col sharedrow" style={{ gap: 2 }}>
                  <span className="row" style={{ gap: 8 }}><b className="grow ellipsis">{f.path}</b><span className="shareddots"><Dots f={f} o={o} /></span></span>
                  <span className="small muted">{fmtBytes(f.size)} · {last(f)}{f.history ? ` · ${f.history} earlier` : ''}</span>
                  <span className="row" style={{ justifyContent: 'flex-end' }}><button className={'btn' + big} onClick={() => remove(f.path)}>Remove from LPL</button></span>
                </div>
              ))}
            </div>
          ) : (
            <table className="devtable sharedtable" style={{ width: '100%' }}>
              <thead><tr><th>File</th><th>Size</th><th>Last sent</th><th title={o.devices.map(d => d.name).join(' · ')}>{o.devices.map(d => d.name.slice(0, 1).toUpperCase()).join(' · ')}</th><th /></tr></thead>
              <tbody>
                {files.map(f => (
                  <tr key={f.path}>
                    <td>{f.path}{f.history ? <span className="tiny muted"> · {f.history} earlier</span> : null}</td>
                    <td className="muted nowrap">{fmtBytes(f.size)}</td>
                    <td className="muted">{last(f)}</td>
                    <td className="shareddots nowrap"><Dots f={f} o={o} /></td>
                    <td><button className="btn" onClick={() => remove(f.path)}>Remove from LPL</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        <span className="tiny muted">● has this file · ! a different file at this path, or changed there too · – not there or removed there</span>
      </div>
    </div>
  )
}
