"""Metadata (exiftool) and preview extraction (embedded JPEG via rawpy, exiftool as fallback)."""
import io
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image, ImageOps

from .config import RAW_EXTS, config

log = logging.getLogger("lpl.media")
NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}  # no console flashes on Windows

try:
    import rawpy  # type: ignore
except Exception:  # pragma: no cover
    rawpy = None

EXIF_ARGS = [
    "-j", "-charset", "filename=utf8", "-fast",
    "-DateTimeOriginal", "-CreateDate", "-ModifyDate", "-Make", "-Model",
    "-LensModel", "-LensID", "-Lens", "-FocalLength#", "-FNumber#", "-ISO#",
    "-ExposureTime#", "-ImageWidth", "-ImageHeight", "-Orientation#",
]


def read_metadata(paths: List[Path]) -> Dict[str, dict]:
    """Run exiftool once for a batch of files. Returns {absolute path: fields}."""
    if not paths:
        return {}
    with tempfile.NamedTemporaryFile("w", suffix=".args", delete=False, encoding="utf-8") as f:
        for p in paths:
            f.write(str(p) + "\n")
        argfile = f.name
    try:
        out = subprocess.run([config.exiftool, *EXIF_ARGS, "-@", argfile], capture_output=True, timeout=600, **NO_WINDOW)
        data = json.loads(out.stdout.decode("utf-8", "replace") or "[]")
    except Exception as e:
        log.warning("exiftool failed: %s", e)
        data = []
    finally:
        os.unlink(argfile)
    result = {}
    for d in data:
        src = d.get("SourceFile")
        if not src:
            continue
        result[str(Path(src))] = normalize(d)
    return result


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date(v) -> Optional[str]:
    if not v or not isinstance(v, str):
        return None
    v = v.strip()
    if len(v) < 10 or v.startswith("0000"):
        return None
    d = v[:10].replace(":", "-")
    t = v[11:19] if len(v) >= 19 else "00:00:00"
    return d + "T" + t


def normalize(d: dict) -> dict:
    make = (d.get("Make") or "").strip()
    model = (d.get("Model") or "").strip()
    for prefix in ("NIKON CORPORATION", "FUJIFILM", "Canon", "SONY", "OLYMPUS IMAGING CORP.", "OM Digital Solutions", "Panasonic", "RICOH IMAGING COMPANY, LTD.", "LEICA CAMERA AG"):
        if make.upper() == prefix.upper() and model.upper().startswith(prefix.split()[0].upper()):
            make = ""
    camera = (make.split()[0].title() + " " + model).strip() if make else model
    lens = ""
    for k in ("LensModel", "LensID", "Lens"):
        v = d.get(k)
        if v and isinstance(v, str) and not all(c in "0123456789ABCDEF " for c in v.strip()):
            lens = v.strip()
            break
    w, h = d.get("ImageWidth"), d.get("ImageHeight")
    orient = int(_num(d.get("Orientation")) or 1)
    return {
        "taken": _date(d.get("DateTimeOriginal")) or _date(d.get("CreateDate")) or _date(d.get("ModifyDate")),
        "camera": camera or None,
        "lens": lens or None,
        "focal": _num(d.get("FocalLength")),
        "aperture": _num(d.get("FNumber")),
        "iso": int(_num(d.get("ISO")) or 0) or None,
        "exposure": _num(d.get("ExposureTime")),
        "width": int(w) if isinstance(w, (int, float)) else None,
        "height": int(h) if isinstance(h, (int, float)) else None,
        "orientation": orient,
    }


def preview_paths(photo_id: int):
    sub = config.preview_dir / ("%02x" % (photo_id % 256))
    return sub / ("%d_t.jpg" % photo_id), sub / ("%d_l.jpg" % photo_id)


def _rotate(img: Image.Image, orientation: int) -> Image.Image:
    if orientation == 3:
        return img.rotate(180, expand=True)
    if orientation == 6:
        return img.rotate(270, expand=True)
    if orientation == 8:
        return img.rotate(90, expand=True)
    return img


def _embedded_jpeg_exiftool(path: Path) -> Optional[bytes]:
    for tag in ("-JpgFromRaw", "-PreviewImage", "-OtherImage", "-ThumbnailImage"):
        try:
            out = subprocess.run([config.exiftool, "-b", tag, str(path)], capture_output=True, timeout=60, **NO_WINDOW)
            if out.stdout and len(out.stdout) > 2000:
                return out.stdout
        except Exception:
            pass
    return None


def load_image(path: Path, ext: str, orientation: int) -> Image.Image:
    if ext in RAW_EXTS:
        img = None
        if rawpy is not None:
            try:
                with rawpy.imread(str(path)) as raw:
                    try:
                        th = raw.extract_thumb()
                        if th.format == rawpy.ThumbFormat.JPEG:
                            img = Image.open(io.BytesIO(th.data))
                            img.load()
                        else:
                            img = Image.fromarray(th.data)
                    except Exception:
                        img = None
                    if img is None or max(img.size) < 600:
                        rgb = raw.postprocess(half_size=True, use_camera_wb=True, output_bps=8)
                        img = Image.fromarray(rgb)
                        orientation = 1  # postprocess already applies flip
            except Exception as e:
                log.debug("rawpy failed for %s: %s", path, e)
        if img is None:
            data = _embedded_jpeg_exiftool(path)
            if data is None:
                raise RuntimeError("no preview found")
            img = Image.open(io.BytesIO(data))
            img.load()
        has_exif_orient = False
        try:
            has_exif_orient = img.getexif().get(0x0112, 1) not in (None, 1)
        except Exception:
            pass
        if has_exif_orient:
            img = ImageOps.exif_transpose(img)
        else:
            img = _rotate(img, orientation)
        return img.convert("RGB")
    img = Image.open(str(path))
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def save_previews(img: Image.Image, photo_id: int) -> None:
    t, l = preview_paths(photo_id)
    t.parent.mkdir(parents=True, exist_ok=True)
    large = img.copy()
    large.thumbnail((config.large_size, config.large_size), Image.LANCZOS)
    large.save(l, "JPEG", quality=86, optimize=True)
    thumb = img.copy()
    thumb.thumbnail((config.thumb_size, config.thumb_size), Image.LANCZOS)
    thumb.save(t, "JPEG", quality=80, optimize=True)


def make_previews(photo_id: int, path: Path, ext: str, orientation: int) -> None:
    img = load_image(path, ext, orientation)
    save_previews(img, photo_id)
