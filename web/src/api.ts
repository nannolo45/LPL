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
  /** board and strip only: the number shown on the photo (its place among the series' visible photos) and its series */
  num?: number | null
  series_id?: number | null
  series?: { id: number; name: string; folder_id?: number | null; folder?: string | null; path?: string[]; color?: string; num?: number | null; count?: number }[]
  // photo detail only (GET /api/photos/{id})
  last_edit?: EditEvent | null
}

/** A darktable module instance in an edit: operation, its English name (from module-names.json on the
 *  server, else the operation), the instance's name, on or off; label = name + instance name. */
export interface EditModule { operation: string; name: string; instance: string; priority: number; enabled: boolean; label: string
  /** in an edit's modules: darktable applied it by itself and nobody changed it (left out of the summary) */
  auto?: boolean }
export interface EditChanges { added: EditModule[]; removed: EditModule[]; switched_on: EditModule[]; switched_off: EditModule[]; changed: EditModule[] }
/** Who edited a photo and what changed (GET /api/photos/{id}/edits). source: agent = posted by a device,
 *  outside = found changed on the NAS, import = sent from a PC's local library, local = darktable on this PC.
 *  changes null: nothing earlier to compare with. */
export interface EditEvent {
  id: number; photo_id: number; time: number; source: 'agent' | 'outside' | 'import' | 'local' | string
  agent_id: string | null; device: string | null; device_os: string | null
  modules: EditModule[]; changes: EditChanges | null; summary: string
  /** saves of one device on one day are one entry: time is the last save, first_time the first */
  first_time?: number; saves?: number
}

export interface SortLevel { f: string; d: 'asc' | 'desc' }

export interface Filters {
  q?: string
  rating?: string
  ratings?: number[]
  lenses?: string[]
  cameras?: string[]
  formats?: string[]
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
  /** every series of a series folder, one after the other */
  series_folder?: number | null
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

export interface AddTarget { frame?: number; series?: number; new?: boolean; per_folder?: boolean; label?: string }
export interface LastAdd { filters: Filters; target: AddTarget }
export interface MatchResult { total: number; new: number; limit: number; folders: number; sample: { id: number; v: number }[] }

export interface Folder { path: string; count: number; edited: number; first: string | null; last: string | null; covers: { id: number; v: number; pending?: boolean }[]; direct: number; pos: number | null; hidden?: boolean }
export interface FoldersResult { folders: Folder[]; excluded: string[]; previews_pending: number }
export interface Tag { path: string; synonyms: string; category: number; private: number; n: number }
export interface SeriesInfo { id: number; name: string; count: number; covers: { id: number; v: number }[]
  /** the series folder it is in (null: top level), its place there, its lane colour, and whether its own board gives its order */
  folder_id?: number | null; pos?: number | null; color?: string; linked?: boolean }
export interface SeriesFolder { id: number; name: string; pos: number | null; linked: boolean; count: number; series_count: number
  /** nested folders: the folder it lies in (null: top level), its colour (group frame, strip heading), how deep it lies, the
   *  top-level folder whose board it is on, and what is in it with its subfolders */
  parent_id?: number | null; color?: string; depth?: number; root_id?: number; total_count?: number; total_series?: number }
/** A subfolder as the board and the strip show it (a group frame / a group heading). */
export interface GroupInfo { id: number; name: string; color: string; parent_id: number | null; depth: number; series_count: number; count: number }
/** What is open in the Series view: one series, or a folder of series. */
export interface Target { kind: 'series' | 'folder'; id: number }
export interface Frame { id?: number; label: string; x: number; y: number; w: number; h: number; maybe: number; color?: string; border?: string
  /** on a folder's board: the series this frame is (null: only a visual group) */
  series_id?: number | null
  /** on a folder's board: the subfolder this group frame is; group: a new frame drawn as a subfolder */
  sub_id?: number | null; group?: boolean }
export interface LaneInfo { id: number; name: string; color: string; count: number; folder_id?: number | null; path?: string[] }
export interface FolderHead { id: number; name: string; linked: boolean; parent_id?: number | null }
export interface BoardData { series?: { id: number; name: string }; folder?: FolderHead | null; linked?: boolean; color?: string
  root?: { id: number; name: string }; path?: { id: number; name: string }[]; groups?: GroupInfo[]; focus?: number | null
  lanes?: LaneInfo[]; frames: Frame[]; items: Photo[]; last_add?: LastAdd | null; shapes?: BoardShape[]; warnings?: string[] }
export interface Lane { key: string; series: { id: number; name: string; color: string } | null; label: string; color: string; maybe: number; frame: boolean; photos: Photo[]
  folder_id?: number | null; depth?: number }
export interface StripData { series?: { id: number; name: string; folder_id: number | null; color: string }; folder?: FolderHead | null; linked: boolean; lanes: Lane[]
  root?: { id: number; name: string }; path?: { id: number; name: string }[]; groups?: GroupInfo[]; tree?: { folder?: number; series?: number; depth: number }[] }
/** How far a board and its strip are apart (GET …/link): what linking them again would do. */
export interface LinkState { linked: boolean; same: boolean; frames: number; series?: number; photos?: number; in_folder?: boolean
  free_frames?: { id: number; label: string; photos: number }[]; missing_frames?: string[]; moves?: number; reordered?: number
  loose?: { id: number; filename: string }[]; off_board?: number; outside?: number; maybe?: number; differs?: number
  new_folders?: string[]; refiled?: number }
export type LinkMode = 'strip' | 'board' | 'none'
const tbase = (t: Target) => (t.kind === 'folder' ? `/api/series-folders/${t.id}` : `/api/series/${t.id}`)
/** step and progress (0..1): what a device is doing with the photo right now, if it says (R16). */
export interface WorkItem { id: number; filename: string; status: string; local_path?: string; error?: string; checkout_ts?: number; synced_ts?: number; step?: string; progress?: number }
export interface Agent { id: string; name: string; os: string; last_seen: number; online: boolean; state: { working_set?: WorkItem[]; darktable_running?: boolean; bridge_alive?: boolean; bridge_status?: string; version?: string; message?: string; local_url?: string; local_error?: string }
  /** what the device says it can do (batch, cancel, open); what is being sent to it; a batch that was stopped; folded on the Devices page (null: never chosen) */
  caps?: string[]; transfer?: import('./transfer').Transfer | null; stopped?: import('./transfer').StoppedRun | null; folded?: boolean | null; folded_phone?: boolean | null }

export interface SendFolder { folder: string; n: number; sent: number; changed: number; first: string | null; last: string | null; descriptor: string }
export interface SendRequest { ids?: number[]; folder?: string; parent: string; descriptor: string; split_by_day: boolean; delete_after?: boolean }
export interface SendState { running: boolean; phase: string; done: number; total: number; bytes_done: number; bytes_total: number; sent_files: number; sent_xmps: number; skipped: number; deleted: number; errors: string[]; finished: number | null; dest_folders: string[]; series?: { name: string; created: boolean; added: number }[] }

export class AuthError extends Error {}

export async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
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
  edits: (id: number) => call<{ edits: EditEvent[] }>('GET', `/api/photos/${id}/edits`),
  meta: (body: Record<string, unknown>) => call<{ items: Photo[]; errors: string[] }>('POST', '/api/meta', body),
  facets: () => call<{ lenses: { v: string; n: number }[]; cameras: { v: string; n: number }[]; formats: { v: string; n: number }[]; hidden_photos?: number }>('GET', '/api/facets'),
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
  series: () => call<{ series: SeriesInfo[]; folders?: SeriesFolder[] }>('GET', '/api/series'),
  seriesCreate: (name: string, ids: number[] = [], folder_id?: number | null) => call<{ id: number }>('POST', '/api/series', { name, ids, folder_id: folder_id ?? undefined }),
  seriesRename: (id: number, name: string) => call('PATCH', `/api/series/${id}`, { name }),
  seriesColor: (id: number, color: string) => call('PATCH', `/api/series/${id}`, { color }),
  seriesDelete: (id: number) => call('DELETE', `/api/series/${id}`),
  board: (id: number) => call<{ series: { id: number; name: string }; frames: Frame[]; items: Photo[]; last_add?: LastAdd | null; shapes?: BoardShape[] }>('GET', `/api/series/${id}/board`),
  shapesSave: (id: number, shapes: BoardShape[]) => call<{ ok: boolean }>('PUT', `/api/series/${id}/shapes`, { shapes }),
  seriesMatch: (id: number, filters: Filters, sort?: SortLevel[]) => call<MatchResult>('POST', `/api/series/${id}/match`, { filters, sort }),
  seriesAddMatching: (id: number, filters: Filters, target: AddTarget, sort?: SortLevel[]) =>
    call<{ added: number }>('POST', `/api/series/${id}/add-matching`, { filters, target, sort }),
  boardSave: (id: number, items: { id: number; x: number; y: number; ord: number | null }[], frames?: Frame[]) =>
    call<{ series: { id: number; name: string }; frames: Frame[]; items: Photo[] }>('PUT', `/api/series/${id}/board`, frames ? { items, frames } : { items }),
  seriesAdd: (id: number, ids: number[]) => call<{ added: number; moved?: number }>('POST', `/api/series/${id}/items`, { ids }),
  seriesRemove: (id: number, ids: number[]) => call('POST', `/api/series/${id}/remove`, { ids }),
  // series folders, the strip and the board link (a Target is one series or a folder of series)
  folderCreate: (name: string, parent_id?: number | null) => call<{ id: number }>('POST', '/api/series-folders', { name, parent_id: parent_id ?? undefined }),
  folderColor: (id: number, color: string) => call<{ ok: boolean }>('PATCH', `/api/series-folders/${id}`, { color }),
  folderMove: (id: number, parent_id: number | null, before?: number | null, before_folder?: number | null) =>
    call<{ ok: boolean; errors?: string[] }>('POST', `/api/series-folders/${id}/move`, { parent_id, before: before ?? undefined, before_folder: before_folder ?? undefined }),
  folderRename: (id: number, name: string) => call<{ ok: boolean; errors?: string[] }>('PATCH', `/api/series-folders/${id}`, { name }),
  folderDelete: (id: number) => call<{ ok: boolean; series: number[] }>('DELETE', `/api/series-folders/${id}`),
  seriesMove: (id: number, folder_id: number | null, before?: number | null, before_folder?: number | null) => call<{ ok: boolean; errors?: string[] }>('POST', `/api/series/${id}/move`, { folder_id, before: before ?? null, before_folder: before_folder ?? null }),
  seriesTopOrder: (entries: ({ folder: number } | { series: number })[], folder_id?: number | null) => call<{ ok: boolean }>('POST', '/api/series/top-order', { entries, folder_id: folder_id ?? undefined }),
  seriesOrder: (folder_id: number | null, ids: number[]) => call<{ ok: boolean }>('POST', '/api/series/order', { folder_id, ids }),
  tboard: (t: Target) => call<BoardData>('GET', `${tbase(t)}/board`),
  tboardSave: (t: Target, body: { items: { id: number; x: number; y: number }[]; frames?: Frame[]; remove?: number[]; restore?: boolean }) => call<BoardData>('PUT', `${tbase(t)}/board`, body),
  tshapesSave: (t: Target, shapes: BoardShape[]) => call<{ ok: boolean }>('PUT', `${tbase(t)}/shapes`, { shapes }),
  tmatch: (t: Target, filters: Filters, sort?: SortLevel[]) => call<MatchResult>('POST', `${tbase(t)}/match`, { filters, sort }),
  taddMatching: (t: Target, filters: Filters, target: AddTarget, sort?: SortLevel[]) => call<{ added: number }>('POST', `${tbase(t)}/add-matching`, { filters, target, sort }),
  seriesLanes: (id: number, lane: string, before: string | null) => call<StripData>('POST', `/api/series/${id}/strip/lanes`, { lane, before }),
  tstrip: (t: Target) => call<StripData>('GET', `${tbase(t)}/strip`),
  /** photos (in the order they should stand) into a lane, in front of `before` (null: at its end) */
  tstripMove: (t: Target, ids: number[], lane: Lane, before: number | null) =>
    call<StripData>('POST', `${tbase(t)}/strip/move`, t.kind === 'folder' ? { ids, series: lane.series?.id, before } : { ids, lane: lane.key, before }),
  tlink: (t: Target) => call<LinkState>('GET', `${tbase(t)}/link`),
  tlinkSet: (t: Target, linked: boolean, mode?: LinkMode) =>
    call<{ linked: boolean; removed_frames?: string[]; created?: string[]; created_folders?: string[]; loose?: { id: number; filename: string }[]; undo?: number }>('POST', `${tbase(t)}/link`, { linked, mode }),
  tlinkRestore: (t: Target, token: number) => call<{ ok: boolean }>('POST', `${tbase(t)}/link/restore`, { token }),
  folderBoardRemove: (id: number, ids: number[]) => call<BoardData>('POST', `/api/series-folders/${id}/remove`, { ids }),
  prefGet: (key: string) => call<{ value: unknown }>('GET', `/api/prefs/${key}`),
  prefSet: (key: string, value: unknown) => call<{ ok: boolean }>('PUT', `/api/prefs/${key}`, { value }),
  agents: () => call<{ agents: Agent[]; server_time: number }>('GET', '/api/agents'),
  job: (kind: string, payload: Record<string, unknown>, agent_id?: string) => call<{ id: number; agent_id: string | null; message?: string }>('POST', '/api/jobs', { kind, payload, agent_id }),
  jobs: () => call<{ jobs: { id: number; agent_id: string | null; kind: string; payload: any; created: number; status: string }[] }>('GET', '/api/jobs'),
}

export const thumbUrl = (p: { id: number; preview_ver?: number }, size: 't' | 'l' = 't') => `/api/preview/${p.id}?s=${size}&v=${p.preview_ver ?? 0}`
export const coverUrl = (c: { id: number; v: number }) => `/api/preview/${c.id}?s=t&v=${c.v}`
