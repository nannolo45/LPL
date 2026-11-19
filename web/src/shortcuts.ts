// Keyboard shortcuts: one list of actions with default keys; the user can change them in the Shortcuts dialog.
// Changes are kept on the server (so every PC gets them) and cached in the browser so they apply at once.
import { useEffect, useState } from 'react'
import { api } from './api'

export type Scope = 'photos' | 'board' | 'viewer'
export interface Action { id: string; label: string; scope: Scope; keys: string[] }

export const ACTIONS: Action[] = [
  { id: 'rate0', label: 'Rating: none', scope: 'photos', keys: ['0'] },
  { id: 'rate1', label: 'Rating: ★1', scope: 'photos', keys: ['1'] },
  { id: 'rate2', label: 'Rating: ★2', scope: 'photos', keys: ['2'] },
  { id: 'rate3', label: 'Rating: ★3', scope: 'photos', keys: ['3'] },
  { id: 'rate4', label: 'Rating: ★4', scope: 'photos', keys: ['4'] },
  { id: 'rate5', label: 'Rating: ★5', scope: 'photos', keys: ['5'] },
  { id: 'reject', label: 'Reject', scope: 'photos', keys: ['R'] },
  { id: 'label0', label: 'Toggle red label', scope: 'photos', keys: ['F1'] },
  { id: 'label1', label: 'Toggle yellow label', scope: 'photos', keys: ['F2'] },
  { id: 'label2', label: 'Toggle green label', scope: 'photos', keys: ['F3'] },
  { id: 'label3', label: 'Toggle blue label', scope: 'photos', keys: ['F4'] },
  { id: 'label4', label: 'Toggle purple label', scope: 'photos', keys: ['F5'] },
  { id: 'edit', label: 'Edit in darktable', scope: 'photos', keys: ['E'] },
  { id: 'view', label: 'Open large', scope: 'photos', keys: ['Enter'] },
  { id: 'selectAll', label: 'Select all', scope: 'photos', keys: ['Ctrl+A'] },
  { id: 'deselect', label: 'Clear selection', scope: 'photos', keys: ['Escape'] },
  { id: 'boardDelete', label: 'Remove selected from the board (photos, notes, arrows)', scope: 'board', keys: ['Delete', 'Backspace'] },
  { id: 'toolSelect', label: 'Tool: select and move', scope: 'board', keys: ['V'] },
  { id: 'toolNote', label: 'Tool: note', scope: 'board', keys: ['N'] },
  { id: 'toolArrow', label: 'Tool: arrow', scope: 'board', keys: ['A'] },
  { id: 'pan', label: 'Move around (hold and drag)', scope: 'board', keys: ['Space'] },
  { id: 'zoomIn', label: 'Zoom in', scope: 'board', keys: ['Ctrl+='] },
  { id: 'zoomOut', label: 'Zoom out', scope: 'board', keys: ['Ctrl+-'] },
  { id: 'zoomReset', label: 'Zoom to 100%', scope: 'board', keys: ['Ctrl+0'] },
  { id: 'next', label: 'Next photo', scope: 'viewer', keys: ['ArrowRight', 'Space'] },
  { id: 'prev', label: 'Previous photo', scope: 'viewer', keys: ['ArrowLeft'] },
  { id: 'close', label: 'Close', scope: 'viewer', keys: ['Escape'] },
]
export const SCOPES: [Scope, string][] = [['photos', 'Photos (Grid, List, Board)'], ['board', 'Board'], ['viewer', 'Large view']]

const NAMES: Record<string, string> = { ' ': 'Space', Esc: 'Escape', Del: 'Delete', '+': '=' }

// "Ctrl+Shift+D", "F1", "Space", "Delete" … Shift only counts with letters and named keys (Shift+1 types "!" anyway).
export function comboOf(e: KeyboardEvent | React.KeyboardEvent): string {
  let k = NAMES[e.key] ?? e.key
  if (['Control', 'Shift', 'Alt', 'Meta', 'AltGraph'].includes(k)) return ''
  if (k.length === 1) k = k.toUpperCase()
  const symbol = k.length === 1 && !/[A-Z0-9]/.test(k)
  const mods = [(e.ctrlKey || e.metaKey) && 'Ctrl', e.altKey && 'Alt', e.shiftKey && !symbol && 'Shift'].filter(Boolean)
  return [...mods, k].join('+')
}

let custom: Record<string, string[]> = (() => { try { return JSON.parse(localStorage.getItem('lpl.keys') || '{}') } catch { return {} } })()
const listeners = new Set<() => void>()
const changed = () => { try { localStorage.setItem('lpl.keys', JSON.stringify(custom)) } catch { /* ignore */ } listeners.forEach(f => f()) }

api.prefGet('shortcuts').then(r => { if (r.value && typeof r.value === 'object') { custom = r.value as Record<string, string[]>; changed() } }).catch(() => {})

export const keysOf = (id: string): string[] => custom[id] ?? ACTIONS.find(a => a.id === id)?.keys ?? []
export const isCustom = (id: string) => id in custom

/** Which action of the given scopes this key press means (first match), or null. */
export function actionFor(e: KeyboardEvent, scopes: Scope[]): string | null {
  const c = comboOf(e)
  if (!c) return null
  for (const a of ACTIONS) if (scopes.includes(a.scope) && keysOf(a.id).includes(c)) return a.id
  return null
}

export function setKeys(id: string, keys: string[] | null) {
  if (keys == null) delete custom[id]
  else custom = { ...custom, [id]: keys }
  changed()
  api.prefSet('shortcuts', custom).catch(() => {})
}
export function resetAll() { custom = {}; changed(); api.prefSet('shortcuts', {}).catch(() => {}) }

/** Re-render when shortcuts change. */
export function useShortcutsVersion() {
  const [v, setV] = useState(0)
  useEffect(() => { const f = () => setV(x => x + 1); listeners.add(f); return () => { listeners.delete(f) } }, [])
  return v
}

/** True while typing somewhere: shortcuts stay out of the way. */
export const typingIn = (t: EventTarget | null) => {
  const el = t as HTMLElement | null
  return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable)
}
