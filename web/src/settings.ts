// Options the user can change in Settings. Kept on the server (every PC and the phone get them) and cached
// in the browser so they apply at once. Keyboard shortcuts live in shortcuts.ts, shown in the same window.
import { useEffect, useState } from 'react'
import { api } from './api'

export interface Settings {
  /** which mouse button drags the board around (besides Space + drag) */
  boardPan: 'right' | 'middle' | 'both'
  /** plain mouse wheel on the board: scroll (Ctrl+wheel zooms) or zoom (Shift+wheel scrolls) */
  boardWheel: 'scroll' | 'zoom'
  /** one finger dragging empty board space on a touch screen */
  boardTouch: 'pan' | 'select'
  /** double-click (double-tap) on a photo on the board */
  boardPhotoDouble: 'darktable' | 'fullscreen' | 'large'
  /** reading-order numbers on the photos */
  boardNumbers: boolean
  /** drop things on a 20 px grid when you let go */
  boardSnap: boolean
  /** colour of a new note */
  boardNoteColor: string
  /** colour of a new frame ('' = none) */
  boardFrameColor: string
  /** the zoom a board opens at (100 = 100%); the phone uses its own */
  boardZoom: number
  boardZoomPhone: number
  /** the photo strip under the board: shown, how it's sorted */
  boardStrip: boolean
  boardStripSort: 'reading' | 'taken' | 'name' | 'rating' | 'label' | 'folder'
  boardStripDesc: boolean
  /** photos sent to a device are opened in darktable when they have arrived (the server passes it on with the job) */
  devicesAutoOpen: boolean
  /** the top bar's devices indicator shows the time left while photos are sent */
  devicesEtaInBar: boolean
  /** an agent installed again on a computer LPL knows is merged into the old entry (server/lpl/identity.py) */
  devicesAutoMerge: boolean
  /** R18: where received exports are stored: relative to the library, or a full path; empty = the "exports" folder in the library */
  exportsFolder: string
  /** R11: the same style, preset or layout changed on a PC and elsewhere: ask (Devices page) or the newest wins */
  sharedPolicy: 'ask' | 'newest'
}

export const DEFAULT_SETTINGS: Settings = {
  boardPan: 'both', boardWheel: 'scroll', boardTouch: 'pan', boardPhotoDouble: 'darktable',
  boardNumbers: true, boardSnap: false, boardNoteColor: '#f5d76e', boardFrameColor: '', boardZoom: 100, boardZoomPhone: 50,
  boardStrip: true, boardStripSort: 'reading', boardStripDesc: false,
  devicesAutoOpen: true, devicesEtaInBar: true, devicesAutoMerge: true, exportsFolder: '', sharedPolicy: 'ask',
}

let current: Settings = (() => {
  try { return { ...DEFAULT_SETTINGS, ...JSON.parse(localStorage.getItem('lpl.settings') || '{}') } } catch { return DEFAULT_SETTINGS }
})()
const listeners = new Set<() => void>()
const changed = () => { try { localStorage.setItem('lpl.settings', JSON.stringify(current)) } catch { /* ignore */ } listeners.forEach(f => f()) }

api.prefGet('settings').then(r => {
  if (r.value && typeof r.value === 'object') { current = { ...DEFAULT_SETTINGS, ...(r.value as Partial<Settings>) }; changed() }
}).catch(() => {})

export const getSettings = () => current
export function setSetting<K extends keyof Settings>(k: K, v: Settings[K]) {
  current = { ...current, [k]: v }
  changed()
  api.prefSet('settings', current).catch(() => {})
}
export function resetSettings(keys?: (keyof Settings)[]) {
  current = keys ? { ...current, ...Object.fromEntries(keys.map(k => [k, DEFAULT_SETTINGS[k]])) } : DEFAULT_SETTINGS
  changed()
  api.prefSet('settings', current).catch(() => {})
}

/** The current settings; the component re-renders when they change. */
export function useSettings(): Settings {
  const [, setV] = useState(0)
  useEffect(() => { const f = () => setV(x => x + 1); listeners.add(f); return () => { listeners.delete(f) } }, [])
  return current
}
