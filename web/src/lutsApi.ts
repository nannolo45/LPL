// SPDX-License-Identifier: GPL-3.0-or-later
// What the Devices page asks of the server about LUT files (R19, server/lpl/luts.py).
import { call } from './api'
import type { LutOverview } from './luts'

export const lutsApi = {
  overview: () => call<LutOverview>('GET', '/api/luts/overview'),
  remove: (path: string) => call<unknown>('DELETE', `/api/luts/file?path=${encodeURIComponent(path)}`),
}
