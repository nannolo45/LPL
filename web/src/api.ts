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
}

export interface Folder { path: string; count: number; edited: number; first: string | null; last: string | null; covers: { id: number; v: number }[]; direct: number; pos: number | null }
export interface Tag { path: string; synonyms: string; category: number; private: number; n: number }
export interface SeriesInfo { id: number; name: string; count: number; covers: { id: number; v: number }[] }
export interface Frame { id?: number; label: string; x: number; y: number; w: number; h: number; maybe: number }
export interface WorkItem { id: number; filename: string; status: string; local_path?: string; error?: string; checkout_ts?: number; synced_ts?: number }
export interface Agent { id: string; name: string; os: string; last_seen: number; online: boolean; state: { working_set?: WorkItem[]; darktable_running?: boolean; bridge_alive?: boolean; bridge_status?: string; version?: string; message?: string } }

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
  status: () => call<{ photos: number; scan: any; agents: Agent[]; server_time: number; version: string }>('GET', '/api/status'),
  scan: (full = false) => call('POST', '/api/scan', { full }),
  query: (sort: SortLevel[], filters: Filters, offset: number, limit: number, seed: number) =>
    call<{ total: number; offset: number; items: Photo[] }>('POST', '/api/photos/query', { sort, filters, offset, limit, seed }),
  ids: (sort: SortLevel[], filters: Filters, seed: number) => call<{ ids: number[] }>('POST', '/api/photos/ids', { sort, filters, seed }),
  get: (ids: number[]) => call<{ items: Photo[] }>('POST', '/api/photos/get', { ids }),
  photo: (id: number) => call<Photo>('GET', `/api/photos/${id}`),
  meta: (body: Record<string, unknown>) => call<{ items: Photo[]; errors: string[] }>('POST', '/api/meta', body),
  facets: () => call<{ lenses: { v: string; n: number }[]; cameras: { v: string; n: number }[] }>('GET', '/api/facets'),
  folders: () => call<{ folders: Folder[] }>('GET', '/api/folders'),
  folderOrder: (order: string[]) => call('POST', '/api/folders/order', { order }),
  timeline: (filters: Filters) => call<{ months: { month: string; count: number; folders: string[]; covers: { id: number; v: number }[] }[] }>('POST', '/api/timeline', { filters }),
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
  board: (id: number) => call<{ series: { id: number; name: string }; frames: Frame[]; items: Photo[] }>('GET', `/api/series/${id}/board`),
  boardSave: (id: number, items: { id: number; x: number; y: number; ord: number | null }[], frames?: Frame[]) =>
    call<{ series: { id: number; name: string }; frames: Frame[]; items: Photo[] }>('PUT', `/api/series/${id}/board`, frames ? { items, frames } : { items }),
  seriesAdd: (id: number, ids: number[]) => call<{ added: number }>('POST', `/api/series/${id}/items`, { ids }),
  seriesRemove: (id: number, ids: number[]) => call('POST', `/api/series/${id}/remove`, { ids }),
  agents: () => call<{ agents: Agent[]; server_time: number }>('GET', '/api/agents'),
  job: (kind: string, payload: Record<string, unknown>, agent_id?: string) => call<{ id: number; agent_id: string | null }>('POST', '/api/jobs', { kind, payload, agent_id }),
  jobs: () => call<{ jobs: { id: number; agent_id: string | null; kind: string; payload: any; created: number; status: string }[] }>('GET', '/api/jobs'),
}

export const thumbUrl = (p: { id: number; preview_ver?: number }, size: 't' | 'l' = 't') => `/api/preview/${p.id}?s=${size}&v=${p.preview_ver ?? 0}`
export const coverUrl = (c: { id: number; v: number }) => `/api/preview/${c.id}?s=t&v=${c.v}`
