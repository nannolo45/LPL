export interface Photo {
  id: number
  relpath: string
  folder: string
  filename: string
  ext: string
  taken: string | null
  import_ts: number | null
  change_ts: number | null
  export_ts: number | null
  print_ts: number | null
  camera: string | null
  lens: string | null
  focal: number | null
  aperture: number | null
  iso: number | null
  exposure: number | null
  width: number | null
  height: number | null
  orientation: number
  rating: number
  title: string | null
  description: string | null
  edited: number
  preview_ver: number
  preview_state: number
  meta_mtime: number
  hidden?: number
  labels: number[]
  tags: string[]
  dt_tags: string[]
  // board only
  x?: number
  y?: number
  ord?: number | null
  series?: { id: number; name: string }[]
}

export interface SortLevel { f: string; d: 'asc' | 'desc' }

export interface Filters {
  q?: string
  rating?: string
  ratings?: number[]
  lenses?: string[]
  cameras?: string[]
  show_hidden?: boolean
  folders?: string[]
  labels?: number[]
  label_mode?: 'or' | 'and'
  lens?: string
  camera?: string
  edited?: '' | 'yes' | 'no'
  folder?: string | null
  subfolders?: boolean
  month?: string | null
  tag?: string | null
  series?: number | null
  tags?: string[]
  tag_mode?: 'or' | 'and'
  only_hidden?: boolean
  date_from?: string | null
  date_to?: string | null
}

// board notes and arrows; an arrow end sits on a photo or a note, or at a free point
export interface BoardEnd { x: number; y: number; photo?: number; note?: string }
export interface BoardNote { key: string; kind: 'note'; x: number; y: number; w: number; h: number; text: string; color: string }
export interface BoardArrow { key: string; kind: 'arrow'; a: BoardEnd; b: BoardEnd; text?: string }
export type BoardShape = BoardNote | BoardArrow

export interface AddTarget { frame?: number; new?: boolean; per_folder?: boolean; label?: string }
export interface LastAdd { filters: Filters; target: AddTarget }
export interface MatchResult { total: number; new: number; limit: number; folders: number; sample: { id: number; v: number }[] }

export interface Folder { path: string; count: number; edited: number; first: string | null; last: string | null; covers: { id: number; v: number; pending?: boolean }[]; direct: number; pos: number | null; hidden?: boolean }
export interface FoldersResult { folders: Folder[]; excluded: string[]; previews_pending: number }
export interface Tag { path: string; synonyms: string; category: number; private: number; n: number }
export interface SeriesInfo { id: number; name: string; count: number; covers: { id: number; v: number }[] }
export interface Frame { id?: number; label: string; x: number; y: number; w: number; h: number; maybe: number; color?: string; border?: string }
export interface WorkItem { id: number; filename: string; status: string; local_path?: string; error?: string; checkout_ts?: number; synced_ts?: number }
export interface Agent { id: string; name: string; os: string; last_seen: number; online: boolean; state: { working_set?: WorkItem[]; darktable_running?: boolean; bridge_alive?: boolean; bridge_status?: string; version?: string; message?: string; local_url?: string; local_error?: string } }

export interface SendFolder { folder: string; n: number; sent: number; changed: number; first: string | null; last: string | null; descriptor: string }
export interface SendRequest { ids?: number[]; folder?: string; parent: string; descriptor: string; split_by_day: boolean; delete_after?: boolean }
export interface SendState { running: boolean; phase: string; done: number; total: number; bytes_done: number; bytes_total: number; sent_files: number; sent_xmps: number; skipped: number; deleted: number; errors: string[]; finished: number | null; dest_folders: string[]; series?: { name: string; created: boolean; added: number }[] }

export class AuthError extends Error {}

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: body !== undefined ? { 'content-type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    credentials: 'same-origin',
  })
  if (r.status === 401) throw new AuthError('login required')
  if (!r.ok) {
    let msg = r.statusText
    try { msg = (await r.json()).detail || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}

export const api = {
  auth: () => call<{ required: boolean; ok: boolean }>('GET', '/api/auth'),
  login: (password: string) => call<{ ok: boolean }>('POST', '/api/login', { password }),
  status: () => call<{ photos: number; scan: any; agents: Agent[]; server_time: number; version: string; mode: 'nas' | 'local'; nas_url: string | null }>('GET', '/api/status'),
  libraryFolders: () => call<{ folders: { name: string; path: string; online: boolean; count: number }[] }>('GET', '/api/library/folders'),
  libraryAdd: (path: string) => call<{ name: string }>('POST', '/api/library/folders', { path }),
  libraryRemove: (name: string) => call<{ removed: number }>('POST', '/api/library/folders/remove', { name }),
  fsList: (path: string) => call<{ path: string; parent: string | null; dirs: string[] }>('GET', '/api/fs/list?path=' + encodeURIComponent(path)),
  sendOverview: () => call<{ folders: SendFolder[]; nas: { ok: boolean; url: string; photos?: number; error?: string }; delete_after: boolean }>('GET', '/api/send/overview'),
  sendNasFolders: (year: string) => call<{ folders: string[]; suggested_parent: string }>('GET', '/api/send/nas-folders?year=' + year),
  sendPlan: (body: SendRequest) => call<{ count: number; bytes: number; already_sent: number; folders: { path: string; count: number }[] }>('POST', '/api/send/plan', body),
  sendStart: (body: SendRequest) => call<{ total: number; folders: string[] }>('POST', '/api/send/start', body),
  sendStatus: () => call<SendState>('GET', '/api/send/status'),
  scan: (full = false) => call('POST', '/api/scan', { full }),
  query: (sort: SortLevel[], filters: Filters, offset: number, limit: number, seed: number) =>
    call<{ total: number; offset: number; items: Photo[] }>('POST', '/api/photos/query', { sort, filters, offset, limit, seed }),
  ids: (sort: SortLevel[], filters: Filters, seed: number) => call<{ ids: number[] }>('POST', '/api/photos/ids', { sort, filters, seed }),
  position: (sort: SortLevel[], filters: Filters, seed: number, id: number) => call<{ index: number }>('POST', '/api/photos/position', { sort, filters, seed, id }),
  get: (ids: number[]) => call<{ items: Photo[] }>('POST', '/api/photos/get', { ids }),
  photo: (id: number) => call<Photo>('GET', `/api/photos/${id}`),
  meta: (body: Record<string, unknown>) => call<{ items: Photo[]; errors: string[] }>('POST', '/api/meta', body),
  facets: () => call<{ lenses: { v: string; n: number }[]; cameras: { v: string; n: number }[]; hidden_photos?: number }>('GET', '/api/facets'),
  hide: (ids: number[], hidden: boolean) => call<{ changed: number; hidden_photos: number }>('POST', '/api/photos/hide', { ids, hidden }),
  folders: () => call<FoldersResult>('GET', '/api/folders'),
  folderHide: (path: string, hidden: boolean) => call<{ hidden: string[] }>('POST', '/api/folders/hide', { path, hidden }),
  folderExclude: (path: string, excluded: boolean) => call<{ excluded: string[]; removed: number }>('POST', '/api/folders/exclude', { path, excluded }),
  folderOrder: (order: string[]) => call('POST', '/api/folders/order', { order }),
  timeline: (filters: Filters) => call<{ months: { month: string; count: number; folders: string[]; paths?: string[]; covers: { id: number; v: number }[] }[] }>('POST', '/api/timeline', { filters }),
  tags: () => call<{ tags: Tag[] }>('GET', '/api/tags'),
  tagCreate: (path: string) => call('POST', '/api/tags/create', { path }),
  tagRename: (old: string, nw: string) => call<{ photos: number; errors: string[] }>('POST', '/api/tags/rename', { old, new: nw }),
  tagDelete: (path: string) => call<{ photos: number }>('POST', '/api/tags/delete', { path }),
  tagProps: (path: string, props: Record<string, unknown>) => call('POST', '/api/tags/props', { path, ...props }),
  tagSuggest: (ids: number[]) => call<{ tags: string[] }>('POST', '/api/tags/suggest', { ids }),
  series: () => call<{ series: SeriesInfo[] }>('GET', '/api/series'),
  seriesCreate: (name: string, ids: number[] = []) => call<{ id: number }>('POST', '/api/series', { name, ids }),
  seriesRename: (id: number, name: string) => call('PATCH', `/api/series/${id}`, { name }),
  seriesDelete: (id: number) => call('DELETE', `/api/series/${id}`),
  board: (id: number) => call<{ series: { id: number; name: string }; frames: Frame[]; items: Photo[]; last_add?: LastAdd | null; shapes?: BoardShape[] }>('GET', `/api/series/${id}/board`),
  shapesSave: (id: number, shapes: BoardShape[]) => call<{ ok: boolean }>('PUT', `/api/series/${id}/shapes`, { shapes }),
  seriesMatch: (id: number, filters: Filters, sort?: SortLevel[]) => call<MatchResult>('POST', `/api/series/${id}/match`, { filters, sort }),
  seriesAddMatching: (id: number, filters: Filters, target: AddTarget, sort?: SortLevel[]) =>
    call<{ added: number }>('POST', `/api/series/${id}/add-matching`, { filters, target, sort }),
  boardSave: (id: number, items: { id: number; x: number; y: number; ord: number | null }[], frames?: Frame[]) =>
    call<{ series: { id: number; name: string }; frames: Frame[]; items: Photo[] }>('PUT', `/api/series/${id}/board`, frames ? { items, frames } : { items }),
  seriesAdd: (id: number, ids: number[]) => call<{ added: number }>('POST', `/api/series/${id}/items`, { ids }),
  seriesRemove: (id: number, ids: number[]) => call('POST', `/api/series/${id}/remove`, { ids }),
  prefGet: (key: string) => call<{ value: unknown }>('GET', `/api/prefs/${key}`),
  prefSet: (key: string, value: unknown) => call<{ ok: boolean }>('PUT', `/api/prefs/${key}`, { value }),
  agents: () => call<{ agents: Agent[]; server_time: number }>('GET', '/api/agents'),
  job: (kind: string, payload: Record<string, unknown>, agent_id?: string) => call<{ id: number; agent_id: string | null; message?: string }>('POST', '/api/jobs', { kind, payload, agent_id }),
  jobs: () => call<{ jobs: { id: number; agent_id: string | null; kind: string; payload: any; created: number; status: string }[] }>('GET', '/api/jobs'),
}

export const thumbUrl = (p: { id: number; preview_ver?: number }, size: 't' | 'l' = 't') => `/api/preview/${p.id}?s=${size}&v=${p.preview_ver ?? 0}`
export const coverUrl = (c: { id: number; v: number }) => `/api/preview/${c.id}?s=t&v=${c.v}`
