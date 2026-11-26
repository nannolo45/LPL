"""SQLite storage. One connection per thread; WAL mode so the scanner and the API can work at the same time."""
import sqlite3
import threading
import time

from .config import config

_local = threading.local()
write_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
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
);
CREATE INDEX IF NOT EXISTS idx_photos_folder ON photos(folder);
CREATE INDEX IF NOT EXISTS idx_photos_taken ON photos(taken);

CREATE TABLE IF NOT EXISTS photo_labels (
    photo_id INTEGER NOT NULL,
    label INTEGER NOT NULL,
    PRIMARY KEY (photo_id, label)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    synonyms TEXT DEFAULT '',
    category INTEGER DEFAULT 0,
    private INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS photo_tags (
    photo_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    PRIMARY KEY (photo_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_photo_tags_tag ON photo_tags(tag_id);

CREATE TABLE IF NOT EXISTS folder_order (
    path TEXT PRIMARY KEY,
    pos INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created REAL
);

CREATE TABLE IF NOT EXISTS series_items (
    series_id INTEGER NOT NULL,
    photo_id INTEGER NOT NULL,
    x REAL DEFAULT 0,
    y REAL DEFAULT 0,
    ord INTEGER,
    PRIMARY KEY (series_id, photo_id)
);

CREATE TABLE IF NOT EXISTS series_frames (
    id INTEGER PRIMARY KEY,
    series_id INTEGER NOT NULL,
    label TEXT NOT NULL,
    x REAL, y REAL, w REAL, h REAL,
    maybe INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT,
    os TEXT,
    last_seen REAL,
    state TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    agent_id TEXT,
    kind TEXT NOT NULL,
    payload TEXT,
    created REAL,
    taken REAL,
    status TEXT DEFAULT 'queued'
);

CREATE TABLE IF NOT EXISTS sent (
    photo_id INTEGER PRIMARY KEY,
    dest TEXT NOT NULL,
    size INTEGER,
    sha256 TEXT,
    xmp_mtime REAL,
    ts REAL
);

-- notes and arrows drawn on a series board (one JSON list per series; arrows point at photos by id, notes by key)
CREATE TABLE IF NOT EXISTS series_shapes (
    series_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL DEFAULT '[]'
);

-- files the darktable Android app exported to LPL (R18, exports.py)
CREATE TABLE IF NOT EXISTS photo_exports (
    id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL,            -- relative to the library root when inside it, else absolute
    name TEXT NOT NULL,
    size INTEGER NOT NULL,
    format TEXT NOT NULL,
    created REAL NOT NULL,
    agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_photo_exports_photo ON photo_exports(photo_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- who edited a photo and what changed (one row per XMP an agent posted, or the scanner found changed); see edits.py
CREATE TABLE IF NOT EXISTS edit_events (
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
CREATE INDEX IF NOT EXISTS idx_edit_events_photo ON edit_events(photo_id, ts);

-- the darktable modules of the XMP LPL last recorded for each photo, to tell what the next one changed
CREATE TABLE IF NOT EXISTS edit_baseline (
    photo_id INTEGER PRIMARY KEY,
    history TEXT NOT NULL,
    xmp_mtime REAL
);

-- series folders (see series.py): a folder holds several series and has one shared board, where (while
-- linked) each frame is one of its series. folder_frames.series_id NULL: a frame that is only a visual group.
CREATE TABLE IF NOT EXISTS series_folders (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    pos INTEGER,
    linked INTEGER DEFAULT 1,
    created REAL
);
CREATE TABLE IF NOT EXISTS folder_frames (
    id INTEGER PRIMARY KEY,
    folder_id INTEGER NOT NULL,
    series_id INTEGER,
    label TEXT NOT NULL,
    x REAL, y REAL, w REAL, h REAL,
    maybe INTEGER DEFAULT 0,
    color TEXT DEFAULT '',
    border TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS folder_items (
    folder_id INTEGER NOT NULL,
    photo_id INTEGER NOT NULL,
    x REAL DEFAULT 0,
    y REAL DEFAULT 0,
    PRIMARY KEY (folder_id, photo_id)
);
CREATE TABLE IF NOT EXISTS folder_shapes (
    folder_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL DEFAULT '[]'
);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(str(config.db_path), timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=OFF")
        _local.conn = c
    return c


def init() -> None:
    config.ensure_dirs()
    with write_lock:
        migrate(conn())


def migrate(c: sqlite3.Connection) -> None:
    """Bring a library of any earlier version up to date in place. Only adds tables and columns, never drops
    or rewrites data; safe to run on every start."""
    c.executescript(SCHEMA)
    # columns added after the first release
    cols = {r[1] for r in c.execute("PRAGMA table_info(photos)").fetchall()}
    if "hidden" not in cols:  # photos hidden from the library views (LPL only, not written to XMP)
        c.execute("ALTER TABLE photos ADD COLUMN hidden INTEGER DEFAULT 0")
    c.execute("CREATE INDEX IF NOT EXISTS idx_photos_ext ON photos(ext)")  # the format filter
    fcols = {r[1] for r in c.execute("PRAGMA table_info(series_frames)").fetchall()}
    if "color" not in fcols:  # frame look on the board: fill colour and border style (solid / dashed / none)
        c.execute("ALTER TABLE series_frames ADD COLUMN color TEXT DEFAULT ''")
        c.execute("ALTER TABLE series_frames ADD COLUMN border TEXT DEFAULT ''")
    ecols = {r[1] for r in c.execute("PRAGMA table_info(edit_events)").fetchall()}
    for col, decl in (("first_ts", "REAL"), ("saves", "INTEGER DEFAULT 1"), ("before", "TEXT")):
        if col not in ecols:  # edit_events as first made on this branch
            c.execute("ALTER TABLE edit_events ADD COLUMN %s %s" % (col, decl))
    jcols = {r[1] for r in c.execute("PRAGMA table_info(jobs)").fetchall()}
    if "attempts" not in jcols:  # how often a job was handed out (a taken job is handed out again after a while)
        c.execute("ALTER TABLE jobs ADD COLUMN attempts INTEGER DEFAULT 0")
    if "ended" not in jcols:  # when a job was reported done or was stopped (render jobs: the bar at the top)
        c.execute("ALTER TABLE jobs ADD COLUMN ended REAL")
    if "hidden" not in jcols:  # a render job cleared from the bar at the top
        c.execute("ALTER TABLE jobs ADD COLUMN hidden INTEGER DEFAULT 0")
    scols = {r[1] for r in c.execute("PRAGMA table_info(series)").fetchall()}
    # series folders and the board link: a series is at the top level (folder_id NULL) or in one folder, has a place
    # among its neighbours (pos), a lane colour, and says whether its own board gives its order (linked, default yes)
    for col, decl in (("folder_id", "INTEGER"), ("pos", "INTEGER"), ("color", "TEXT DEFAULT ''"), ("linked", "INTEGER DEFAULT 1")):
        if col not in scols:
            c.execute("ALTER TABLE series ADD COLUMN %s %s" % (col, decl))
    # the top level of the series tree has one order for folders and series together (series_folders.pos and series.pos
    # of the top-level series share it). Older libraries had two numberings (folders first, then series): where
    # they collide, number them again in that order. Nothing is rewritten once the numbers are distinct.
    # nested folders: a folder can lie in another (parent_id; NULL = top level) and has its own colour; on the board of
    # its top-level folder it is a group frame (folder_frames.sub_id). Older libraries: every folder is top-level.
    fcols2 = {r[1] for r in c.execute("PRAGMA table_info(series_folders)").fetchall()}
    for col, decl in (("parent_id", "INTEGER"), ("color", "TEXT DEFAULT ''")):
        if col not in fcols2:
            c.execute("ALTER TABLE series_folders ADD COLUMN %s %s" % (col, decl))
    if "sub_id" not in {r[1] for r in c.execute("PRAGMA table_info(folder_frames)").fetchall()}:
        c.execute("ALTER TABLE folder_frames ADD COLUMN sub_id INTEGER")
    fs = c.execute("SELECT id, pos FROM series_folders WHERE parent_id IS NULL ORDER BY pos NULLS LAST, name COLLATE NOCASE").fetchall()
    ss = c.execute("SELECT id, pos FROM series WHERE folder_id IS NULL ORDER BY pos NULLS LAST, name COLLATE NOCASE").fetchall()
    if {r[1] for r in fs} & {r[1] for r in ss}:
        for n, r in enumerate(fs):
            c.execute("UPDATE series_folders SET pos=? WHERE id=?", (n + 1, r[0]))
        for n, r in enumerate(ss):
            c.execute("UPDATE series SET pos=? WHERE id=?", (len(fs) + n + 1, r[0]))
    from . import versions  # versions of a photo (R17)
    versions.migrate(c)
    from . import transfers  # batches sent to devices: its own tables, and agents.caps
    transfers.migrate(c)
    from . import identity  # agents.machine, agents.name_pending, agents.merged_from
    identity.migrate(c)
    from . import autotag  # auto-tagging: results, corrections, where each preview came from
    autotag.migrate(c)
    from . import shared  # shared darktable styles, presets and module layouts (R11)
    shared.migrate(c)
    from . import luts  # the LUT folder (R19)
    luts.migrate(c)
    c.commit()


def now() -> float:
    return time.time()


def get_setting(key: str, default: str = "") -> str:
    r = conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_setting(key: str, value: str) -> None:
    with write_lock:
        c = conn()
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        c.commit()


def tag_id(path: str, create: bool = True):
    c = conn()
    r = c.execute("SELECT id FROM tags WHERE path=?", (path,)).fetchone()
    if r:
        return r["id"]
    if not create:
        return None
    cur = c.execute("INSERT INTO tags(path) VALUES(?)", (path,))
    return cur.lastrowid


def photo_tags(photo_id: int) -> list:
    rows = conn().execute(
        "SELECT t.path FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.photo_id=? ORDER BY t.path", (photo_id,)
    ).fetchall()
    return [r["path"] for r in rows]


def photo_labels(photo_id: int) -> list:
    rows = conn().execute("SELECT label FROM photo_labels WHERE photo_id=? ORDER BY label", (photo_id,)).fetchall()
    return [r["label"] for r in rows]


def set_photo_meta(photo_id: int, rating=None, labels=None, tags=None, title=None, description=None, touch=True) -> None:
    """Write rating/labels/tags into the DB (caller holds write_lock and commits)."""
    c = conn()
    if rating is not None:
        c.execute("UPDATE photos SET rating=? WHERE id=?", (int(rating), photo_id))
    if title is not None:
        c.execute("UPDATE photos SET title=? WHERE id=?", (title, photo_id))
    if description is not None:
        c.execute("UPDATE photos SET description=? WHERE id=?", (description, photo_id))
    if labels is not None:
        c.execute("DELETE FROM photo_labels WHERE photo_id=?", (photo_id,))
        for lab in sorted(set(int(x) for x in labels)):
            if 0 <= lab <= 4:
                c.execute("INSERT INTO photo_labels(photo_id,label) VALUES(?,?)", (photo_id, lab))
    if tags is not None:
        c.execute("DELETE FROM photo_tags WHERE photo_id=?", (photo_id,))
        for t in sorted(set(tags)):
            t = t.strip().strip("|")
            if not t:
                continue
            c.execute("INSERT OR IGNORE INTO photo_tags(photo_id,tag_id) VALUES(?,?)", (photo_id, tag_id(t)))
    if touch:
        c.execute("UPDATE photos SET meta_mtime=? WHERE id=?", (now(), photo_id))
