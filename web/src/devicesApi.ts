// SPDX-License-Identifier: GPL-3.0-or-later
// What the Devices page and the top bar's indicator ask of the server (server/lpl/transfers.py).
import { call } from './api'

const dev = (id: string) => `/api/agents/${encodeURIComponent(id)}`
export const devicesApi = {
  /** Stop sending photos to this device: what arrived stays, the rest can be resumed. */
  stop: (id: string) => call<{ ok: boolean; stopped: number }>('POST', `${dev(id)}/stop`),
  /** Send what a stop left over. */
  resume: (id: string) => call<{ ok: boolean; id: number | null; photos: number }>('POST', `${dev(id)}/resume`),
  /** Take single photos out of what is still to be sent. */
  dontSend: (id: string, ids: number[]) => call<{ ok: boolean; photos: number }>('POST', `${dev(id)}/dont-send`, { ids }),
  /** Close the "Stopped" note. */
  dismiss: (id: string) => call<{ ok: boolean }>('POST', `${dev(id)}/dismiss`),
  /** Take back a job no device has taken yet. */
  cancelJob: (job: number) => call<{ ok: boolean }>('POST', `/api/jobs/${job}/cancel`),
  /** Remember folded / unfolded per device (null forgets the choice). */
  fold: (folds: Record<string, boolean | null>) => call<{ folds: Record<string, boolean> }>('PUT', '/api/devices/fold', { folds }),
  /** Send photos it could not copy again, as a new batch. */
  retry: (id: string, ids: number[]) => call<{ ok: boolean; id: number; photos: number }>('POST', `${dev(id)}/retry`, { ids }),
  /** "Clear from queue": photos it could not copy leave the page (what a failed copy left on it is deleted there). */
  clear: (id: string, ids: number[]) => call<{ ok: boolean; cleared: number; on_device: number }>('POST', `${dev(id)}/clear`, { ids }),
  /** Rename a device; the device is told, so it keeps the name. */
  rename: (id: string, name: string) => call<{ ok: boolean; name: string }>('PUT', `${dev(id)}/name`, { name }),
  /** Merge an old entry of a computer into its current one (only an entry that stopped reporting). */
  merge: (id: string, into: string) => call<{ ok: boolean }>('POST', `${dev(id)}/merge`, { into }),
  /** Take an offline device off the list (it comes back if it connects again). */
  forget: (id: string) => call<{ ok: boolean }>('DELETE', dev(id)),
}

/** Ask the store to fetch the devices' state now, instead of at its next round (after Stop, Resume, a fold…). */
export const refreshStatus = () => { if (typeof window !== 'undefined') window.dispatchEvent(new Event('lpl-refresh-status')) }
