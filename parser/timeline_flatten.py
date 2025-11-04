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
        # Collect by ObjectID (used for TrackGroups/Sequence linking)
        oid = e.attrib.get("ObjectID")
        if oid: by_id[oid] = e
        # Collect by ObjectUID (used for MasterClip linking, etc.)
        ouid = e.attrib.get("ObjectUID")
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
    mr = obj.find(".//MasterClip") 
    
    # Logic for SubClip reference
    if sr is not None:
        ref = sr.attrib.get("ObjectRef") or sr.attrib.get("ObjectURef")
        tgt = by_id.get(ref) or by_uid.get(ref)
        if tgt is not None:
            if tgt.tag.endswith("Sequence"):
                nested = tgt; name = name or _first_text(nested, "Name")
            else:
                name = name or _first_text(tgt, "Name") or _deep_name_scan(tgt) or _basename_from_paths(tgt)
    
    # Logic for MasterClip reference (handles standard media clips)
    elif mr is not None:
        ref = mr.attrib.get("ObjectRef") or mr.attrib.get("ObjectURef")
        tgt = by_id.get(ref) or by_uid.get(ref)
        if tgt is not None:
            if tgt.tag.endswith("Sequence"):
                nested = tgt; name = name or _first_text(nested, "Name")
            else:
                name = name or _first_text(tgt, "Name") or _deep_name_scan(tgt) or _basename_from_paths(tgt)

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
    
    # 1. Find all TrackGroup references inside the Sequence element
    for tg in main_seq.findall(".//TrackGroups/TrackGroup"):
        ref = None
        # Check for direct ObjectRef/ObjectURef on the TrackGroup element itself (common case)
        ref = tg.attrib.get("ObjectRef") or tg.attrib.get("ObjectURef")
        
        # If no direct reference, check the First/Second slots (for older/different XML format)
        if not ref:
            for slot in ("First", "Second"):
                sl = tg.find(slot)
                if sl is not None:
                    ref = sl.attrib.get("ObjectRef") or sl.attrib.get("ObjectURef")
                    if ref:
                        break
        
        # 2. Resolve the reference to the actual TrackGroup object
        obj = (by_id.get(ref) or by_uid.get(ref)) if ref else None
        
        if obj is None:
            continue
        
        # 3. Classify the resolved object
        if obj.tag.endswith("VideoTrackGroup"):
            vgs.append(obj)
        elif obj.tag.endswith("AudioTrackGroup"):
            ags.append(obj)
    
    # 4. Extract individual Tracks from the found TrackGroup objects
    def tracks(groups):
        out = []
        for g in groups:
            # Tracks are found as children of the <Tracks> tag within the TrackGroup object
            for tr in g.findall(".//Tracks/Track"):
                # Track element itself holds the index and reference to the full track data
                idx = tr.attrib.get("Index")
                guid = tr.attrib.get("ObjectURef") or tr.attrib.get("ObjectUID") or tr.attrib.get("ObjectRef")
                
                # Resolve the reference to the full VideoTrack or AudioTrack object
                to = by_uid.get(guid) or by_id.get(guid)
                
                if to is not None:
                    out.append((int(idx) if idx and idx.isdigit() else None, to))
        out.sort(key=lambda x: (9999 if x[0] is None else x[0]))
        return out
        
    return tracks(vgs), tracks(ags)


def _collect_tracks_fallback(main_seq: ET.Element):
    vt, at = [], []
    # Fallback only looks for tracks directly embedded in the sequence element, which is rare.
    v = [e for e in main_seq.iter() if e.tag.endswith("VideoTrack")]
    a = [e for e in main_seq.iter() if e.tag.endswith("AudioTrack")]
    for i, e in enumerate(v):
        idx = e.attrib.get("Index"); vt.append((int(idx) if idx and idx.isdigit() else i, e))
    for i, e in enumerate(a):
        idx = e.attrib.get("Index"); at.append((int(idx) if idx and idx.isdigit() else i, e))
    vt.sort(key=lambda x: x[0]); at.sort(key=lambda x: x[0])
    return vt, at
    
def _collect_tracks_by_declared_uids(main_seq, by_uid):
    """
    Fallback: derive tracks by reading the sequence's declared Track@ObjectURef GUIDs
    and mapping those to top-level VideoClipTrack/AudioClipTrack via by_uid.
    """
    vt, at = [], []
    for tr in main_seq.findall(".//TrackGroups//Track"):
        uid = tr.attrib.get("ObjectURef") or tr.attrib.get("ObjectUID")
        if not uid:
            continue
        elem = by_uid.get(uid)
        if elem is None:
            continue
        idx_attr = elem.attrib.get("Index")
        idx = int(idx_attr) if idx_attr and idx_attr.isdigit() else None
        if elem.tag.endswith("VideoClipTrack"):
            vt.append((idx, elem))
        elif elem.tag.endswith("AudioClipTrack"):
            at.append((idx, elem))
    vt.sort(key=lambda x: (9999 if x[0] is None else x[0]))
    at.sort(key=lambda x: (9999 if x[0] is None else x[0]))
    return vt, at

def extract_rows(root: ET.Element, sequence_name: str, expand_nested: bool = True, include_parent: bool = False):
    by_id, by_uid = _collect_objects(root); seq_by_name = _discover_sequences(root)
    main = seq_by_name.get(sequence_name)
    if main is None:
        for el in root.iter():
            if el.tag.endswith("Sequence") and _first_text(el, "Name") == sequence_name:
                main = el; break
    if main is None: return []
    
    # Use the robust track collection logic
    v, a = _collect_tracks_via_trackgroups(main, by_id, by_uid)
    if not v and not a:
    v, a = _collect_tracks_by_declared_uids(main, by_uid)
    if not v and not a:
    v, a = _collect_tracks_fallback(main)
    
    rows = []

def add_items(track_elem, kind, track_no, off=0):
    """
    Traverse concrete clip items (VideoClipTrackItem/AudioClipTrackItem) instead of the
    generic TrackItems/TrackItem stubs. The concrete items contain both timing
    (ClipTrackItem/TrackItem/Start, End) and identity (SubClip/Name), which is reliable
    across Premiere schemas.
    """
    item_tag = "VideoClipTrackItem" if kind == "Video" else "AudioClipTrackItem"
    for item in track_elem.findall(f".//{item_tag}"):
        ti = item.find(".//ClipTrackItem/TrackItem")
        if ti is None:
            continue
        s = _ticks(ti, "Start"); e = _ticks(ti, "End")
        if s is None or e is None:
            continue
        name, nested = _resolve_name_and_nested(item, by_id, by_uid, seq_by_name)
        row = {
            "Type": kind,
            "Track": track_no,
            "Name": name,
            "ClipType": _classify(name, kind)[0],
            "Source": _classify(name, kind)[1],
            "StartTicks": s + off,
            "EndTicks": e + off
        }
        if nested is not None and expand_nested:
            if include_parent:
                rows.append(row)
            nv, na = _collect_tracks_via_trackgroups(nested, by_id, by_uid)
            if not nv and not na:
                nv, na = _collect_tracks_fallback(nested)
            for _i, tr2 in nv:
                add_items(tr2, "Video", track_no if track_no is not None else 0, off=s + off)
            for _i, tr2 in na:
                add_items(tr2, "Audio", track_no if track_no is not None else 0, off=s + off)
        else:
            rows.append(row)
                
    for idx, tr in v: add_items(tr, "Video", idx if idx is not None else 0, 0)
    for idx, tr in a: add_items(tr, "Audio", idx if idx is not None else 0, 0)
    
    return rows
