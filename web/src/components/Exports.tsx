// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useState } from 'react'
import { api, ExportInfo, exportUrl, Photo } from '../api'
import { useStore } from '../store'
import { deleteExportText, exportLine } from '../exports'
import { useAsk } from './Ask'

/** The exports the darktable app sent for a photo (R18): name, size, device, date; download and delete. Nothing is
 *  shown for a photo without exports. */
export function ExportsList({ p }: { p: Photo }) {
  const s = useStore()
  const [node, ask] = useAsk()
  const [list, setList] = useState<ExportInfo[]>([])
  const has = (p.exports ?? 0) > 0
  useEffect(() => {
    let live = true
    if (!has) { setList([]); return }
    api.exportsOf(p.id).then(r => { if (live) setList(r.exports) }).catch(() => {})
    return () => { live = false }
  }, [p.id, p.exports])
  if (!has || !list.length) return node
  const del = async (e: ExportInfo) => {
    if (await ask({ ...deleteExportText(e), ok: 'Delete', danger: true }) == null) return
    try { const r = await api.deleteExport(p.id, e.id); setList(r.exports); s.toast(`${e.name} moved to the backup folder`) } catch (err: any) { s.toast(err.message) }
  }
  return (
    <div className="col" style={{ gap: 4 }} data-testid="exports">
      <span className="caps">Exports</span>
      {list.map(e => (
        <div key={e.id} className="row wrap" style={{ gap: 6 }}>
          <div className="col grow" style={{ gap: 0, minWidth: 0 }}>
            <span className="small" style={{ overflowWrap: 'anywhere' }}>{e.name}</span>
            <span className="tiny muted">{exportLine(e)}{e.has_file ? '' : ' · file missing'}</span>
          </div>
          {e.has_file && <a className="btn tiny" href={exportUrl(p.id, e.id)} download={e.name}>Download</a>}
          <button className="btn tiny" onClick={() => del(e)}>Delete</button>
        </div>
      ))}
      {node}
    </div>
  )
}
