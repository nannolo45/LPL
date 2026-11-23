// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useState } from 'react'
import { api, Photo, VersionInfo, versionUrl } from '../api'
import { useStore } from '../store'
import { bumpVersions, canPin, deleteText, metaVersion, pickedVersion, pickVersion, pinFor, useVersionsRev, versionLabel, versionState, viewedPhoto, viewedVersion } from '../versions'
import { LABELS, stars } from '../fields'
import { useAsk } from './Ask'
import { EditOnPhone, useListTitle } from './PhoneEdit'
import { bumpSeries } from '../seriesops'

/** The versions of one photo and the one being looked at (R17). `view` is the photo as that version has it (rating, labels,
 *  tags), `mv` the `version` to send with a rating / label / tag change (undefined: the cover). A photo with one version
 *  gets view = the photo itself and no list. */
export function useVersionsFor(p: Photo | null) {
  const rev = useVersionsRev()
  const [list, setList] = useState<VersionInfo[] | null>(null)
  const many = !!p && (p.versions ?? 1) > 1
  const key = p ? [p.id, p.versions, p.cover, p.preview_ver, p.rating, p.labels.join(), p.meta_mtime, p.edited].join(':') : ''
  useEffect(() => {
    if (!p || !many) { setList(null); return }
    let live = true
    api.versions(p.id).then(r => { if (live) setList(r.versions) }).catch(() => {})
    return () => { live = false }
  }, [key, rev, many])
  if (!p) return { list: null, n: 0, view: null as Photo | null, mv: undefined as number | undefined, many: false }
  const n = viewedVersion(list, pickedVersion(p.id), p.cover ?? 0)
  return { list: many ? list : null, n, view: viewedPhoto(p, many ? list : null, n), mv: metaVersion(p, n), many }
}

/** The tile's count badge: "3" when the photo has three versions. */
export const VersionBadge = ({ p }: { p: Pick<Photo, 'versions'> }) =>
  (p.versions ?? 1) > 1 ? <span className="vcount" title={`${p.versions} versions`} aria-label={`${p.versions} versions`}>{p.versions}</span> : null

/** The versions strip of the photo panel (desktop) and of the photo page (phone): a thumbnail per version with its number,
 *  name, rating and labels; under it what can be done with the viewed one. */
export function VersionsStrip({ p, list, n }: { p: Photo; list: VersionInfo[]; n: number }) {
  const s = useStore()
  const [node, ask] = useAsk()
  const { series } = useListTitle()
  const cur = list.find(v => v.n === n) || list[0]
  const refresh = async () => { const r = await api.get([p.id]); s.mergePhotos(r.items); bumpVersions() }
  const fail = (e: any) => s.toast(e?.message || String(e))
  const setCover = async () => { try { await api.setCover(p.id, cur.n); await refresh(); s.toast(`${versionLabel(cur)} is the cover`) } catch (e) { fail(e) } }
  const rename = async () => {
    const name = await ask({ title: `Name of ${'v' + cur.n}`, field: 'Version name', initial: cur.name, ok: 'Save', text: 'Stored in its XMP, as darktable does. Leave it empty for no name.' })
    if (name == null) return
    try { await api.versionName(p.id, cur.n, name); bumpVersions() } catch (e) { fail(e) }
  }
  const del = async () => {
    const t = deleteText(cur)
    if (await ask({ ...t, ok: 'Delete', danger: true }) == null) return
    try {
      const r = await api.deleteVersion(p.id, cur.n)
      pickVersion(p.id, r.cover)
      await refresh(); bumpSeries()
      s.toast(`${versionLabel(cur)} moved to the backup folder`)
    } catch (e) { fail(e) }
  }
  const pin = async () => {
    if (series == null) return
    try { await api.seriesPin(series, p.id, pinFor(p, cur.n)); bumpSeries(); s.toast(pinFor(p, cur.n) == null ? 'The series follows the cover again' : `The series uses ${versionLabel(cur)}`) } catch (e) { fail(e) }
  }
  return (
    <div className="vstrip col" style={{ gap: 8 }} aria-label="Versions">
      {node}
      <div className="vrow">
        {list.map(v => (
          <button key={v.n} type="button" className={'vth' + (v.n === n ? ' on' : '')} aria-pressed={v.n === n} aria-label={`${versionLabel(v)}, ${versionState(v)}${v.cover ? ', cover' : ''}`}
            onClick={() => pickVersion(p.id, v.n)}>
            <span className="box"><img src={versionUrl(p.id, v.n, v.preview_ver)} alt="" draggable={false} loading="lazy" />
              {v.cover && <span className="vcover" title="cover">cover</span>}</span>
            <span className="vn">{versionLabel(v)}</span>
            <span className="vm">{v.rating > 0 ? stars(v.rating) : v.rating < 0 ? 'rej' : versionState(v)}
              {v.labels.map(l => <i key={l} style={{ background: LABELS[l].c }} />)}</span>
          </button>
        ))}
      </div>
      <div className="row wrap" style={{ gap: 6 }}>
        {!cur.cover && <button className="btn" onClick={setCover}>Set as cover</button>}
        {canPin({ cover: p.cover, pin: p.pin, show: p.show }, cur.n) && series != null && <button className="btn" onClick={pin}>Use in series</button>}
        <button className="btn" onClick={() => s.editInDarktable([p.id], cur.n)}>Edit this version in darktable</button>
        <EditOnPhone ids={[p.id]} start={p.id} version={cur.n} series={series} className="btn" label="Edit this version on this phone" />
        <button className="btn ghost" onClick={rename}>Name…</button>
        {cur.n > 0 && <button className="btn danger" onClick={del}>Delete…</button>}
      </div>
    </div>
  )
}
