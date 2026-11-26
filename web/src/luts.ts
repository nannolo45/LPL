// SPDX-License-Identifier: GPL-3.0-or-later
// R19 (docs/luts-r19.md): darktable's LUT 3D files carried through LPL. What the Devices page shows: each file, who
// sent it last, which devices have it (a dot per device), and the devices' LUT folders.

export type LutState = 'ok' | 'different' | 'removed' | 'conflict' | 'unusable'

export interface LutDeviceHas { agent_id: string; name: string; os: string | null; sha256: string | null; state: LutState }
export interface LutFile {
  path: string; size: number; sha256: string; seq: number; changed_at: number
  device_id: string | null; device_name: string | null; deleted: boolean; history: number; devices: LutDeviceHas[]
}
export interface LutDevice {
  agent_id: string; name: string; os: string | null; folder: string | null; reason: string | null
  files: number; updated: number | null; last_seen: number | null
}
export interface LutOverview { files: LutFile[]; devices: LutDevice[]; bytes_current: number; bytes_stored: number; max_file_bytes: number }

export type LutDot = 'newest' | 'other' | 'removed' | 'conflict' | 'unusable' | 'absent'

export const LUT_DOTS: Record<LutDot, [string, string]> = {
  newest: ['●', 'has this file'], other: ['!', 'has a different file at this path (kept as it is)'],
  removed: ['–', 'removed there'], conflict: ['!', 'changed there and in LPL: to be settled on that device'],
  unusable: ['○', 'has it, cannot use it (.gmz needs darktable with G\'MIC)'], absent: ['–', 'not there yet'],
}

/** The dot of one device for one file. */
export function lutDot(f: Pick<LutFile, 'sha256' | 'devices'>, agentId: string): LutDot {
  const d = f.devices.find(x => x.agent_id === agentId)
  if (!d) return 'absent'
  if (d.state === 'removed') return 'removed'
  if (d.state === 'conflict') return 'conflict'
  if (d.state === 'unusable') return 'unusable'
  if (d.state === 'different' || (d.sha256 && d.sha256 !== f.sha256)) return 'other'
  return 'newest'
}

/** "1.2 MB", "840 kB", "12 bytes" */
export function fmtBytes(n: number): string {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + ' MB'
  if (n >= 1e3) return Math.round(n / 1e3) + ' kB'
  return n + (n === 1 ? ' byte' : ' bytes')
}

/** The line under a device's name: its folder, or why it offers none. */
export function folderLine(d: Pick<LutDevice, 'folder' | 'reason' | 'files' | 'os'>): string {
  if (!d.folder) return d.reason || 'no LUT folder'
  const n = `${d.files} file${d.files === 1 ? '' : 's'}`
  return d.os === 'android' ? `${n} on the phone` : `${n} · ${d.folder}`
}

/** The heading's count: files LPL has now (removed ones are not counted). */
export function lutCount(o: Pick<LutOverview, 'files' | 'bytes_current' | 'bytes_stored'>): string {
  const live = o.files.filter(f => !f.deleted)
  const hist = o.bytes_stored > o.bytes_current ? ` · ${fmtBytes(o.bytes_stored)} stored with earlier versions` : ''
  return `${live.length} file${live.length === 1 ? '' : 's'} · ${fmtBytes(o.bytes_current)}${hist}`
}

/** What "Remove from LPL" asks first. */
export const removeQuestion = (path: string) =>
  `Remove ${path} from LightPhotoLibrary? No device loses its copy, and LPL keeps the file's earlier versions. A device that changes the file sends it again.`
