-- The database schema of a library made by LPL before the Devices page (commit 7572235), as db.init() leaves it.
-- Made by running that commit's server/lpl/db.py init() on an empty data folder and listing sqlite_master.
CREATE TABLE photos (
    id INTEGER PRIMARY KEY,
    relpath TEXT NOT NULL UNIQUE,
    folder TEXT NOT NULL,
    filename TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER,
    mtime REAL,
    xmp_mtime REAL,
    taken TEXT,
    import_ts REAL,
    change_ts REAL,
    export_ts REAL,
    print_ts REAL,
    camera TEXT,
    lens TEXT,
    focal REAL,
    aperture REAL,
    iso INTEGER,
    exposure REAL,
    width INTEGER,
    height INTEGER,
    orientation INTEGER DEFAULT 1,
    rating INTEGER DEFAULT 0,
    title TEXT,
    description TEXT,
    edited INTEGER DEFAULT 0,
    preview_ver INTEGER DEFAULT 0,
    preview_state INTEGER DEFAULT 0,
    meta_mtime REAL DEFAULT 0,
    missing INTEGER DEFAULT 0,
    first_seen REAL
, hidden INTEGER DEFAULT 0);
CREATE INDEX idx_photos_folder ON photos(folder);
CREATE INDEX idx_photos_taken ON photos(taken);
CREATE TABLE photo_labels (
    photo_id INTEGER NOT NULL,
    label INTEGER NOT NULL,
    PRIMARY KEY (photo_id, label)
);
CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    synonyms TEXT DEFAULT '',
    category INTEGER DEFAULT 0,
    private INTEGER DEFAULT 0
);
CREATE TABLE photo_tags (
    photo_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (photo_id, tag_id)
);
CREATE INDEX idx_photo_tags_tag ON photo_tags(tag_id);
CREATE TABLE folder_order (
    path TEXT PRIMARY KEY,
    pos INTEGER NOT NULL
);
CREATE TABLE series (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created REAL
);
CREATE TABLE series_items (
    series_id INTEGER NOT NULL,
    photo_id INTEGER NOT NULL,
    x REAL DEFAULT 0,
    y REAL DEFAULT 0,
    ord INTEGER,
    PRIMARY KEY (series_id, photo_id)
);
CREATE TABLE series_frames (
    id INTEGER PRIMARY KEY,
    series_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    x REAL, y REAL, w REAL, h REAL,
    maybe INTEGER DEFAULT 0
, color TEXT DEFAULT '', border TEXT DEFAULT '');
CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    name TEXT,
    os TEXT,
    last_seen REAL,
    state TEXT
);
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    agent_id TEXT,
    kind TEXT NOT NULL,
    payload TEXT,
    created REAL,
    taken REAL,
    status TEXT DEFAULT 'queued'
, attempts INTEGER DEFAULT 0);
CREATE TABLE sent (
    photo_id INTEGER PRIMARY KEY,
    dest TEXT NOT NULL,
    size INTEGER,
    sha256 TEXT,
    xmp_mtime REAL,
    ts REAL
);
CREATE TABLE series_shapes (
    series_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE edit_events (
    id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    source TEXT NOT NULL,          -- agent / outside / import
    agent_id TEXT,
    device TEXT,                   -- the agent's name at that time
    device_os TEXT,
    modules TEXT NOT NULL,         -- JSON: the modules the edit uses
    changes TEXT,                  -- JSON: added / removed / switched_on / switched_off / changed; NULL = nothing to compare with
    first_ts REAL,                 -- saves of one device on one day are one entry: ts is the last save, first_ts the first
    saves INTEGER DEFAULT 1,
    before TEXT                    -- JSON: the modules before the first save (what changes compares with); NULL = unknown
);
CREATE INDEX idx_edit_events_photo ON edit_events(photo_id, ts);
CREATE TABLE edit_baseline (
    photo_id INTEGER PRIMARY KEY,
    history TEXT NOT NULL,
    xmp_mtime REAL
);
