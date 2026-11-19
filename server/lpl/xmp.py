"""Read and write darktable-compatible XMP sidecars.

darktable stores, in <file>.<ext>.xmp:
  xmp:Rating                     0..5, -1 = rejected
  darktable:colorlabels          rdf:Seq of ints (0 red, 1 yellow, 2 green, 3 blue, 4 purple)
  lr:hierarchicalSubject         rdf:Bag of full tag paths ("places|Portugal|Lisbon")
  dc:subject                     rdf:Bag of leaf tag names
  dc:title / dc:description      rdf:Alt
  darktable:history_end, darktable:history   the edit itself (never touched by LPL)
  darktable:*_timestamp          import / change / export / print times

LPL only rewrites rating, labels, tags (and optionally title/description).
Everything else in the file is preserved. darktable's internal "darktable|..." tags are kept as they are.
"""
import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from lxml import etree

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "lr": "http://ns.adobe.com/lightroom/1.0/",
    "darktable": "http://darktable.sf.net/",
    "xml": "http://www.w3.org/XML/1998/namespace",
}


def q(prefix: str, name: str) -> str:
    return "{%s}%s" % (NS[prefix], name)


TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="LightPhotoLibrary">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about=""\n'
    '    xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
    '    xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
    '    xmlns:lr="http://ns.adobe.com/lightroom/1.0/"\n'
    '    xmlns:darktable="http://darktable.sf.net/"\n'
    '    xmp:Rating="0">\n'
    '  </rdf:Description>\n'
    ' </rdf:RDF>\n'
    '</x:xmpmeta>\n'
)

_parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, no_network=True, huge_tree=False)


def sidecar_path(image_path: Path) -> Path:
    """darktable naming: IMG.RAF -> IMG.RAF.xmp"""
    return image_path.with_name(image_path.name + ".xmp")


def alt_sidecar_path(image_path: Path) -> Path:
    """Other apps: IMG.RAF -> IMG.xmp (read only fallback)"""
    return image_path.with_suffix(".xmp")


def _parse_bytes(data: bytes):
    return etree.fromstring(data, _parser).getroottree()


def _description(tree):
    root = tree.getroot()
    d = root.find(".//" + q("rdf", "Description"))
    if d is None:
        rdf = root.find(".//" + q("rdf", "RDF"))
        if rdf is None:
            raise ValueError("not an XMP file")
        d = etree.SubElement(rdf, q("rdf", "Description"))
        d.set(q("rdf", "about"), "")
    return d


def _list_items(desc, prefix, name):
    el = desc.find(q(prefix, name))
    if el is None:
        return []
    return [(li.text or "").strip() for li in el.iter(q("rdf", "li")) if (li.text or "").strip()]


def _get_attr_or_child(desc, prefix, name):
    v = desc.get(q(prefix, name))
    if v is not None:
        return v
    el = desc.find(q(prefix, name))
    if el is not None and el.text:
        return el.text.strip()
    return None


def _alt_text(desc, prefix, name):
    el = desc.find(q(prefix, name))
    if el is None:
        return ""
    for li in el.iter(q("rdf", "li")):
        return (li.text or "").strip()
    return (el.text or "").strip()


def dt_timestamp(v) -> Optional[float]:
    """darktable writes GTimeSpan (microseconds since 0001-01-01) in recent versions, unix seconds in old ones; -1 = unset."""
    if v is None:
        return None
    try:
        n = int(float(v))
    except ValueError:
        return None
    if n <= 0:
        return None
    if n > 10 ** 14:
        return n / 1e6 - 62135596800.0
    return float(n)


def _item_value(li, name):
    """A history item's field: darktable writes them as attributes of the rdf:li; older writers as child elements."""
    v = li.get(q("darktable", name))
    if v is None:
        el = li.find(q("darktable", name))
        if el is not None:
            v = (el.text or "").strip()
    return v


def _int(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def history(desc) -> list:
    """The darktable modules the XMP's edit uses, one entry per module instance.

    darktable keeps the edit as a list of history items (darktable:history, rdf:Seq). Each item names a module
    (darktable:operation), its instance (darktable:multi_priority, and the instance's name darktable:multi_name),
    whether it is on (darktable:enabled) and its settings (darktable:params, darktable:blendop_params).
    darktable:history_end says how far the edit goes: items whose darktable:num is history_end or more were
    undone in the history stack and are not part of the edit. When several items name the same instance,
    the last one counts. Settings are not decoded: only a fingerprint is kept, to see whether they changed.

    Returns [{"operation", "instance", "priority", "enabled", "params", "num"}], most recently changed first."""
    el = desc.find(q("darktable", "history"))
    if el is None:
        return []
    items = list(el.iter(q("rdf", "li")))
    end = _get_attr_or_child(desc, "darktable", "history_end")
    end = _int(end, len(items)) if end is not None else None
    state = {}
    for pos, li in enumerate(items):
        op = _item_value(li, "operation")
        if not op:
            continue
        num = _int(_item_value(li, "num"), pos)
        if end is not None and num >= end:
            continue
        prio = _int(_item_value(li, "multi_priority"), 0)
        fp = hashlib.sha1(("%s\0%s\0%s" % (_item_value(li, "modversion") or "", _item_value(li, "params") or "",
                                           _item_value(li, "blendop_params") or "")).encode()).hexdigest()[:16]
        state[(op, prio)] = {"operation": op, "instance": _item_value(li, "multi_name") or "", "priority": prio,
                             "enabled": _item_value(li, "enabled") not in ("0", "false", "False"), "params": fp, "num": num}
    return sorted(state.values(), key=lambda m: (-m["num"], m["operation"], m["priority"]))


def parse(data: bytes) -> dict:
    tree = _parse_bytes(data)
    desc = _description(tree)
    rating = _get_attr_or_child(desc, "xmp", "Rating")
    try:
        rating = int(float(rating)) if rating is not None else 0
    except ValueError:
        rating = 0
    rating = max(-1, min(5, rating))
    labels = []
    for v in _list_items(desc, "darktable", "colorlabels"):
        try:
            labels.append(int(v))
        except ValueError:
            pass
    hier = _list_items(desc, "lr", "hierarchicalSubject")
    flat = _list_items(desc, "dc", "subject")
    leaves = {h.split("|")[-1] for h in hier}
    tags = [h for h in hier]
    for f in flat:
        if f not in leaves and f not in tags:
            tags.append(f)
    hist_end = _get_attr_or_child(desc, "darktable", "history_end")
    try:
        hist_end = int(hist_end) if hist_end is not None else 0
    except ValueError:
        hist_end = 0
    hist_items = desc.find(q("darktable", "history"))
    n_hist = len(list(hist_items.iter(q("rdf", "li")))) if hist_items is not None else 0
    return {
        "rating": rating,
        "labels": sorted(set(l for l in labels if 0 <= l <= 4)),
        "tags": tags,
        "user_tags": [t for t in tags if not t.startswith("darktable|")],
        "title": _alt_text(desc, "dc", "title"),
        "description": _alt_text(desc, "dc", "description"),
        "edited": 1 if (hist_end > 0 or n_hist > 0) else 0,
        "import_ts": dt_timestamp(_get_attr_or_child(desc, "darktable", "import_timestamp")),
        "change_ts": dt_timestamp(_get_attr_or_child(desc, "darktable", "change_timestamp")),
        "export_ts": dt_timestamp(_get_attr_or_child(desc, "darktable", "export_timestamp")),
        "print_ts": dt_timestamp(_get_attr_or_child(desc, "darktable", "print_timestamp")),
        "history": history(desc),
    }


def read_for_image(image_path: Path) -> Optional[dict]:
    for p in (sidecar_path(image_path), alt_sidecar_path(image_path)):
        if p.is_file():
            try:
                d = parse(p.read_bytes())
                d["path"] = p
                d["mtime"] = p.stat().st_mtime
                return d
            except Exception:
                continue
    return None


def _set_bag(desc, prefix, name, kind, values):
    for old in desc.findall(q(prefix, name)):
        desc.remove(old)
    if not values:
        return
    el = etree.SubElement(desc, q(prefix, name))
    container = etree.SubElement(el, q("rdf", kind))
    for v in values:
        li = etree.SubElement(container, q("rdf", "li"))
        li.text = str(v)


def _set_alt(desc, prefix, name, value):
    for old in desc.findall(q(prefix, name)):
        desc.remove(old)
    if desc.get(q(prefix, name)) is not None:
        del desc.attrib[q(prefix, name)]
    if not value:
        return
    el = etree.SubElement(desc, q(prefix, name))
    alt = etree.SubElement(el, q("rdf", "Alt"))
    li = etree.SubElement(alt, q("rdf", "li"))
    li.set(q("xml", "lang"), "x-default")
    li.text = value


def apply_meta(data: Optional[bytes], rating: Optional[int] = None, labels: Optional[Iterable[int]] = None,
               tags: Optional[Iterable[str]] = None, title: Optional[str] = None,
               description: Optional[str] = None, category_tags: Iterable[str] = ()) -> bytes:
    """Return new XMP bytes with the given fields replaced. None = leave untouched."""
    tree = _parse_bytes(data if data else TEMPLATE.encode("utf-8"))
    desc = _description(tree)
    if rating is not None:
        child = desc.find(q("xmp", "Rating"))
        if child is not None:
            child.text = str(int(rating))
        else:
            desc.set(q("xmp", "Rating"), str(int(rating)))
    if labels is not None:
        _set_bag(desc, "darktable", "colorlabels", "Seq", sorted(set(int(l) for l in labels)))
    if tags is not None:
        old_hier = _list_items(desc, "lr", "hierarchicalSubject")
        internal = [t for t in old_hier if t.startswith("darktable|")]
        user = sorted({t.strip().strip("|") for t in tags if t and not t.startswith("darktable|")})
        cats = set(category_tags)
        _set_bag(desc, "lr", "hierarchicalSubject", "Bag", user + internal)
        leaves = []
        for t in user:
            if t in cats:
                continue
            leaf = t.split("|")[-1]
            if leaf not in leaves:
                leaves.append(leaf)
        _set_bag(desc, "dc", "subject", "Bag", leaves)
    if title is not None:
        _set_alt(desc, "dc", "title", title)
    if description is not None:
        _set_alt(desc, "dc", "description", description)
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8", pretty_print=False) + b"\n"


def set_derived_from(data: bytes, filename: str) -> bytes:
    """The working-set copy has a different file name; point the sidecar back at the original."""
    tree = _parse_bytes(data)
    desc = _description(tree)
    key = "{http://ns.adobe.com/xap/1.0/mm/}DerivedFrom"
    if desc.get(key) is not None and desc.get(key) != filename:
        desc.set(key, filename)
        return etree.tostring(tree, xml_declaration=True, encoding="UTF-8") + b"\n"
    return data


def atomic_write(path: Path, data: bytes, backup_root: Optional[Path] = None, rel: Optional[str] = None) -> None:
    """Backup the original once, then replace atomically."""
    if backup_root is not None and rel is not None and path.exists():
        b = backup_root / (rel + ".orig.xmp")
        if not b.exists():
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, b)
    fd, tmp = tempfile.mkstemp(prefix=".lpl-", suffix=".xmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
