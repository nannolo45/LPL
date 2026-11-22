import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { Agent, api, Filters, Photo, SeriesFolder, SeriesInfo, SortLevel, Tag } from './api'
import { keysOf } from './shortcuts'
import { agentLabel, needsDeviceChoice } from './devices'

export type View = 'grid' | 'list' | 'folders' | 'timeline' | 'board' | 'work' | 'send'
export type Gran = 'day' | 'month' | 'year'

/** A tab: its own view, filters, sort and board, so several ways into the library can stay open side by side. */
export interface TabState { view: View; sort: SortLevel[]; group: boolean; gran: Gran; filters: Filters; boardSeries: number | null }
export interface WTab { id: string; title?: string; st: TabState }
const newTabId = () => 't' + Math.random().toString(36).slice(2, 9)

interface Saved { view: View; sort: SortLevel[]; group: boolean; gran: Gran; filters: Filters; thumb: number }
const DEFAULTS: Saved = {
  view: 'grid',
  sort: [{ f: 'taken', d: 'asc' }, { f: 'name', d: 'asc' }],
  group: true, gran: 'day', filters: { ratings: [], labels: [], label_mode: 'or', lenses: [], cameras: [], subfolders: true }, thumb: 170,
}

function loadSaved(): Saved {
  try {
    const s = { ...DEFAULTS, ...JSON.parse(localStorage.getItem('lpl.v1') || '{}') }
    // older versions stored single-choice filters; drop them in favour of the multi-choice ones
    const { rating, lens, camera, ...f } = s.filters || {}
    s.filters = { ...DEFAULTS.filters, ...f }
    return s
  } catch { return DEFAULTS }
}

export interface Store {
  isMobile: boolean
  tabs: WTab[]; activeTab: string
  switchTab: (id: string) => void
  newTab: (st?: Partial<TabState>, title?: string) => void
  closeTab: (id: string) => void
  renameTab: (id: string, title: string) => void
  moveTab: (id: string, toIndex: number) => void
  liveTab: () => TabState
  view: View; setView: (v: View) => void
  sort: SortLevel[]; setSort: (s: SortLevel[]) => void
  group: boolean; setGroup: (g: boolean) => void
  gran: Gran; setGran: (g: Gran) => void
  filters: Filters; patchFilters: (p: Partial<Filters>) => void; clearFilters: () => void
  thumb: number; setThumb: (n: number) => void
  seed: number; reshuffle: () => void
  items: Photo[]; total: number; loading: boolean; loadMore: () => void; reload: () => void
  /** Which sort and filters `items` were loaded for (queryKey); '' while another tab's are on their way. */
  itemsKey: string
  sel: number[]; setSel: (ids: number[]) => void; clickSelect: (id: number, e?: { ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean }) => void
  selPhotos: Photo[]; selectAll: () => void
  mergePhotos: (ps: Photo[]) => void
  meta: (body: Record<string, unknown>, ids?: number[]) => Promise<void>
  tags: Tag[]; reloadTags: () => void
  facets: { lenses: { v: string; n: number }[]; cameras: { v: string; n: number }[]; hidden_photos?: number }
  /** Hide photos from the library views (or show them again); LPL only, files untouched. */
  hidePhotos: (ids: number[], hidden: boolean) => Promise<void>
  series: SeriesInfo[]; reloadSeries: () => void
  /** Folders of series (a series says which one it is in with folder_id). */
  seriesFolders: SeriesFolder[]
  agents: Agent[]; status: any; isLocal: boolean
  sendIds: number[] | null; setSendIds: (ids: number[] | null) => void
  editInDarktable: (ids: number[]) => Promise<void>
  /** "Edit in darktable" with several devices: the photos waiting for the user to pick a device, and sending them to one. */
  devicePick: number[] | null; closeDevicePick: () => void
  editOn: (ids: number[], agentId: string) => Promise<void>
  toast: (m: string) => void; toastMsg: string
  viewer: number | null; setViewer: (id: number | null) => void
  boardSeries: number | null; setBoardSeries: (id: number | null) => void
  /** Open the Library on a photo's folder, scrolled to it and selected. */
  reveal: (id: number) => void
  revealId: number | null; clearReveal: () => void
  /** Full screen photo view: the photos to page through and where to start. */
  fullscreen: { list: Photo[]; index: number } | null; setFullscreen: (f: { list: Photo[]; index: number } | null) => void
}

const Ctx = createContext<Store | null>(null)
export const useStore = () => useContext(Ctx) as Store

function useMedia(q: string) {
  const [m, setM] = useState(() => window.matchMedia(q).matches)
  useEffect(() => {
    const mq = window.matchMedia(q)
    const h = () => setM(mq.matches)
    mq.addEventListener('change', h)
    return () => mq.removeEventListener('change', h)
  }, [q])
  return m
}

const PAGE = 300

/** The photos a grid or list shows, as one text: same sort, filters and shuffle, same photos in the same order. */
export const queryKey = (sort: SortLevel[], filters: Filters, seed: number) => JSON.stringify([sort, filters, seed])
/** Where each tab's grid and list were scrolled (see useScrollResume in Library.tsx): for which photos (q) and
 *  layout, how far down, and how many photos were loaded by then, so coming back loads as far again. */
export const libScroll = new Map<string, { q: string; lay: string; top: number; n: number }>()

export function StoreProvider({ children }: { children: React.ReactNode }) {
  const saved = useMemo(loadSaved, [])
  const isMobile = useMedia('(max-width: 760px)')
  const [view, setView] = useState<View>(saved.view)
  const [sort, setSort] = useState<SortLevel[]>(saved.sort)
  const [group, setGroup] = useState(saved.group)
  const [gran, setGran] = useState<Gran>(saved.gran)
  const [filters, setFilters] = useState<Filters>(saved.filters)
  const [thumb, setThumb] = useState(saved.thumb)
  const [seed, setSeed] = useState(1)
  const [items, setItems] = useState<Photo[]>([])
  const [total, setTotal] = useState(0)
  const [itemsKey, setItemsKey] = useState('')
  const [loading, setLoading] = useState(false)
  const [sel, setSel] = useState<number[]>([])
  const [cache, setCache] = useState<Map<number, Photo>>(new Map())
  const [tags, setTags] = useState<Tag[]>([])
  const [facets, setFacets] = useState<Store['facets']>({ lenses: [], cameras: [] })
  const [series, setSeries] = useState<SeriesInfo[]>([])
  const [seriesFolders, setSeriesFolders] = useState<SeriesFolder[]>([])
  const [agents, setAgents] = useState<Agent[]>([])
  const [status, setStatus] = useState<any>(null)
  const [toastMsg, setToastMsg] = useState('')
  const [viewer, setViewer] = useState<number | null>(null)
  const [boardSeries, setBoardSeries] = useState<number | null>(null)
  const [sendIds, setSendIds] = useState<number[] | null>(null)
  const [devicePick, setDevicePick] = useState<number[] | null>(null)
  const [revealId, setRevealId] = useState<number | null>(null)
  const [fullscreen, setFullscreen] = useState<Store['fullscreen']>(null)
  const [revealTick, setRevealTick] = useState(0)
  const revealRef = useRef<number | null>(null)
  const reqId = useRef(0)
  const anchor = useRef<number | null>(null)
  const toastTimer = useRef<number>()

  useEffect(() => {
    localStorage.setItem('lpl.v1', JSON.stringify({ view, sort, group, gran, filters, thumb }))
  }, [view, sort, group, gran, filters, thumb])

  const toast = useCallback((m: string) => {
    setToastMsg(m)
    window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToastMsg(''), 3200)
  }, [])

  const reload = useCallback(() => {
    const id = ++reqId.current
    setLoading(true)
    const want = revealRef.current
    const key = queryKey(sort, filters, seed)
    ;(async () => {
      let need = PAGE
      for (const k of libScroll.values()) if (k.q === key) need = Math.max(need, k.n)  // as far as it was scrolled
      if (want != null) {  // load far enough to include the photo we're going to
        const r = await api.position(sort, filters, seed, want)
        if (r.index >= 0) need = Math.max(need, r.index + 60)
        else toast("That photo isn't in this view")
      }
      let got: Photo[] = [], total = 0
      while (got.length < need) {
        const r = await api.query(sort, filters, got.length, Math.min(2000, need - got.length), seed)
        if (id !== reqId.current) return
        got = got.concat(r.items); total = r.total
        if (!r.items.length || got.length >= total) break
      }
      setItems(got); setTotal(total); setItemsKey(key)
      if (want != null && revealRef.current === want) { revealRef.current = null; setRevealId(want) }
    })().catch(e => toast(String(e.message || e))).finally(() => { if (id === reqId.current) setLoading(false) })
  }, [sort, filters, seed, toast, revealTick])

  useEffect(() => { const t = window.setTimeout(reload, 120); return () => window.clearTimeout(t) }, [reload])

  const loadMore = useCallback(() => {
    if (loading || items.length >= total) return
    const id = reqId.current
    setLoading(true)
    api.query(sort, filters, items.length, PAGE, seed).then(r => {
      if (id !== reqId.current) return
      setItems(prev => [...prev, ...r.items.filter(p => !prev.some(q => q.id === p.id))])
      setTotal(r.total)
    }).finally(() => setLoading(false))
  }, [loading, items.length, total, sort, filters, seed])

  const reloadTags = useCallback(() => { api.tags().then(r => setTags(r.tags)).catch(() => {}) }, [])
  const reloadSeries = useCallback(() => { api.series().then(r => { setSeries(r.series); setSeriesFolders(r.folders || []) }).catch(() => {}) }, [])
  useEffect(() => {
    reloadTags(); reloadSeries()
    api.facets().then(setFacets).catch(() => {})
  }, [reloadTags, reloadSeries])

  useEffect(() => {
    let alive = true
    // received_at: when the answer arrived, so the server's clock can be followed between answers (serverNow)
    const tick = () => api.status().then(s => { if (alive) { setStatus({ ...s, received_at: Date.now() / 1000 }); setAgents(s.agents) } }).catch(() => {})
    tick()
    const t = window.setInterval(tick, 4000)
    return () => { alive = false; window.clearInterval(t) }
  }, [])

  const mergePhotos = useCallback((ps: Photo[]) => {
    if (!ps.length) return
    const by = new Map(ps.map(p => [p.id, p]))
    setItems(prev => prev.map(p => (by.has(p.id) ? { ...p, ...by.get(p.id)! } : p)))
    setCache(prev => { const n = new Map(prev); ps.forEach(p => n.set(p.id, { ...(n.get(p.id) || {}), ...p })); return n })
  }, [])

  // keep the selection's photo data available even when they scroll out / are filtered away
  useEffect(() => {
    const known = new Set([...items.map(p => p.id), ...cache.keys()])
    const missing = sel.filter(id => !known.has(id))
    if (missing.length) api.get(missing.slice(0, 2000)).then(r => mergePhotos(r.items)).catch(() => {})
  }, [sel, items, cache, mergePhotos])

  const selPhotos = useMemo(() => {
    const by = new Map(items.map(p => [p.id, p]))
    return sel.map(id => by.get(id) || cache.get(id)).filter(Boolean) as Photo[]
  }, [sel, items, cache])

  const clickSelect = useCallback((id: number, e?: { ctrlKey?: boolean; metaKey?: boolean; shiftKey?: boolean }) => {
    if (e?.shiftKey && anchor.current != null) {
      const a = items.findIndex(p => p.id === anchor.current)
      const b = items.findIndex(p => p.id === id)
      if (a >= 0 && b >= 0) {
        const range = items.slice(Math.min(a, b), Math.max(a, b) + 1).map(p => p.id)
        setSel(prev => Array.from(new Set([...prev, ...range])))
        return
      }
    }
    anchor.current = id
    if (e?.ctrlKey || e?.metaKey) setSel(prev => (prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]))
    else setSel([id])
  }, [items])

  const selectAll = useCallback(() => {
    api.ids(sort, filters, seed).then(r => setSel(r.ids)).catch(() => {})
  }, [sort, filters, seed])

  const meta = useCallback(async (body: Record<string, unknown>, ids?: number[]) => {
    const target = ids || sel
    if (!target.length) return
    try {
      const r = await api.meta({ ids: target, ...body })
      mergePhotos(r.items)
      if (r.errors.length) toast(`Saved, but ${r.errors.length} XMP file(s) could not be written: ${r.errors[0]}`)
      if ('attach' in body || 'detach' in body) reloadTags()
    } catch (e: any) { toast(e.message || String(e)) }
  }, [sel, mergePhotos, toast, reloadTags])

  const editInDarktable = useCallback(async (ids: number[]) => {
    if (!ids.length) return
    try {
      if (status?.mode === 'local') {
        const r = await api.job('edit', { ids })
        toast(r.message || 'Opening in darktable…')
        return
      }
      if (needsDeviceChoice(agents)) { setDevicePick(ids); return }  // which device: DevicePicker, then editOn
      const online = agents.filter(a => a.online)
      const r = await api.job('edit', { ids }, online.length === 1 ? online[0].id : undefined)
      const ag = agents.find(a => a.id === r.agent_id)
      toast(ag && ag.online ? `Sending ${ids.length} photo${ids.length > 1 ? 's' : ''} to darktable on ${agentLabel(ag, agents)}…`
        : 'Queued. It opens in darktable as soon as your PC agent is online.')
    } catch (e: any) { toast(e.message || String(e)) }
  }, [agents, toast, status])

  const editOn = useCallback(async (ids: number[], agentId: string) => {
    setDevicePick(null)
    try {
      localStorage.setItem('lpl.editDevice', agentId)
      await api.job('edit', { ids }, agentId)
      const ag = agents.find(a => a.id === agentId)
      const name = ag ? agentLabel(ag, agents) : agentId
      toast(ag && ag.online ? `Sending ${ids.length} photo${ids.length > 1 ? 's' : ''} to darktable on ${name}…`
        : `Queued. It opens in darktable as soon as ${name} is online.`)
    } catch (e: any) { toast(e.message || String(e)) }
  }, [agents, toast])

  const reveal = useCallback((pid: number) => {
    api.photo(pid).then(p => {
      revealRef.current = pid
      setFilters(f => ({ ...DEFAULTS.filters, show_hidden: f.show_hidden, folder: p.folder }))
      setSel([pid])
      setView(v => (v === 'list' ? 'list' : 'grid'))
      setRevealTick(t => t + 1)
      toast(`Showing ${p.filename} in ${p.folder || 'its folder'}`)
    }).catch(e => toast(e.message))
  }, [toast])

  const patchFilters = useCallback((p: Partial<Filters>) => setFilters(f => ({ ...f, ...p })), [])
  const clearFilters = useCallback(() => setFilters(DEFAULTS.filters), [])

  // ---- tabs
  const liveTab = (): TabState => ({ view, sort, group, gran, filters, boardSeries })
  const [tabs, setTabs] = useState<WTab[]>(() => {
    try {
      const t = JSON.parse(localStorage.getItem('lpl.tabs') || 'null')
      if (t && Array.isArray(t.tabs) && t.tabs.length) return t.tabs
    } catch { /* ignore */ }
    return [{ id: newTabId(), st: { view: saved.view, sort: saved.sort, group: saved.group, gran: saved.gran, filters: saved.filters, boardSeries: null } }]
  })
  const [activeTab, setActiveTab] = useState<string>(() => {
    try { const t = JSON.parse(localStorage.getItem('lpl.tabs') || 'null'); if (t?.active && t.tabs?.some((x: WTab) => x.id === t.active)) return t.active } catch { /* ignore */ }
    return tabs[0].id
  })
  // the open tab's state lives in the normal variables; keep its copy in the list current and remembered
  useEffect(() => {
    const live = liveTab()
    const all = tabs.map(t => (t.id === activeTab ? { ...t, st: live } : t))
    try { localStorage.setItem('lpl.tabs', JSON.stringify({ tabs: all, active: activeTab })) } catch { /* ignore */ }
  }, [tabs, activeTab, view, sort, group, gran, filters, boardSeries])
  const applyTab = (st: TabState) => {
    setView(st.view); setSort(st.sort); setGroup(st.group); setGran(st.gran)
    setFilters({ ...DEFAULTS.filters, ...st.filters }); setBoardSeries(st.boardSeries ?? null); setSel([])
    setItemsKey('')  // the photos on screen are the other tab's until this one's arrive
  }
  const switchTab = (id: string) => {
    if (id === activeTab) return
    const target = tabs.find(t => t.id === id)
    if (!target) return
    const live = liveTab()
    setTabs(ts => ts.map(t => (t.id === activeTab ? { ...t, st: live } : t)))
    setActiveTab(id); applyTab(target.st)
  }
  const newTab = (st: Partial<TabState> = {}, title?: string) => {
    const live = liveTab()
    const tab: WTab = { id: newTabId(), title, st: { view: 'grid', sort: DEFAULTS.sort, group: true, gran: 'day', filters: DEFAULTS.filters, boardSeries: null, ...st } }
    setTabs(ts => { const i = ts.findIndex(t => t.id === activeTab); const n = ts.map(t => (t.id === activeTab ? { ...t, st: live } : t)); n.splice(i + 1, 0, tab); return n })
    setActiveTab(tab.id); applyTab(tab.st)
  }
  const closeTab = (id: string) => {
    if (tabs.length <= 1) return
    const i = tabs.findIndex(t => t.id === id)
    const rest = tabs.filter(t => t.id !== id)
    try { localStorage.removeItem('lpl.folder.' + id) } catch { /* ignore */ }
    for (const k of libScroll.keys()) if (k.startsWith(id + ' ')) libScroll.delete(k)
    if (id === activeTab) { const next = rest[Math.min(i, rest.length - 1)]; setTabs(rest); setActiveTab(next.id); applyTab(next.st) }
    else setTabs(rest)
  }
  const renameTab = (id: string, title: string) => setTabs(ts => ts.map(t => (t.id === id ? { ...t, title: title.trim() || undefined } : t)))
  const moveTab = (id: string, to: number) => setTabs(ts => { const n = ts.filter(t => t.id !== id); const t = ts.find(x => x.id === id)!; n.splice(Math.max(0, Math.min(to, n.length)), 0, t); return n })
  useEffect(() => { const t = tabs.find(x => x.id === activeTab); if (t) applyTab(t.st) }, [])  // reopen where you were

  const hidePhotos = async (ids: number[], hidden: boolean) => {
    if (!ids.length) return
    try {
      const r = await api.hide(ids, hidden)
      setFacets(f => ({ ...f, hidden_photos: r.hidden_photos }))
      setCache(prev => { const n = new Map(prev); ids.forEach(id => { const p = n.get(id) || items.find(x => x.id === id); if (p) n.set(id, { ...p, hidden: hidden ? 1 : 0 }) }); return n })
      const k = keysOf('hide')[0]
      if (hidden) toast(`${ids.length} photo${ids.length > 1 ? 's' : ''} hidden${filters.show_hidden ? '' : ' from the library'}. ${k ? k + ' again brings them back; ' : ''}${view === 'board' ? '“show hidden” next to the board name' : '“Show hidden” in Filters'} shows them.`)
      else toast(`${ids.length} photo${ids.length > 1 ? 's' : ''} shown again`)
      reload()
    } catch (e: any) { toast(e.message || String(e)) }
  }

  const value: Store = {
    isMobile, tabs, activeTab, switchTab, newTab, closeTab, renameTab, moveTab, liveTab, view, setView, sort, setSort, group, setGroup, gran, setGran, filters, patchFilters, clearFilters,
    thumb, setThumb, seed, reshuffle: () => setSeed(s => s + 1), items, total, loading, loadMore, reload, itemsKey,
    sel, setSel, clickSelect, selPhotos, selectAll, mergePhotos, meta, tags, reloadTags, facets, hidePhotos, series, reloadSeries, seriesFolders,
    agents, status, isLocal: status?.mode === 'local', sendIds, setSendIds, editInDarktable, devicePick, closeDevicePick: () => setDevicePick(null), editOn, toast, toastMsg, viewer, setViewer, boardSeries, setBoardSeries,
    reveal, revealId, clearReveal: () => setRevealId(null), fullscreen, setFullscreen,
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
