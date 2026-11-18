import type { Photo, SortLevel } from './api'

export type FieldType = 'date' | 'num' | 'str' | 'none'
export interface FieldDef { v: string; l: string; t: FieldType; dt: boolean }

// darktable's own sort criteria first, then the extras LightPhotoLibrary adds
export const FIELDS: FieldDef[] = [
  { v: 'taken', l: 'capture time', t: 'date', dt: true },
  { v: 'name', l: 'filename', t: 'str', dt: true },
  { v: 'import', l: 'import time', t: 'date', dt: true },
  { v: 'modified', l: 'last modification time', t: 'date', dt: true },
  { v: 'exported', l: 'last export time', t: 'date', dt: true },
  { v: 'printed', l: 'last print time', t: 'date', dt: true },
  { v: 'visited', l: 'last visit time', t: 'date', dt: true },
  { v: 'rating', l: 'rating', t: 'num', dt: true },
  { v: 'label', l: 'color label', t: 'num', dt: true },
  { v: 'group', l: 'group (folder)', t: 'str', dt: true },
  { v: 'path', l: 'full path', t: 'str', dt: true },
  { v: 'custom', l: 'custom sort', t: 'none', dt: true },
  { v: 'title', l: 'title', t: 'str', dt: true },
  { v: 'description', l: 'description', t: 'str', dt: true },
  { v: 'aspect', l: 'aspect ratio', t: 'num', dt: true },
  { v: 'shuffle', l: 'shuffle', t: 'none', dt: true },
  { v: 'lens', l: 'lens', t: 'str', dt: false },
  { v: 'camera', l: 'camera', t: 'str', dt: false },
  { v: 'focal', l: 'focal length', t: 'num', dt: false },
  { v: 'aperture', l: 'aperture', t: 'num', dt: false },
  { v: 'iso', l: 'ISO', t: 'num', dt: false },
  { v: 'exposure', l: 'exposure time', t: 'num', dt: false },
  { v: 'tag', l: 'first tag', t: 'str', dt: false },
  { v: 'edited', l: 'edited in darktable', t: 'num', dt: false },
]

export const field = (v: string) => FIELDS.find(f => f.v === v) || FIELDS[0]

export const LABELS = [
  { i: 0, name: 'red', c: '#e5484d' },
  { i: 1, name: 'yellow', c: '#f5c542' },
  { i: 2, name: 'green', c: '#46a758' },
  { i: 3, name: 'blue', c: '#3e8ed0' },
  { i: 4, name: 'purple', c: '#9b5fd6' },
]

export const PRESETS: { label: string; sort: SortLevel[] }[] = [
  { label: 'Date › Name', sort: [{ f: 'taken', d: 'asc' }, { f: 'name', d: 'asc' }] },
  { label: 'Date › Lens › Rating', sort: [{ f: 'taken', d: 'asc' }, { f: 'lens', d: 'asc' }, { f: 'rating', d: 'desc' }] },
  { label: 'Folder › Date', sort: [{ f: 'path', d: 'asc' }, { f: 'taken', d: 'asc' }] },
  { label: 'Lens › Rating', sort: [{ f: 'lens', d: 'asc' }, { f: 'rating', d: 'desc' }, { f: 'taken', d: 'asc' }] },
  { label: 'Best first', sort: [{ f: 'rating', d: 'desc' }, { f: 'taken', d: 'desc' }] },
]

export function dirText(f: string, d: string) {
  const t = field(f).t
  if (t === 'date') return d === 'asc' ? 'old → new' : 'new → old'
  if (t === 'num') return d === 'asc' ? 'low → high' : 'high → low'
  if (t === 'none') return d === 'asc' ? 'forward' : 'reverse'
  return d === 'asc' ? 'A → Z' : 'Z → A'
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

const isoFromTs = (ts: number | null) => (ts ? new Date(ts * 1000).toISOString().slice(0, 19) : null)

function dateOf(p: Photo, f: string): string | null {
  switch (f) {
    case 'taken': return p.taken
    case 'import': return isoFromTs(p.import_ts)
    case 'modified': return isoFromTs(p.change_ts)
    case 'exported': return isoFromTs(p.export_ts)
    case 'printed': return isoFromTs(p.print_ts)
    case 'visited': return isoFromTs(p.change_ts || p.import_ts)
  }
  return null
}

export function fmtDay(iso: string) {
  const d = new Date(iso.slice(0, 10) + 'T12:00:00')
  return `${WD[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()].slice(0, 3)} ${d.getFullYear()}`
}
export const fmtMonth = (iso: string) => `${MONTHS[parseInt(iso.slice(5, 7), 10) - 1]} ${iso.slice(0, 4)}`
export const stars = (r: number) => (r < 0 ? 'rejected' : r > 0 ? '★★★★★'.slice(0, r) : '')
export const leaf = (t: string) => t.split('|').pop() || t

export function fmtExposure(e: number | null) {
  if (!e) return ''
  if (e >= 0.3) return `${Math.round(e * 10) / 10}s`
  return `1/${Math.round(1 / e)}`
}

export function groupLabel(p: Photo, f: string, gran: 'day' | 'month' | 'year'): string {
  const fd = field(f)
  if (fd.t === 'date') {
    const v = dateOf(p, f)
    if (!v) return f === 'taken' ? 'no date' : 'never'
    return gran === 'day' ? fmtDay(v) : gran === 'year' ? v.slice(0, 4) : fmtMonth(v)
  }
  switch (f) {
    case 'rating': return p.rating < 0 ? 'rejected' : p.rating ? stars(p.rating) : 'unrated'
    case 'label': return p.labels.length ? LABELS[p.labels[0]].name : 'no label'
    case 'group': case 'path': return p.folder || '(top folder)'
    case 'lens': return p.lens || 'unknown lens'
    case 'camera': return p.camera || 'unknown camera'
    case 'focal': return p.focal ? `${Math.round(p.focal)} mm` : 'unknown'
    case 'aperture': return p.aperture ? `f/${p.aperture}` : 'unknown'
    case 'iso': return p.iso ? `ISO ${p.iso}` : 'unknown'
    case 'exposure': return fmtExposure(p.exposure) || 'unknown'
    case 'tag': { const t = [...p.tags].sort()[0]; return t || 'no tags' }
    case 'edited': return p.edited ? 'edited in darktable' : 'not edited yet'
    case 'name': return p.filename.slice(0, 1).toUpperCase()
    case 'aspect': return aspectLabel(p)
    case 'title': return p.title || 'no title'
    case 'description': return p.description || 'no description'
  }
  return ''
}

function aspectLabel(p: Photo) {
  if (!p.width || !p.height) return 'unknown'
  const rot = [5, 6, 7, 8].includes(p.orientation)
  const r = rot ? p.height / p.width : p.width / p.height
  if (r > 1.05) return 'landscape'
  if (r < 0.95) return 'portrait'
  return 'square'
}

export function captionValue(p: Photo, f: string): string {
  const fd = field(f)
  if (fd.t === 'date') {
    const v = dateOf(p, f)
    if (!v) return f === 'taken' ? 'no date' : `${fd.l}: never`
    const d = new Date(v.slice(0, 10) + 'T12:00:00')
    const s = `${d.getDate()} ${MONTHS[d.getMonth()].slice(0, 3)} ${v.slice(0, 4)}`
    return f === 'taken' ? `${s} ${v.slice(11, 16)}` : `${fd.l.replace(' time', '')} ${s}`
  }
  switch (f) {
    case 'name': return p.filename
    case 'rating': return p.rating < 0 ? 'rejected' : p.rating ? `${p.rating}★` : 'unrated'
    case 'label': return p.labels.length ? p.labels.map(l => LABELS[l].name).join('+') : 'no label'
    case 'group': case 'path': return p.folder.split('/').pop() || '/'
    case 'custom': return p.ord != null ? `#${p.ord}` : ''
    case 'shuffle': return ''
    case 'lens': return p.lens || 'no lens info'
    case 'camera': return p.camera || ''
    case 'focal': return p.focal ? `${Math.round(p.focal)} mm` : ''
    case 'aperture': return p.aperture ? `f/${p.aperture}` : ''
    case 'iso': return p.iso ? `ISO ${p.iso}` : ''
    case 'exposure': return fmtExposure(p.exposure)
    case 'tag': return p.tags.length ? leaf([...p.tags].sort()[0]) : 'no tags'
    case 'edited': return p.edited ? 'edited' : 'not edited'
    case 'aspect': return aspectLabel(p)
    case 'title': return p.title || 'no title'
    case 'description': return p.description || ''
  }
  return ''
}

export function exifLine(p: Photo) {
  return [p.focal ? `${Math.round(p.focal)} mm` : '', p.aperture ? `f/${p.aperture}` : '', fmtExposure(p.exposure), p.iso ? `ISO ${p.iso}` : '']
    .filter(Boolean).join(' · ')
}
