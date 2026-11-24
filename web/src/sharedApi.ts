// SPDX-License-Identifier: GPL-3.0-or-later
// What the Devices page asks of the server about shared styles, presets and module layouts (R11, server/lpl/shared.py).
import { call } from './api'
import type { Choice, SharedItem, SharedOverview } from './shared'

export const sharedApi = {
  overview: () => call<SharedOverview>('GET', '/api/shared/devices'),
  resolve: (id: number, agent: string, choice: Choice) => call<{ items: SharedItem[] }>('POST', `/api/shared/${id}/resolve`, { agent, choice }),
  update: (body: { agent: string; ids: number[]; delete: number[]; keep: number[] }) => call<{ id: number }>('POST', '/api/shared/update', body),
}
