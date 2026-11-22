import { useState } from 'react'
import { SeriesInfo } from '../api'
import { TabState, useStore, View, WTab } from '../store'
import { fmtMonth, leaf } from '../fields'
import { keysOf, useShortcutsVersion } from '../shortcuts'

const ICON: Record<View, JSX.Element> = {
  grid: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" aria-hidden="true"><rect x="3.5" y="3.5" width="7" height="7" rx="1.2" /><rect x="13.5" y="3.5" width="7" height="7" rx="1.2" /><rect x="3.5" y="13.5" width="7" height="7" rx="1.2" /><rect x="13.5" y="13.5" width="7" height="7" rx="1.2" /></svg>,
  list: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13" /><circle cx="3.8" cy="6" r="1" fill="currentColor" /><circle cx="3.8" cy="12" r="1" fill="currentColor" /><circle cx="3.8" cy="18" r="1" fill="currentColor" /></svg>,
  folders: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinejoin="round" aria-hidden="true"><path d="M3 6.5A1.5 1.5 0 014.5 5h4.2l2 2.2h8.8A1.5 1.5 0 0121 8.7v9.8a1.5 1.5 0 01-1.5 1.5h-15A1.5 1.5 0 013 18.5z" /></svg>,
  timeline: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15" rx="2" /><path d="M3.5 10h17M8 3v4M16 3v4" /></svg>,
  board: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="8" height="6" rx="1" /><rect x="13" y="4" width="8" height="10" rx="1" /><rect x="3" y="12" width="8" height="8" rx="1" /><rect x="13" y="16" width="8" height="4" rx="1" /></svg>,
  work: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="1.5" /><path d="M8 20h8M12 16v4" /></svg>,
  send: <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 15v4a1 1 0 001 1h14a1 1 0 001-1v-4" /></svg>,
}
const NAME: Record<View, string> = { grid: 'Grid', list: 'List', folders: 'Folders', timeline: 'Timeline', board: 'Board', work: 'Working set', send: 'Send to NAS' }

/** What a tab shows, in a few words: its own name if you gave one, else from its view and filters. */
export function tabTitle(t: WTab, series: SeriesInfo[]): string {
  if (t.title) return t.title
  if (t.st.view === 'work') return 'Devices'  // the Devices page, in a tab of its own
  const st: TabState = t.st, f = st.filters || {}
  const sname = (id?: number | null) => series.find(x => x.id === id)?.name
  if (st.view === 'board') return 'Board · ' + (sname(st.boardSeries) || series[0]?.name || 'series')
  if (st.view === 'work' || st.view === 'send') return NAME[st.view]
  if (st.view === 'folders') {
    let cur = ''
    try { cur = localStorage.getItem('lpl.folder.' + t.id) || '' } catch { /* ignore */ }
    return cur ? 'Folders · ' + cur.split('/').pop() : 'Folders'
  }
  const parts: string[] = []
  if (f.series) parts.push(sname(f.series) || 'series')
  if (f.folder) parts.push(f.folder.split('/').pop() || f.folder)
  if (f.folders?.length) parts.push(f.folders.length === 1 ? f.folders[0].split('/').pop()! : `${f.folders.length} folders`)
  if (f.tag) parts.push(leaf(f.tag))
  if (f.tags?.length) parts.push(f.tags.map(leaf).join(', '))
  if (f.month) parts.push(fmtMonth(f.month + '-01'))
  if (f.q) parts.push(`“${f.q}”`)
  if (f.ratings?.length) parts.push(f.ratings.map(r => (r > 0 ? '★' + r : r === 0 ? 'unrated' : 'rejected')).join(' '))
  if (f.labels?.length) parts.push(`${f.labels.length} label${f.labels.length > 1 ? 's' : ''}`)
  if (f.lenses?.length) parts.push(f.lenses.length === 1 ? f.lenses[0] : `${f.lenses.length} lenses`)
  if (f.cameras?.length) parts.push(f.cameras.length === 1 ? f.cameras[0] : `${f.cameras.length} cameras`)
  if (f.edited) parts.push(f.edited === 'yes' ? 'edited' : 'not edited')
  if (f.only_hidden) parts.push('hidden photos')
  return parts.join(' · ') || (st.view === 'timeline' ? 'Timeline' : 'All photos')
}

export function TabStrip() {
  const s = useStore()
  useShortcutsVersion()
  const [editing, setEditing] = useState<string | null>(null)
  const [drag, setDrag] = useState<string | null>(null)
  const [over, setOver] = useState<number | null>(null)
  const tabs = s.tabs.map(t => (t.id === s.activeTab ? { ...t, st: s.liveTab() } : t))
  return (
    <div className="wtabs" role="tablist" aria-label="Tabs">
      {tabs.map((t, i) => {
        const title = tabTitle(t, s.series)
        const on = t.id === s.activeTab
        return (
          <div key={t.id} role="tab" aria-selected={on} className={'wtab' + (on ? ' on' : '') + (over === i && drag && drag !== t.id ? ' over' : '')} title={title}
            draggable={editing !== t.id}
            onDragStart={e => { setDrag(t.id); e.dataTransfer.setData('text/plain', t.id); e.dataTransfer.effectAllowed = 'move' }}
            onDragOver={e => { if (drag) { e.preventDefault(); setOver(i) } }} onDragLeave={() => setOver(null)}
            onDrop={e => { e.preventDefault(); if (drag) s.moveTab(drag, i); setDrag(null); setOver(null) }} onDragEnd={() => { setDrag(null); setOver(null) }}
            onMouseDown={e => { if (e.button === 1) { e.preventDefault(); s.closeTab(t.id) } }}
            onClick={() => s.switchTab(t.id)} onDoubleClick={() => setEditing(t.id)}>
            <span className="wtab-ic">{ICON[t.st.view]}</span>
            {editing === t.id
              ? <input autoFocus className="wtab-in" defaultValue={t.title || title} aria-label="Tab name"
                  onClick={e => e.stopPropagation()}
                  onBlur={e => { s.renameTab(t.id, e.target.value === title && !t.title ? '' : e.target.value); setEditing(null) }}
                  onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setEditing(null) }} />
              : <span className="wtab-t">{title}</span>}
            {s.tabs.length > 1 && <button className="wtab-x" aria-label={`Close ${title}`} onClick={e => { e.stopPropagation(); s.closeTab(t.id) }}>×</button>}
          </div>
        )
      })}
      <button className="wtab-new" title={`New tab, showing all photos (${keysOf('newTab').join(', ') || 'no key'}). Double-click a tab to name it, drag to reorder, middle-click to close.`} aria-label="New tab" onClick={() => s.newTab()}>+</button>
    </div>
  )
}

/** Grid, List, Folders, Timeline, Board as one compact switch; Send to NAS apart (local library only).
 *  The Devices page is not a view: it opens as a tab from the top bar's indicator. On that tab nothing
 *  is selected here, and choosing a view turns the tab into that view. */
export function ViewSwitch() {
  const s = useStore()
  const views: View[] = ['grid', 'list', 'folders', 'timeline', 'board']
  const extra: View = 'send'
  return (
    <div className="row" style={{ gap: 8 }}>
      <div className="vswitch" role="group" aria-label="View">
        {views.map(v => (
          <button key={v} className={s.view === v ? 'on' : ''} aria-pressed={s.view === v} title={NAME[v]} aria-label={NAME[v]} onClick={() => s.setView(v)}>
            {ICON[v]}{s.view === v && <span>{NAME[v]}</span>}
          </button>
        ))}
      </div>
      {s.isLocal && <button className={'vbtn' + (s.view === extra ? ' on' : '')} title={NAME[extra]} onClick={() => s.setView(extra)}>
        {ICON[extra]}<span>{NAME[extra]}</span>
      </button>}
    </div>
  )
}
