"""Filtering and multi-level sorting, done in SQL so 20k+ photos stay fast."""
from typing import Any, Dict, List, Tuple

LABEL_MIN = "(SELECT MIN(label) FROM photo_labels pl WHERE pl.photo_id=p.id)"
FIRST_TAG = ("(SELECT MIN(t.path) FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id "
             "WHERE pt.photo_id=p.id AND t.path NOT LIKE 'darktable|%')")

SORT_SQL = {
    "taken": "p.taken",
    "name": "p.filename COLLATE NOCASE",
    "import": "p.import_ts",
    "modified": "p.change_ts",
    "exported": "p.export_ts",
    "printed": "p.print_ts",
    "visited": "COALESCE(p.change_ts, p.import_ts)",
    "rating": "p.rating",
    "label": LABEL_MIN,
    "group": "p.folder COLLATE NOCASE",
    "path": "p.relpath COLLATE NOCASE",
    "title": "NULLIF(p.title,'') COLLATE NOCASE",
    "description": "NULLIF(p.description,'') COLLATE NOCASE",
    "aspect": "(CASE WHEN p.orientation IN (5,6,7,8) THEN p.height*1.0/NULLIF(p.width,0) ELSE p.width*1.0/NULLIF(p.height,0) END)",
    "lens": "NULLIF(p.lens,'') COLLATE NOCASE",
    "camera": "NULLIF(p.camera,'') COLLATE NOCASE",
    "focal": "p.focal",
    "aperture": "p.aperture",
    "iso": "p.iso",
    "exposure": "p.exposure",
    "tag": FIRST_TAG,
    "edited": "(1 - p.edited)",
}

SELECT_COLS = (
    "p.id, p.relpath, p.folder, p.filename, p.ext, p.taken, p.import_ts, p.change_ts, p.export_ts, p.print_ts, "
    "p.camera, p.lens, p.focal, p.aperture, p.iso, p.exposure, p.width, p.height, p.orientation, p.rating, "
    "p.title, p.description, p.edited, p.preview_ver, p.preview_state, p.meta_mtime, "
    "(SELECT group_concat(label) FROM photo_labels pl WHERE pl.photo_id=p.id) AS labels_csv, "
    "(SELECT group_concat(t.path, char(31)) FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.photo_id=p.id) AS tags_us"
)


def build_where(f: Dict[str, Any]) -> Tuple[str, List[Any], str]:
    where = ["p.missing=0"]
    args: List[Any] = []
    join = ""
    q = (f.get("q") or "").strip()
    if q:
        like = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
        where.append(
            "(p.relpath LIKE ? ESCAPE '\\' OR p.camera LIKE ? ESCAPE '\\' OR p.lens LIKE ? ESCAPE '\\' OR p.title LIKE ? ESCAPE '\\' "
            "OR EXISTS (SELECT 1 FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.photo_id=p.id AND (t.path LIKE ? ESCAPE '\\' OR t.synonyms LIKE ? ESCAPE '\\')))")
        args += [like] * 6
    r = f.get("rating") or "all"
    if r == "unr":
        where.append("p.rating=0")
    elif r == "rej":
        where.append("p.rating=-1")
    elif r == "notrej":
        where.append("p.rating>=0")
    elif r.startswith("r") and r[1:].isdigit():
        where.append("p.rating>=?")
        args.append(int(r[1:]))
    labels = [int(x) for x in (f.get("labels") or []) if str(x).lstrip("-").isdigit()]
    if labels:
        marks = ",".join("?" * len(labels))
        if (f.get("label_mode") or "or") == "and":
            where.append("(SELECT COUNT(DISTINCT label) FROM photo_labels pl WHERE pl.photo_id=p.id AND pl.label IN (%s))=?" % marks)
            args += labels + [len(labels)]
        else:
            where.append("EXISTS (SELECT 1 FROM photo_labels pl WHERE pl.photo_id=p.id AND pl.label IN (%s))" % marks)
            args += labels
    for key, col in (("lens", "p.lens"), ("camera", "p.camera")):
        v = f.get(key)
        if v:
            where.append("%s=?" % col)
            args.append(v)
    e = f.get("edited")
    if e == "yes":
        where.append("p.edited=1")
    elif e == "no":
        where.append("p.edited=0")
    folder = f.get("folder")
    if folder is not None and folder != "":
        if f.get("subfolders", True):
            where.append("(p.folder=? OR p.folder LIKE ? ESCAPE '\\')")
            args += [folder, folder.replace("%", "\\%").replace("_", "\\_") + "/%"]
        else:
            where.append("p.folder=?")
            args.append(folder)
    month = f.get("month")
    if month:
        where.append("substr(p.taken,1,7)=?")
        args.append(month)
    tag = f.get("tag")
    if tag:
        where.append("EXISTS (SELECT 1 FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.photo_id=p.id AND (t.path=? OR t.path LIKE ? ESCAPE '\\'))")
        args += [tag, tag.replace("%", "\\%").replace("_", "\\_") + "|%"]
    series = f.get("series")
    if series:
        join = " JOIN series_items si ON si.photo_id=p.id AND si.series_id=%d" % int(series)
    ids = f.get("ids")
    if ids:
        ids = [int(i) for i in ids][:5000]
        where.append("p.id IN (%s)" % ",".join(str(i) for i in ids))
    return " AND ".join(where), args, join


def build_order(sort: List[Dict[str, str]], seed: int, has_series: bool) -> str:
    parts = []
    for lv in sort or [{"f": "taken", "d": "asc"}]:
        f = lv.get("f")
        d = "DESC" if lv.get("d") == "desc" else "ASC"
        if f == "shuffle":
            expr = "((p.id * 2654435761 + %d) %% 1000003)" % int(seed or 1)
        elif f == "custom":
            expr = "si.ord" if has_series else "p.relpath COLLATE NOCASE"
        else:
            expr = SORT_SQL.get(f)
        if not expr:
            continue
        parts.append("%s %s NULLS LAST" % (expr, d))
    parts.append("p.id ASC")
    return ", ".join(parts)


def row_to_photo(r) -> Dict[str, Any]:
    d = dict(r)
    d["labels"] = [int(x) for x in (d.pop("labels_csv") or "").split(",") if x]
    tags = [t for t in (d.pop("tags_us") or "").split("\x1f") if t]
    d["tags"] = sorted(t for t in tags if not t.startswith("darktable|"))
    d["dt_tags"] = sorted(t for t in tags if t.startswith("darktable|"))
    return d
