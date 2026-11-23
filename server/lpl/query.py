"""Filtering and multi-level sorting, done in SQL so 20k+ photos stay fast."""
from typing import Any, Dict, List, Tuple

LABEL_MIN = "(SELECT MIN(label) FROM photo_labels pl WHERE pl.photo_id=p.id)"
FIRST_TAG = ("(SELECT MIN(t.path) FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id "
             "WHERE pt.photo_id=p.id AND t.path NOT LIKE 'darktable|%')")

_FMT_ALIASES = {"JPEG": ["jpg", "jpeg"], "JPG": ["jpg", "jpeg"], "TIFF": ["tif", "tiff"], "TIF": ["tif", "tiff"]}


def format_of(ext: str) -> str:
    """The format name shown in the filter: upper case, JPEG as JPG and TIFF as TIF."""
    e = (ext or "").upper()
    return {"JPEG": "JPG", "TIFF": "TIF"}.get(e, e)


def format_exts(formats) -> list:
    """Stored (lower-case) extensions for a list of format names."""
    out: list = []
    for x in formats or []:
        x = str(x).strip().lstrip(".").upper()
        for e in _FMT_ALIASES.get(x, [x.lower()] if x else []):
            if e not in out:
                out.append(e)
    return out

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
    "p.title, p.description, p.edited, p.preview_ver, p.preview_state, p.meta_mtime, p.hidden, "
    "(SELECT COUNT(*) FROM photo_exports x WHERE x.photo_id=p.id) AS exports, "
    "(SELECT COUNT(*) FROM photo_versions v WHERE v.photo_id=p.id) AS versions, "
    "COALESCE((SELECT v.n FROM photo_versions v WHERE v.photo_id=p.id AND v.cover=1), 0) AS cover, "
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
    ratings = sorted({int(x) for x in (f.get("ratings") or []) if str(x).lstrip("-").isdigit() and -1 <= int(x) <= 5})
    if ratings:
        where.append("p.rating IN (%s)" % ",".join(str(r) for r in ratings))
    labels = [int(x) for x in (f.get("labels") or []) if str(x).lstrip("-").isdigit()]
    if labels:
        marks = ",".join("?" * len(labels))
        if (f.get("label_mode") or "or") == "and":
            where.append("(SELECT COUNT(DISTINCT label) FROM photo_labels pl WHERE pl.photo_id=p.id AND pl.label IN (%s))=?" % marks)
            args += labels + [len(labels)]
        else:
            where.append("EXISTS (SELECT 1 FROM photo_labels pl WHERE pl.photo_id=p.id AND pl.label IN (%s))" % marks)
            args += labels
    for key, many, col in (("lens", "lenses", "p.lens"), ("camera", "cameras", "p.camera")):
        vals = [v for v in (f.get(many) or []) if v]
        if f.get(key):
            vals.append(f[key])
        if vals:
            where.append("%s IN (%s)" % (col, ",".join("?" * len(vals))))
            args += vals
    exts = format_exts(f.get("formats"))
    if exts:
        where.append("p.ext IN (%s)" % ",".join("?" * len(exts)))
        args += exts
    e = f.get("edited")
    if e == "yes":
        where.append("p.edited=1")
    elif e == "no":
        where.append("p.edited=0")
    if f.get("exports") == "yes":  # R18: photos that have exports
        where.append("EXISTS (SELECT 1 FROM photo_exports x WHERE x.photo_id=p.id)")
    folder = f.get("folder")
    many = [x for x in (f.get("folders") or []) if x]
    opened = ([folder] if folder else []) + many
    explicit = bool(f.get("ids"))  # asked for by id (a board, a selection): hidden or not, they come back
    # hidden photos: left out unless "show hidden" is on
    if not f.get("show_hidden") and not explicit:
        where.append("COALESCE(p.hidden,0)=0")
    if f.get("only_hidden"):
        where.append("p.hidden=1")
    # hidden folders: left out unless "show hidden" is on, or you opened that folder (or one inside it) yourself
    if not f.get("show_hidden") and not explicit:
        from .scanner import hidden_folders
        for h in hidden_folders():
            if any(o == h or o.startswith(h + "/") for o in opened):
                continue
            where.append("NOT (p.folder=? OR p.folder LIKE ? ESCAPE '\\')")
            args += [h, h.replace("%", "\\%").replace("_", "\\_") + "/%"]
    if many:  # several folders picked in the Folders view: photos from any of them
        ors = []
        for x in many:
            if f.get("subfolders", True):
                ors.append("(p.folder=? OR p.folder LIKE ? ESCAPE '\\')")
                args += [x, x.replace("%", "\\%").replace("_", "\\_") + "/%"]
            else:
                ors.append("p.folder=?")
                args.append(x)
        where.append("(" + " OR ".join(ors) + ")")
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
    tags = [t for t in (f.get("tags") or []) if t]
    if tags:  # several tags (a tag also matches everything below it); any of them, or all with tag_mode "and"
        parts = []
        for t in tags:
            parts.append("EXISTS (SELECT 1 FROM photo_tags pt JOIN tags t ON t.id=pt.tag_id WHERE pt.photo_id=p.id AND (t.path=? OR t.path LIKE ? ESCAPE '\\'))")
            args += [t, t.replace("%", "\\%").replace("_", "\\_") + "|%"]
        where.append("(" + (" AND " if f.get("tag_mode") == "and" else " OR ").join(parts) + ")")
    d1, d2 = f.get("date_from"), f.get("date_to")
    if d1:
        where.append("p.taken >= ?")
        args.append(str(d1)[:10])
    if d2:
        where.append("p.taken < ?")
        args.append(str(d2)[:10] + "~")  # '~' sorts after any time part, so the whole end day counts
    not_in = f.get("not_in_series")
    if not_in:
        where.append("p.id NOT IN (SELECT photo_id FROM series_items WHERE series_id=%d)" % int(not_in))
    if f.get("no_series"):  # in no series at all (the strip's photo panel: "Not in a series yet")
        where.append("p.id NOT IN (SELECT photo_id FROM series_items)")
    not_in_folder = f.get("not_in_series_folder")
    if not_in_folder:  # in none of the series of a series folder and its subfolders
        from . import series as series_mod
        sids = [x["id"] for x in series_mod.series_under(series_mod.db.conn(), int(not_in_folder))] or [0]
        where.append("p.id NOT IN (SELECT photo_id FROM series_items WHERE series_id IN (%s))" % ",".join(str(int(i)) for i in sids))
    series = f.get("series")
    if series:
        join = " JOIN series_items si ON si.photo_id=p.id AND si.series_id=%d" % int(series)
    sfolder = f.get("series_folder")
    if sfolder and not series:  # every series of a series folder and of its subfolders, in tree order, each in its own order
        from . import series as series_mod
        sids = [x["id"] for x in series_mod.series_under(series_mod.db.conn(), int(sfolder))]
        rank = " ".join("WHEN %d THEN %d" % (sid, k) for k, sid in enumerate(sids)) or "WHEN 0 THEN 0"
        join = (" JOIN (SELECT si0.photo_id, MIN((CASE si0.series_id %s END) * 1000000 + COALESCE(si0.ord, 999999)) AS ord FROM series_items si0 "
                "WHERE si0.series_id IN (%s) GROUP BY si0.photo_id) si ON si.photo_id=p.id" % (rank, ",".join(str(i) for i in sids) or "0"))
    look = [x for x in (f.get("look") or []) if x in ("bnw", "toned", "colour", "unsure", "unchecked")]
    if look:  # what auto-tagging found (the user's corrections count over the rule's verdict)
        v = "COALESCE(ao.verdict, ar.verdict)"
        tn = "COALESCE(ao.toned, ar.toned)"
        parts = {"bnw": "%s='bnw'" % v, "toned": "(%s='bnw' AND %s=1)" % (v, tn), "colour": "%s='colour'" % v,
                 "unsure": "(ao.verdict IS NULL AND ar.verdict='unsure')", "unchecked": "(%s IS NULL OR %s='none')" % (v, v)}
        where.append("p.id IN (SELECT p2.id FROM photos p2 LEFT JOIN autotag_results ar ON ar.photo_id=p2.id AND ar.rule='bnw' "
                     "LEFT JOIN autotag_overrides ao ON ao.photo_id=p2.id AND ao.rule='bnw' WHERE %s)" % " OR ".join(parts[x] for x in look))
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
    d["versions"] = max(1, d.get("versions") or 0)  # the number of versions (1: only the photo itself)
    d["labels"] = [int(x) for x in (d.pop("labels_csv") or "").split(",") if x]
    tags = [t for t in (d.pop("tags_us") or "").split("\x1f") if t]
    d["tags"] = sorted(t for t in tags if not t.startswith("darktable|"))
    d["dt_tags"] = sorted(t for t in tags if t.startswith("darktable|"))
    return d
