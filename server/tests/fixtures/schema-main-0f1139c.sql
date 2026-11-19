-- The database schema of a library made by LPL on main (commit 0f1139c), as db.init() leaves it.
-- Made by running main's server/lpl/db.py init() on an empty data folder and listing sqlite_master.
CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    name TEXT,
    os TEXT,
    last_seen REAL,
    state TEXT
);
CREATE TABLE folder_order (
    path TEXT PRIMARY KEY,
    pos INTEGER NOT NULL
);
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    agent_id TEXT,
    kind TEXT NOT NULL,
    payload TEXT,
    created REAL,
    taken REAL,
    status TEXT DEFAULT 'queued'
);
CREATE TABLE photo_labels (
    photo_id INTEGER NOT NULL,
    label INTEGER NOT NULL,
    PRIMARY KEY (photo_id, label)
);
CREATE TABLE photo_tags (
    photo_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (photo_id, tag_id)
);
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
CREATE TABLE sent (
    photo_id INTEGER PRIMARY KEY,
    dest TEXT NOT NULL,
    size INTEGER,
    sha256 TEXT,
    xmp_mtime REAL,
    ts REAL
);
CREATE TABLE series (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created REAL
);
CREATE TABLE series_frames (
    id INTEGER PRIMARY KEY,
    series_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    x REAL, y REAL, w REAL, h REAL,
    maybe INTEGER DEFAULT 0
, color TEXT DEFAULT '', border TEXT DEFAULT '');
CREATE TABLE series_items (
    series_id INTEGER NOT NULL,
    photo_id INTEGER NOT NULL,
    x REAL DEFAULT 0,
    y REAL DEFAULT 0,
    ord INTEGER,
    PRIMARY KEY (series_id, photo_id)
);
CREATE TABLE series_shapes (
    series_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    synonyms TEXT DEFAULT '',
    category INTEGER DEFAULT 0,
    private INTEGER DEFAULT 0
);
CREATE INDEX idx_photo_tags_tag ON photo_tags(tag_id);
CREATE INDEX idx_photos_folder ON photos(folder);
CREATE INDEX idx_photos_taken ON photos(taken);
