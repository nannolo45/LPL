# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-tagging, the B&W rule: reading the edit history, measuring pixels, applying tags, corrections."""
import base64
import struct
import zlib

import pytest

from conftest import PHOTOS

HIST = '''<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
 <rdf:Description rdf:about="" xmlns:darktable="http://darktable.sf.net/" xmlns:xmp="http://ns.adobe.com/xap/1.0/"
   xmp:Rating="1" darktable:history_end="%d">
  <darktable:history><rdf:Seq>
%s
  </rdf:Seq></darktable:history>
 </rdf:Description></rdf:RDF></x:xmpmeta>
'''


def li(num, op, enabled=1, params="00", version=1, prio=0):
    return ('<rdf:li darktable:num="%d" darktable:operation="%s" darktable:enabled="%d" darktable:modversion="%d" '
            'darktable:multi_priority="%d" darktable:params="%s"/>' % (num, op, enabled, version, prio, params))


def hist(items, end=None):
    from lpl import autotag, xmp
    data = (HIST % (len(items) if end is None else end, "\n".join(items))).encode()
    return autotag.read_history(xmp._description(xmp._parse_bytes(data)))


def mixer(grey=(0.0, 0.0, 0.0), gz=False):
    raw = struct.pack("<20f", *([1, 0, 0, 0] + [0, 1, 0, 0] + [0, 0, 1, 0] + [0] * 8)) + struct.pack("<4f", *grey, 0) + b"\0" * 40
    if gz:
        return "gz12" + base64.b64encode(zlib.compress(raw)).decode()
    return raw.hex()


def test_history_latest_state_counts():
    assert hist([li(0, "exposure"), li(1, "monochrome")])["bnw"] is True
    # switched on, then off again: colour (the old tool said B&W here)
    assert hist([li(0, "exposure"), li(1, "monochrome"), li(2, "monochrome", enabled=0)])["bnw"] is False
    # beyond history_end (undone in the history stack): not part of the edit
    assert hist([li(0, "exposure"), li(1, "monochrome")], end=1)["bnw"] is False
    # a second instance switched off does not undo the first
    assert hist([li(0, "monochrome"), li(1, "monochrome", enabled=0, prio=1)])["bnw"] is True
    assert hist([])["bnw"] is None


def test_history_color_calibration_gray_tab():
    on = hist([li(0, "channelmixerrgb", params=mixer((0.3, 0.6, 0.1)), version=3)])
    assert on["bnw"] is True and "gray" in on["why"]
    assert hist([li(0, "channelmixerrgb", params=mixer((0.3, 0.6, 0.1), gz=True), version=3)])["bnw"] is True
    assert hist([li(0, "channelmixerrgb", params=mixer(), version=3)])["bnw"] is False
    # darktable 5.x writes version 4 (the checker box appended at the end): same place for the gray tab
    v4 = hist([li(0, "channelmixerrgb", params=mixer((0.3, 0.6, 0.1)) + "00" * 40, version=4)])
    assert v4["bnw"] is True and v4["unknown"] is False
    assert hist([li(0, "channelmixerrgb", params=mixer((1, 0, 0)), version=2)])["bnw"] is True
    odd = hist([li(0, "channelmixerrgb", params="0000", version=4)])
    assert odd["bnw"] is False and odd["unknown"] is True


def test_history_color_balance_rgb_chroma():
    def cb(chroma):
        return struct.pack("<17f", *([0] * 12 + [1, 0, 1, 0, 0])).hex() + struct.pack("<f", chroma).hex() + "00" * 120
    off = hist([li(0, "colorbalancergb", params=cb(-1.0), version=5)])
    assert off["bnw"] is True and "chroma" in off["why"]
    assert hist([li(0, "colorbalancergb", params=cb(-0.4), version=5)])["bnw"] is False


def _img(kind, size=(160, 120)):
    from PIL import Image
    im = Image.new("RGB", size)
    px = im.load()
    for x in range(size[0]):
        for y in range(size[1]):
            v = int(255 * x / (size[0] - 1))
            if kind == "grey":
                px[x, y] = (v, v, v)
            elif kind == "sepia":
                px[x, y] = (min(255, int(v * 1.08) + 8), int(v * .95), int(v * .78))
            elif kind == "split":  # cool shadows, warm highlights
                t = v / 255
                px[x, y] = (min(255, int(v + 40 * t)), v, min(255, int(v + 40 * (1 - t))))
            elif kind == "cast":
                px[x, y] = (min(255, v + 4), v, max(0, v - 5))
            else:
                px[x, y] = ((x * 7) % 256, (y * 11) % 256, ((x + y) * 5) % 256)
    return im


def test_pixels():
    from lpl import autotag
    b = dict(autotag.DEFAULTS["bnw"])
    assert autotag.judge(autotag.measure(_img("grey")), b) == {"verdict": "bnw", "toned": 0, "score": 0.0}
    for k in ("sepia", "split"):
        j = autotag.judge(autotag.measure(_img(k)), b)
        assert j["verdict"] == "bnw" and j["toned"] == 1, k
    assert autotag.judge(autotag.measure(_img("colour")), b)["verdict"] == "colour"
    m = autotag.measure(_img("cast"))
    assert autotag.judge(m, {**b, "strictness": 0, "band": 100})["verdict"] in ("unsure", "bnw")
    assert autotag.judge(m, {**b, "strictness": 100})["verdict"] == "bnw"


@pytest.fixture()
def lib(client):
    """Three new photos: grey, sepia, colour (JPEG: the camera's preview is the image itself), with previews."""
    from lpl import autotag, db, scanner
    d = PHOTOS / "2025/2025-01-02 Look"
    d.mkdir(parents=True, exist_ok=True)
    for k in ("grey", "sepia", "colour"):
        _img(k, (320, 240)).save(d / ("%s.jpg" % k), quality=95)
    scanner.scan()
    rows = {r["filename"][:-4]: r for r in db.conn().execute(
        "SELECT id, relpath, ext, orientation, filename FROM photos WHERE folder='2025/2025-01-02 Look'").fetchall()}
    for r in rows.values():
        scanner._do_preview(r["id"], r["relpath"], r["ext"], r["orientation"])
    with db.write_lock:
        db.conn().execute("DELETE FROM autotag_results")
        db.conn().execute("DELETE FROM autotag_overrides")
        db.conn().commit()
    autotag.save_settings({"bnw": {"enabled": True, "folders": ["2025"], "toned": "bnw+toned"}})
    yield client, {k: r["id"] for k, r in rows.items()}, d
    autotag.save_settings({"bnw": {"enabled": False, "folders": []}})


def tags(client, pid):
    return client.get("/api/photos/%d" % pid).json()["tags"]


def test_run_apply_and_corrections(lib):
    client, ids, d = lib
    r = client.post("/api/autotag/run", json={"wait": True})
    assert r.status_code == 200
    rv = client.get("/api/autotag/review", params={"kind": "bnw"}).json()
    assert rv["counts"]["bnw"] == 2 and rv["counts"]["toned"] == 1 and rv["counts"]["adds"] == 2
    assert tags(client, ids["grey"]) == []  # a preview run writes nothing
    a = client.post("/api/autotag/apply", json={}).json()
    assert a["photos"] == 2
    assert tags(client, ids["grey"]) == ["auto|look|bnw"]
    assert tags(client, ids["sepia"]) == ["auto|look|bnw", "auto|look|toned"]
    assert tags(client, ids["colour"]) == []
    assert "auto|look|bnw" in (d / "grey.jpg.xmp").read_text()  # written to the sidecar, the way darktable reads it
    info = client.get("/api/photos/%d" % ids["grey"]).json()["autotag"]
    assert info["verdict"] == "bnw" and info["source"] == "pixels" and "no colour" in info["detail"]
    # the Look filter
    q = client.post("/api/photos/query", json={"sort": [], "filters": {"look": ["toned"]}, "offset": 0, "limit": 50, "seed": 0}).json()
    assert [p["id"] for p in q["items"]] == [ids["sepia"]]
    # a correction sticks: the colour photo marked B&W keeps it, a later run does not change it
    client.post("/api/autotag/correct", json={"ids": [ids["colour"]], "verdict": "bnw"})
    assert tags(client, ids["colour"]) == ["auto|look|bnw"]
    client.post("/api/autotag/run", json={"wait": True, "apply": True, "force": True})
    assert tags(client, ids["colour"]) == ["auto|look|bnw"]
    # the user takes the tag off by hand: that is their answer, it does not come back
    client.post("/api/meta", json={"ids": [ids["grey"]], "detach": ["auto|look|bnw"]})
    client.post("/api/autotag/run", json={"wait": True, "apply": True, "force": True})
    assert tags(client, ids["grey"]) == []
    assert client.get("/api/photos/%d" % ids["grey"]).json()["autotag"]["override"]["verdict"] == "colour"
    # back to the rule
    client.post("/api/autotag/correct", json={"ids": [ids["grey"]], "verdict": "clear"})
    assert tags(client, ids["grey"]) == ["auto|look|bnw"]


def test_toned_as_bnw_only_and_rename_cleans_up(lib):
    from lpl import autotag
    client, ids, d = lib
    client.post("/api/autotag/run", json={"wait": True, "apply": True})
    assert tags(client, ids["sepia"]) == ["auto|look|bnw", "auto|look|toned"]
    client.put("/api/autotag", json={"bnw": {"toned": "bnw", "tag_bnw": "look|bw"}})
    assert set(autotag.get_settings()["bnw"]["managed"]) == {"auto|look|bnw"}
    client.post("/api/autotag/run", json={"wait": True, "apply": True})
    assert tags(client, ids["sepia"]) == ["look|bw"]
    assert tags(client, ids["grey"]) == ["look|bw"]
    client.put("/api/autotag", json={"bnw": {"toned": "bnw+toned", "tag_bnw": "auto|look|bnw"}})
    client.post("/api/autotag/run", json={"wait": True, "apply": True})
    assert tags(client, ids["grey"]) == ["auto|look|bnw"]


def test_edit_history_and_rendered_preview(lib):
    """An edited photo: the history decides until darktable's rendered preview arrives; then the preview does,
    and when they disagree the photo waits for the user."""
    import io
    client, ids, d = lib
    pid = ids["colour"]
    (d / "colour.jpg.xmp").write_text(HIST % (2, "\n".join([li(0, "exposure"), li(1, "monochrome")])))
    from lpl import scanner
    scanner.scan()
    client.post("/api/autotag/run", json={"wait": True, "apply": True})
    info = client.get("/api/photos/%d" % pid).json()["autotag"]
    assert info["verdict"] == "bnw" and info["source"] == "history" and "monochrome" in info["detail"]
    assert tags(client, pid) == ["auto|look|bnw"]
    buf = io.BytesIO()
    _img("colour").save(buf, "JPEG")
    assert client.post("/api/photos/%d/preview" % pid, content=buf.getvalue()).status_code == 200
    client.post("/api/autotag/run", json={"wait": True, "apply": True})
    info = client.get("/api/photos/%d" % pid).json()["autotag"]
    assert info["verdict"] == "unsure" and info["source"] == "rendered"
    assert tags(client, pid) == ["auto|look|bnw"]  # unsure: left as it was, for the user to decide
    buf = io.BytesIO()
    _img("grey").save(buf, "JPEG")
    client.post("/api/photos/%d/preview" % pid, content=buf.getvalue())
    client.post("/api/autotag/run", json={"wait": True, "apply": True})
    assert client.get("/api/photos/%d" % pid).json()["autotag"]["verdict"] == "bnw"
    # back to colour in darktable (monochrome off), and a colour render: the tag comes off
    (d / "colour.jpg.xmp").write_text(HIST % (3, "\n".join([li(0, "exposure"), li(1, "monochrome"), li(2, "monochrome", enabled=0)])))
    scanner.scan()
    buf = io.BytesIO()
    _img("colour").save(buf, "JPEG")
    client.post("/api/photos/%d/preview" % pid, content=buf.getvalue())
    client.post("/api/autotag/run", json={"wait": True, "apply": True})
    assert client.get("/api/photos/%d" % pid).json()["autotag"]["verdict"] == "colour"
    assert tags(client, pid) == []


def test_camera_preview_off_leaves_unedited_photos(lib):
    client, ids, d = lib
    client.put("/api/autotag", json={"bnw": {"use_camera": False}})
    client.post("/api/autotag/run", json={"wait": True, "apply": True, "force": True})
    assert tags(client, ids["grey"]) == []
    client.put("/api/autotag", json={"bnw": {"use_camera": True}})


def test_marking_by_hand_tags_with_the_rule_off(lib):
    from lpl import autotag
    client, ids, d = lib
    autotag.save_settings({"bnw": {"enabled": False}})
    info = client.get("/api/photos/%d" % ids["colour"]).json()["autotag"]
    assert info["enabled"] is False and info["verdict"] is None  # shown (to mark by hand) even with the rule off
    client.post("/api/autotag/correct", json={"ids": [ids["colour"], ids["grey"]], "verdict": "bnw"})
    assert tags(client, ids["colour"]) == ["auto|look|bnw"] and tags(client, ids["grey"]) == ["auto|look|bnw"]
    q = client.post("/api/photos/query", json={"sort": [], "filters": {"look": ["bnw"]}, "offset": 0, "limit": 50, "seed": 0}).json()
    assert {p["id"] for p in q["items"]} == {ids["colour"], ids["grey"]}
    client.post("/api/autotag/correct", json={"ids": [ids["colour"]], "verdict": "colour"})
    assert tags(client, ids["colour"]) == []


def test_history_old_channel_mixer_lut_and_style():
    gray = struct.pack("<21f", *([0, 0, 0, 1, 0, 0, 0.3] + [0, 0, 0, 0, 1, 0, 0.6] + [0, 0, 0, 0, 0, 1, 0.1])).hex() + "01000000"
    plain = struct.pack("<21f", *([0, 0, 0, 1, 0, 0, 0] + [0, 0, 0, 0, 1, 0, 0] + [0, 0, 0, 0, 0, 1, 0])).hex() + "01000000"
    assert hist([li(0, "channelmixer", params=gray, version=2)])["bnw"] is True
    assert hist([li(0, "channelmixer", params=plain, version=2)])["bnw"] is False
    lut = (b"Fuji/Fuji XTrans III - Acros.png".ljust(512, b"\0") + b"\0" * 200).hex()
    r = hist([li(0, "lut3d", params=lut, version=3)])
    assert r["bnw"] is True and "Acros" in r["why"]
    lut = (b"Fuji/Fuji XTrans III - Velvia.png".ljust(512, b"\0") + b"\0" * 200).hex()
    assert hist([li(0, "lut3d", params=lut, version=3)])["bnw"] is False
    # a preset's name stays on the module after the edit goes back to colour: the name does not count
    plain_cc = li(0, "channelmixerrgb", params=mixer(), version=4).replace('darktable:params', 'darktable:multi_name="monochrome | Fuji Acros 100" darktable:params')
    assert hist([plain_cc])["bnw"] is False


def test_shot_bnw_in_camera_and_edited(lib):
    """Edited in darktable with no B&W module LPL knows and no darktable render: the camera's B&W preview decides."""
    client, ids, d = lib
    (d / "grey.jpg.xmp").write_text(HIST % (1, li(0, "exposure")))
    (d / "colour.jpg.xmp").write_text(HIST % (1, li(0, "exposure")))
    from lpl import scanner
    scanner.scan()
    client.post("/api/autotag/run", json={"wait": True, "apply": True, "force": True})
    info = client.get("/api/photos/%d" % ids["grey"]).json()["autotag"]
    assert info["verdict"] == "bnw" and info["source"] == "camera" and "in camera" in info["detail"]
    assert tags(client, ids["grey"]) == ["auto|look|bnw"]
    assert client.get("/api/photos/%d" % ids["colour"]).json()["autotag"]["verdict"] == "colour"
    client.put("/api/autotag", json={"bnw": {"camera_over_edit": False}})
    client.post("/api/autotag/run", json={"wait": True, "apply": True})
    assert client.get("/api/photos/%d" % ids["grey"]).json()["autotag"]["verdict"] == "colour"
    client.put("/api/autotag", json={"bnw": {"camera_over_edit": True}})


def test_small_coloured_subject_is_colour():
    """A green insect on a white sheet: most of the picture is grey, but the colour is plainly there."""
    from PIL import Image
    from lpl import autotag
    im = Image.new("RGB", (400, 300), (225, 222, 218))
    for x in range(160, 240):
        for y in range(130, 160):
            im.putpixel((x, y), (90, 160, 40))
    j = autotag.judge(autotag.measure(im), dict(autotag.DEFAULTS["bnw"]))
    assert j["verdict"] == "colour"
