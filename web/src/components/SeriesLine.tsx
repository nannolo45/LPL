// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useState } from 'react'
import { api, Photo } from '../api'
import { useStore } from '../store'
import { num2 } from '../seriesnav'
import { useSeriesRev } from '../seriesops'

/** The series a photo is in, each with its folder and the photo's place: "Grecia / Cicladi · Naxos  position 06 of 14". */
export function SeriesLine({ id }: { id: number }) {
  const s = useStore()
  const rev = useSeriesRev()
  const [list, setList] = useState<NonNullable<Photo['series']>>([])
  useEffect(() => {
    let alive = true
    api.photo(id).then(p => { if (alive) setList(p.series || []) }).catch(() => {})
    return () => { alive = false }
  }, [id, rev, s.series])
  if (!list.length) return null
  return (
    <div className="col" style={{ gap: 3 }}>
      {list.map(x => (
        <button key={x.id} className="linkbtn sline" title="Open this series" onClick={() => { s.setBoardSeries(x.id); s.setView('board') }}>
          <i className="st-chip" style={x.color ? { background: x.color } : undefined} />
          <span className="ellipsis">{x.path?.length ? x.path.join(' / ') + ' / ' : x.folder ? x.folder + ' / ' : ''}{x.name}</span>
          <span className="small muted" style={{ whiteSpace: 'nowrap' }}>{x.num ? `position ${num2(x.num)} of ${x.count}` : 'not in the order'}</span>
        </button>
      ))}
    </div>
  )
}
