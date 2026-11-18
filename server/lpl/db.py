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

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
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
        c = conn()
        c.executescript(SCHEMA)
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
