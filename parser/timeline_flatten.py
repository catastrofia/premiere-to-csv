# parser/timeline_flatten.py
# Robust timeline extraction with nested-sequence expansion for Premiere .prproj XML.

import os
import xml.etree.ElementTree as ET


def _first_text(elem: ET.Element | None, tag: str) -> str | None:
    if elem is None:
        return None
    for c in elem:
        if c.tag.endswith(tag):
            t = (c.text or "").strip()
            if t:
                return t
    t = elem.find(".//" + tag)
    return (t.text or "").strip() if t is not None and t.text else None


def _collect_objects(root: ET.Element):
    by_id, by_uid = {}, {}
    for e in root.iter():
        oid = e.attrib.get("ObjectID"); ouid = e.attrib.get("ObjectUID")
        if oid: by_id[oid] = e
        if ouid: by_uid[ouid] = e
    return by_id, by_uid


def _discover_sequences(root: ET.Element):
    d = {}
    for e in root.iter():
        if e.tag.endswith("Sequence"):
            nm = _first_text(e, "Name")
            if nm: d[nm] = e
    return d


def _ticks(el: ET.Element, tag: str):
    t = el.find(f".//{tag}")
    if t is not None and t.text:
        s = t.text.strip()
        if s.lstrip("-+").isdigit():
            try:
                return int(s)
            except Exception:
                return None
    return None


def _classify(name, typ):
    n = (name or "").lower()
    source = (
        "Artlist" if "artlist" in n else
        "Colourbox" if ("colourbox" in n or "colorbox" in n) else
        "Imago" if "imago" in n else ""
    )
    if typ == "Audio":
        c = "audio"
    else:
        if any(n.endswith(ext) for ext in (".png",".jpg",".jpeg",".tif",".tiff",".gif",".bmp",".webp")):
            c = "image"
        elif any(n.endswith(ext) for ext in (".mp4",".mov",".m4v",".avi",".mxf",".mkv")):
            c = "video"
        elif (name or "").lower() in ("graphic","white") or (name and "." not in name):
            c = "graphic"
        else:
            c = "video"
    return c, source


def _basename_from_paths(elem: ET.Element):
    for tag in ("AbsolutePath","RelativePath","Path","FilePath"):
        p = _first_text(elem, tag)
        if p:
            b = os.path.basename(p.replace("\\", "/"))
            if b:
                return b
    return None


def _deep_name_scan(elem: ET.Element):
    for tag in ("ClipName","DisplayName","Title","Name"):
        t = elem.find(f".//{tag}")
        if t is not None and t.text and t.text.strip():
            return t.text.strip()
    for d in elem.iter():
        if d.tag.endswith("Name") and d.text and d.text.strip():
            return d.text.strip()
    return None


def _find_sequence_reference(elem: ET.Element, by_id, by_uid):
    for k in ("ObjectRef","ObjectURef"):
        ref = elem.attrib.get(k)
        if ref:
            t = by_id.get(ref) or by_uid.get(ref)
            if t is not None and t.tag.endswith("Sequence"):
                return t
    for sub in elem.iter():
        for k in ("ObjectRef","ObjectURef"):
            ref = sub.attrib.get(k)
            if ref:
                t = by_id.get(ref) or by_uid.get(ref)
                if t is not None and t.tag.endswith("Sequence"):
                    return t
    return None


def _resolve_name_and_nested(obj: ET.Element, by_id, by_uid, seq_by_name):
    name = _first_text(obj, "Name"); nested = None
    sr = obj.find(".//SubClip")
    
    # --- FIX START ---
    # Find MasterClip reference as well
    mr = obj.find(".//MasterClip")
    # --- FIX END ---

    if sr is not None:
        ref = sr.attrib.get("ObjectRef") or sr.attrib.get("ObjectURef")
        tgt = by_id.get(ref) or by_uid.get(ref)
        if tgt is not None:
            if tgt.tag.endswith("Sequence"):
                nested = tgt; name = name or _first_text(nested, "Name")
            else:
                name = name or _first_text(tgt, "Name") or _deep_name_scan(tgt) or _basename_from_paths(tgt)
    
    # --- FIX START ---
    # Add check for MasterClip reference if SubClip not found
    elif mr is not None:
        ref = mr.attrib.get("ObjectRef") or mr.attrib.get("ObjectURef")
        tgt = by_id.get(ref) or by_uid.get(ref)
        if tgt is not None:
            if tgt.tag.endswith("Sequence"):
                nested = tgt; name = name or _first_text(nested, "Name")
            else:
                # This is the common case for standard media
                name = name or _first_text(tgt, "Name") or _deep_name_scan(tgt) or _basename_from_paths(tgt)
    # --- FIX END ---

    if nested is None:
        seq = _find_sequence_reference(obj, by_id, by_uid)
        if seq is not None:
            nested = seq; name = name or _first_text(nested, "Name")
    if nested is None and name and name in seq_by_name:
        nested = seq_by_name[name]
    if not name:
        name = _deep_name_scan(obj) or _basename_from_paths(obj)
    return name, nested


def _collect_tracks_via_trackgroups(main_seq: ET.Element, by_id, by_uid):
    vgs, ags = [], []
    for tg in main_seq.findall(".//TrackGroups/TrackGroup"):
        ref = None
        for slot in ("First","Second"):
            sl = tg.find(slot)
            if sl is not None:
                ref = sl.attrib.get("ObjectRef") or sl.attrib.get("ObjectURef")
                if ref:
                    break
        obj = (by_id.get(ref) or by_uid.get(ref)) if ref else None
        if obj is None:
            continue
        if obj.tag.endswith("VideoTrackGroup"):
            vgs.append(obj)
        elif obj.tag.endswith("AudioTrackGroup"):
            ags.append(obj)
    def tracks(groups):
        out = []
        for g in groups:
            for tr in g.findall(".//Tracks/Track"):
                idx = tr.attrib.get("Index")
                guid = tr.attrib.get("ObjectURBef") or tr.attrib.get("ObjectUID") # Typo fix: ObjectURef
                to = by_uid.get(guid)
                if to is not None:
                    out.append((int(idx) if idx and idx.isdigit() else None, to))
        out.sort(key=lambda x: (9999 if x[0] is None else x[0]))
        return out
    return tracks(vgs), tracks(ags)


def _collect_tracks_fallback(main_seq: ET.Element):
    vt, at = [], []
    v = [e for e in main_seq.iter() if e.tag.endswith("VideoTrack")]
    a = [e for e in main_seq.iter() if e.tag.endswith("AudioTrack")]
    for i, e in enumerate(v):
        idx = e.attrib.get("Index"); vt.append((int(idx) if idx and idx.isdigit() else i, e))
    for i, e in enumerate(a):
        idx = e.attrib.get("Index"); at.append((int(idx) if idx and idx.isdigit() else i, e))
    vt.sort(key=lambda x: x[0]); at.sort(key=lambda x: x[0])
    return vt, at


def extract_rows(root: ET.Element, sequence_name: str, expand_nested: bool = True, include_parent: bool = False):
    by_id, by_uid = _collect_objects(root); seq_by_name = _discover_sequences(root)
    main = seq_by_name.get(sequence_name)
    if main is None:
        for el in root.iter():
            if el.tag.endswith("Sequence") and _first_text(el, "Name") == sequence_name:
                main = el; break
    if main is None: return []
    v, a = _collect_tracks_via_trackgroups(main, by_id, by_uid)
    if not v and not a: v, a = _collect_tracks_fallback(main)
    rows = []
    def add_items(track_elem: ET.Element, kind: str, track_no, off=0):
        for obj in track_elem.findall(".//TrackItem"):
            s = _ticks(obj, "Start"); e = _ticks(obj, "End")
            if s is None or e is None: continue
            name, nested = _resolve_name_and_nested(obj, by_id, by_uid, seq_by_name)
            row = {"Type": kind, "Track": track_no, "Name": name, "ClipType": _classify(name, kind)[0], "Source": _classify(name, kind)[1], "StartTicks": s+off, "EndTicks": e+off}
            if nested is not None and expand_nested:
                if include_parent: rows.append(row)
                nv, na = _collect_tracks_via_trackgroups(nested, by_id, by_uid)
                if not nv and not na: nv, na = _collect_tracks_fallback(nested)
                for _i, tr2 in nv: add_items(tr2, "Video", track_no if track_no is not None else 0, off=s+off)
                for _i, tr2 in na: add_items(tr2, "Audio", track_no if track_no is not None else 0, off=s+off)
            else:
                rows.append(row)
    for idx, tr in v: add_items(tr, "Video", idx if idx is not None else 0, 0)
    for idx, tr in a: add_items(tr, "Audio", idx if idx is not None else 0, 0)
    return rows
