// SPDX-License-Identifier: GPL-3.0-or-later
// The phone's Back button and back gesture (and the LPL shell's Back) step out of what is open: each open
// thing (sheet, dialog, photo page, board, a folder opened from Folders, ...) is a "layer" with its own
// history entry. Back pops the entry and closes the top layer; closing a layer in the app takes its entry
// off again. Only standard History API calls and popstate, so it works the same in Chrome, Firefox and web views.
import { useEffect, useRef } from 'react'

type Layer = { close: () => void }
const stack: Layer[] = []
let pendingBack = 0

/** How many layers the history entry we arrived at stands for. The full-screen photo keeps its own entry
 *  ({lplFull}) and handles it itself; it sits on top of the layers below it. */
export function depthOf(state: any, open: number): number {
  if (state && typeof state.lplDepth === 'number') return state.lplDepth
  if (state && state.lplFull) return open
  return 0
}

/** Which layers a Back to `depth` closes, top first (as indexes into the stack). */
export const layersToClose = (open: number, depth: number) => Array.from({ length: Math.max(0, open - depth) }, (_, k) => open - 1 - k)

if (typeof window !== 'undefined') {
  window.addEventListener('popstate', e => {
    const depth = depthOf(e.state, stack.length)
    for (const k of layersToClose(stack.length, depth)) stack.splice(k, 1)[0].close()
  })
  // a reload forgets what was open but not the history entries: go back to the first one, so Back doesn't
  // have to step through entries that no longer open anything
  const d = depthOf(history.state, 0)
  if (d > 0) history.go(-d)
}

/** While `open`, Back closes this layer by calling `close`. A new `key` (e.g. the folder you are in) is a new
 *  layer in place of the old one, so Back steps out one level at a time without piling up entries. */
export function useBackLayer(open: boolean, close: () => void, key?: string | number) {
  const closeRef = useRef(close)
  closeRef.current = close
  useEffect(() => {
    if (!open) return
    const layer: Layer = { close: () => closeRef.current() }
    stack.push(layer)
    history.pushState({ ...(history.state || {}), lplFull: undefined, lplDepth: stack.length }, '')
    return () => {
      const k = stack.indexOf(layer)
      if (k < 0) return  // closed by Back: its entry is gone already
      stack.splice(k, 1)
      // closed in the app: take its entry off the history (several at once in one step)
      if (pendingBack++ === 0) queueMicrotask(() => { const n = pendingBack; pendingBack = 0; history.go(-n) })
    }
  }, [open, key])
}
