// SPDX-License-Identifier: GPL-3.0-or-later
// R19 on the Devices page: "LUT files", darktable's LUT 3D files carried between the PCs' LUT folders and the phone.
// Each device's folder (or why it offers none), the files grouped by their top folder (each group folds, and can be
// removed from LPL in one go), who sent each last and a dot per device, and "Remove from LPL" (no device loses its
// copy). The whole section folds. See docs/luts-r19.md.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useStore } from '../store'
import { fmtWhen } from '../edits'
import { fmtBytes, folderLine, LUT_DOTS, lutCount, lutDot, LutFile, LutOverview, lutGroups, removeFolderQuestion, removeQuestion } from '../luts'
import { lutsApi } from '../lutsApi'
import { Chevron, FoldSection, useShowMore } from './DevicesBits'

function Dots({ f, o }: { f: LutFile; o: LutOverview }) {
  return <>{o.devices.map(d => {
    const v = lutDot(f, d.agent_id)
    return <span key={d.agent_id} className={'shareddot ' + (v === 'newest' ? 'newest' : v === 'other' || v === 'conflict' ? 'conflict' : 'absent')}
      title={`${d.name}: ${LUT_DOTS[v][1]}`} aria-label={`${d.name}: ${LUT_DOTS[v][1]}`}>{LUT_DOTS[v][0]}</span>
  })}</>
}

function Group({ name, files, o, phone, now, onlyOne, remove, removeFolder }: {
  name: string; files: LutFile[]; o: LutOverview; phone: boolean; now: number; onlyOne: boolean
  remove: (path: string) => void; removeFolder: (folder: string, n: number) => void
}) {
  const [open, setOpen] = useState(onlyOne)
  const { shown, more } = useShowMore(files, 50)
  const size = files.reduce((n, f) => n + f.size, 0)
  const last = (f: LutFile) => `${f.device_name || f.device_id || ''} · ${fmtWhen(f.changed_at, now)}`
  const big = phone ? ' big' : ''
  const label = name || 'Top level'
  return (
    <div className="col lutgroup" style={{ gap: 6 }}>
      <div className="row" style={{ gap: 10 }}>
        <button className="devmore grow" style={{ justifyContent: 'flex-start', fontSize: 13, color: 'var(--text)' }} aria-expanded={open} onClick={() => setOpen(!open)}>
          <Chevron open={open} /><b className="ellipsis">{label}</b><span className="muted">{files.length} file{files.length === 1 ? '' : 's'} · {fmtBytes(size)}</span>
        </button>
        {name && <button className={'btn' + big} title={`Remove every file in ${name} from LPL`} onClick={() => removeFolder(name, files.length)}>Remove folder from LPL</button>}
      </div>
      {open && (phone ? (
        <div className="col" style={{ gap: 8 }}>
          {shown.map(f => (
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
            {shown.map(f => (
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
      ))}
      {open && more}
    </div>
  )
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
  const files = useMemo(() => (o ? o.files.filter(f => !f.deleted) : []), [o])
  const groups = useMemo(() => lutGroups(files), [files])
  if (!o || (!o.devices.length && !files.length)) return null
  const remove = (path: string) => {
    if (!window.confirm(removeQuestion(path))) return
    lutsApi.remove(path).then(() => { s.toast(`${path} removed from LPL`); reload() }).catch(e => s.toast(e.message))
  }
  const removeFolder = (folder: string, n: number) => {
    if (!window.confirm(removeFolderQuestion(folder, n))) return
    lutsApi.removeFolder(folder).then(r => { s.toast(`${folder}: ${r.removed} file${r.removed === 1 ? '' : 's'} removed from LPL${r.freed_bytes ? `, ${fmtBytes(r.freed_bytes)} freed on the NAS` : ''}`); reload() })
      .catch(e => s.toast(e.message))
  }
  const noFolder = o.devices.filter(d => !d.folder)
  const summary = lutCount(o) + (noFolder.length ? ` · ${noFolder.map(d => d.name).join(', ')}: no LUT folder` : '')
  return (
    <FoldSection id="luts" title="LUT files" summary={summary} warn={noFolder.some(d => d.reason && /home folder|only LUT files/.test(d.reason))} phone={phone}>
      <span className="small muted">darktable's LUT 3D files (.cube, .3dl, .png, .gmz), from each PC's LUT folder and the phone. A device takes in the files it lacks and never replaces one it has.</span>
      {o.devices.length > 0 && (
        <div className="card sharedpanel">
          {o.devices.map(d => (
            <span key={d.agent_id} className="small"><b>{d.name}</b> <span className={d.folder ? 'muted' : 'sharedwarn'}>{folderLine(d)}</span></span>
          ))}
        </div>
      )}
      <div className="card sharedpanel">
        {!files.length ? <span className="small muted">No LUT files yet. Set darktable's LUT folder (preferences › processing) on a PC, or add files in the phone app.</span>
          : groups.map(([name, fs]) => <Group key={name} name={name} files={fs} o={o} phone={phone} now={now} onlyOne={groups.length === 1} remove={remove} removeFolder={removeFolder} />)}
        <span className="tiny muted">● has this file · ! a different file at this path, or changed there too · – not there or removed there</span>
      </div>
    </FoldSection>
  )
}
