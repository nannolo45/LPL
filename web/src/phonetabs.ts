// SPDX-License-Identifier: GPL-3.0-or-later
// The phone layout shows the same tabs as the desktop; this says where on the phone a tab's view is shown.
import type { View } from './store'

export type PhoneTab = 'lib' | 'folders' | 'series' | 'pc'

/** Where the phone shows a tab with this view: the bottom-bar tab, the board to open, and a note when the
 *  phone cannot show that view (it then shows the grid; the tab itself keeps its view). */
export function phoneSpot(view: View, boardSeries: number | null): { tab: PhoneTab; board: number | null; note?: string } {
  switch (view) {
    case 'grid': return { tab: 'lib', board: null }
    case 'folders': return { tab: 'folders', board: null }
    case 'work': return { tab: 'pc', board: null }
    case 'board': return boardSeries != null ? { tab: 'series', board: boardSeries } : { tab: 'series', board: null }
    case 'list': return { tab: 'lib', board: null, note: 'List: shown as the grid on the phone' }
    case 'timeline': return { tab: 'lib', board: null, note: 'Timeline: shown as the grid on the phone' }
    default: return { tab: 'lib', board: null, note: 'Send to NAS: shown as the grid on the phone' }
  }
}
