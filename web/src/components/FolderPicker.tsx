// SPDX-License-Identifier: GPL-3.0-or-later
// A folder tree with checkboxes (several folders, optionally with their subfolders): the board's Add photos
// panel and Import from Miro use it.
import { useEffect, useMemo, useState } from 'react'
import { api, Folder } from '../api'
import { toggle } from './Left'
import { readCached, writeCached } from './Folders'

const lastSeg = (p: string) => p.slice(p.lastIndexOf('/') + 1) || p

export function FolderPicker({ picked, sub, onPicked, onSub, hideSub }: { picked: string[]; sub: boolean; onPicked: (f: string[]) => void; onSub: (v: boolean) => void; hideSub?: boolean }) {
  const [folders, setFolders] = useState<Folder[]>(() => readCached()?.folders || [])
  const [fq, setFq] = useState('')
  const [open, setOpen] = useState<Set<string>>(() => new Set())
  useEffect(() => { api.folders().then(r => { writeCached(r); setFolders(r.folders) }).catch(() => {}) }, [])

  // folder tree (parents added even when they hold no photos themselves)
  const tree = useMemo(() => {
    const by = new Map<string, { path: string; count: number }>()
    for (const f of folders) by.set(f.path, { path: f.path, count: f.count })
    for (const f of folders) {
      let p = f.path
      while (p.includes('/')) { p = p.slice(0, p.lastIndexOf('/')); if (!by.has(p)) by.set(p, { path: p, count: 0 }) }
    }
    const list = [...by.values()].filter(f => f.path).sort((a, b) => a.path.localeCompare(b.path, undefined, { numeric: true, sensitivity: 'base' }))
    const kids = new Set(list.filter(f => f.path.includes('/')).map(f => f.path.slice(0, f.path.lastIndexOf('/'))))
    return { list, kids }
  }, [folders])

  const rows = useMemo(() => {
    const q = fq.trim().toLowerCase()
    if (q) return tree.list.filter(f => f.path.toLowerCase().includes(q)).slice(0, 300).map(f => ({ ...f, depth: 0, flat: true }))
    return tree.list.filter(f => {
      let p = f.path
      while (p.includes('/')) { p = p.slice(0, p.lastIndexOf('/')); if (!open.has(p)) return false }
      return true
    }).map(f => ({ ...f, depth: f.path.split('/').length - 1, flat: false }))
  }, [tree, fq, open])

  const coveredBy = (p: string) => (sub ? picked.find(x => x !== p && p.startsWith(x + '/')) : undefined)
  const flipFolder = (p: string) => {
    let next = toggle(picked, p)
    if (sub && next.includes(p)) next = next.filter(x => !x.startsWith(p + '/'))  // the parent now covers them
    onPicked(next)
  }

  return (
    <>
            <input className="input" placeholder="Search folders" value={fq} onChange={e => setFq(e.target.value)} />
            <div className="addp-tree">
              {rows.map(f => {
                const on = picked.includes(f.path), cov = coveredBy(f.path)
                const hasKids = tree.kids.has(f.path)
                return (
                  <div key={f.path} className={'addp-f' + (on ? ' on' : '')} style={{ paddingLeft: 4 + f.depth * 16 }} title={f.path}>
                    {!f.flat && hasKids
                      ? <button className="chevb" aria-label={open.has(f.path) ? 'Collapse' : 'Expand'} onClick={() => setOpen(o => { const n = new Set(o); n.has(f.path) ? n.delete(f.path) : n.add(f.path); return n })}>
                          <span style={{ display: 'inline-block', transform: open.has(f.path) ? 'rotate(90deg)' : 'none' }}>›</span></button>
                      : <span className="chevb" />}
                    <label className="row grow" style={{ gap: 7, cursor: cov ? 'default' : 'pointer', minWidth: 0 }}>
                      <input type="checkbox" checked={on || !!cov} disabled={!!cov} onChange={() => flipFolder(f.path)} style={{ accentColor: '#E8A33D' }} />
                      <span className="ellipsis grow">{f.flat ? f.path : lastSeg(f.path)}</span>
                    </label>
                    {f.count > 0 && <span className="tiny muted">{f.count}</span>}
                  </div>
                )
              })}
              {!rows.length && <div className="small muted" style={{ padding: 10 }}>{folders.length ? 'No folder matches.' : 'Loading folders…'}</div>}
            </div>
            {!hideSub && <label className="row small" style={{ cursor: 'pointer' }}>
              <input type="checkbox" checked={sub} onChange={() => onSub(!sub)} style={{ accentColor: '#E8A33D' }} />
              Include subfolders
            </label>}
    </>
  )
}
