// SPDX-License-Identifier: GPL-3.0-or-later
// Exports of a photo (R18, docs/exports-r18.md): the line the photo panel shows for each. Plain functions.
import type { ExportInfo } from './api'
import { fmtBytes } from './transfer.ts'
import { fmtWhen } from './edits.ts'

/** "out.jpg · 2.1 MB · Phone · 23 Nov 14:05" (the version when it is not the original: "v2"). */
export function exportLine(e: Pick<ExportInfo, 'size' | 'device' | 'agent' | 'created' | 'version'>, now: number = Date.now() / 1000): string {
  return [fmtBytes(e.size), e.version ? `v${e.version}` : '', e.device || e.agent || '', fmtWhen(e.created, now)].filter(Boolean).join(' · ')
}

export const deleteExportText = (e: Pick<ExportInfo, 'name'>) => ({
  title: `Delete ${e.name}?`,
  text: 'The file is moved to the backup folder on the NAS, not erased.',
})
