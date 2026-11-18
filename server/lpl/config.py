"""Runtime configuration, read from environment variables.

PHOTO_ROOT     folder that holds the photos (inside the container: /photos)
DATA_DIR       where LPL keeps its database, previews and XMP backups (/data)
SCAN_INTERVAL  seconds between automatic rescans (0 = only on startup / on demand)
LPL_PASSWORD   optional shared password for the web UI and the PC agent
PREVIEW_WORKERS number of threads used to extract previews
WEB_DIR        folder with the built web UI (served at /)
"""
import os
from pathlib import Path


class Config:
    def __init__(self) -> None:
        self.photo_root = Path(os.environ.get("PHOTO_ROOT", "/photos")).resolve()
        self.data_dir = Path(os.environ.get("DATA_DIR", "/data")).resolve()
        self.scan_interval = int(os.environ.get("SCAN_INTERVAL", "900"))
        self.password = os.environ.get("LPL_PASSWORD", "")
        self.preview_workers = int(os.environ.get("PREVIEW_WORKERS", "2"))
        self.web_dir = Path(os.environ.get("WEB_DIR", str(Path(__file__).resolve().parent.parent / "web_dist")))
        self.thumb_size = int(os.environ.get("THUMB_SIZE", "480"))
        self.large_size = int(os.environ.get("LARGE_SIZE", "1920"))
        self.write_series_tags = os.environ.get("WRITE_SERIES_TAGS", "1") != "0"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lpl.sqlite"

    @property
    def preview_dir(self) -> Path:
        return self.data_dir / "previews"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "xmp-backups"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.preview_dir, self.backup_dir):
            d.mkdir(parents=True, exist_ok=True)


RAW_EXTS = {
    "raf", "nef", "nrw", "cr2", "cr3", "crw", "arw", "srf", "sr2", "dng", "orf", "rw2", "rwl",
    "pef", "srw", "x3f", "3fr", "fff", "iiq", "mos", "mrw", "kdc", "dcr", "erf", "raw",
}
IMG_EXTS = {"jpg", "jpeg", "tif", "tiff", "png", "webp"}
SUPPORTED_EXTS = RAW_EXTS | IMG_EXTS
SKIP_DIRS = {"@eaDir", "#recycle", "#snapshot", ".@__thumb", "@Recycle", ".Trash-1000", "$RECYCLE.BIN", "System Volume Information"}

config = Config()
