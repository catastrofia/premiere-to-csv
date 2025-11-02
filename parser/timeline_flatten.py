import streamlit as st
import xml.etree.ElementTree as ET
import os


def _first_text(elem, tag):
    if elem is None:
        return None
    for c in elem:
        if c.tag.endswith(tag):
            t = (c.text or "").strip()
            if t:
                return t
    t = elem.find(".//" + tag)
    return (t.text or "").strip() if t is not None and t.text else None


def _collect_objects(root):
    by_id, by_uid = {}, {}
    for e in root.iter():
        oid = e.attrib.get("ObjectID")
        if oid:
            by_id[oid] = e
        ouid = e.attrib.get("ObjectUID")
        if ouid:
            by_uid[ouid] = e
    return by_id, by_uid


def _discover_sequences(root):
    """Return dict: Name -> Sequence element."""
    seqs = {}
    for e in root.iter():
        if e.tag.endswith("Sequence"):
            nm = _first_text(e, "Name")
            if nm:
                seqs[nm] = e
    return seqs


def _ticks(el, tag):
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
    source = "Artlist" if "artlist" in n else ("Colourbox" if ("colourbox" in n or "colorbox" in n) else ("Imago" if "imago" in n else ""))
    if typ == "Audio":
        c = "audio"
    else:
        if any(n.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp", ".webp")):
            c = "image"
        elif any(n.endswith(ext) for ext in (".mp4", ".mov", ".m4v", ".avi", ".mxf", ".mkv")):
            c = "video"
        elif (name or "").lower() in ("graphic", "white") or (name and "." not in name):
            c = "graphic"
        else:
            c = "video"
    return c, source


def _basename_from_paths(elem):
    for tag in ("AbsolutePath", "RelativePath", "Path", "FilePath"):
        p = _first_text(elem, tag)
        if p:
            base = os.path.basename(p.replace("\\", "/"))
            if base:
                return base
    return None


def _deep_name_scan(elem):
    # Prefer explicit name-like tags first
    preferred = ("ClipName", "DisplayName", "Title", "Name")
    for tag in preferred:
        t = elem.find(f".//{tag}")
        if t is not None and t.text and t.text.strip():
            return t.text.strip()
    # Then any descendant whose tag endswith 'Name'
    for d in elem.iter():
        if d.tag.endswith("Name") and d.text and d.text.strip():
            return d.text.strip()
    return None


def _find_sequence_reference(elem, by_id, by_uid):
    """Return a Sequence element if elem (or its descendants) reference one."""
    # Direct attributes
    for key in ("ObjectRef", "ObjectURef"):
        ref = elem.attrib.get(key)
        if ref:
            target = by_id.get(ref) or by_uid.get(ref)
            if target is not None and target.tag.endswith("Sequence"):
                return target
    # Descendants
    for sub in elem.iter():
        for key in ("ObjectRef", "ObjectURef"):
            ref = sub.attrib.get(key)
            if ref:
                target = by_id.get(ref) or by_uid.get(ref)
                if target is not None and target.tag.endswith("Sequence"):
                    return target
    return None


def _resolve_name_and_nested(obj, by_id, by_uid, seq_by_name):
    """
    Return (name, nested_seq_or_None) for a TrackItem's referenced object.
    Follows SubClip, any ObjectRef/URef chain, and finally falls back by name.
    """
    name = _first_text(obj, "Name")

    # A) Standard SubClip reference
    nested_seq = None
    seq_ref = obj.find(".//SubClip")
    if seq_ref is not None:
        sr = seq_ref.attrib.get("ObjectRef") or seq_ref.attrib.get("ObjectURef")
        target = by_id.get(sr) or by_uid.get(sr)
        if target is not None:
            if target.tag.endswith("Sequence"):
                nested_seq = target
                name = name or _first_text(nested_seq, "Name")
            else:
                name = name or _first_text(target, "Name") or _deep_name_scan(target) or _basename_from_paths(target)

    # B) If still no nested seq: any other ObjectRef/URef chain → Sequence
    if nested_seq is None:
        seq = _find_sequence_reference(obj, by_id, by_uid)
        if seq is not None:
            nested_seq = seq
            name = name or _first_text(nested_seq, "Name")

    # C) Fallback by name equals a known sequence (e.g., "Emissions Normal Copy 01")
    if nested_seq is None and name and name in seq_by_name:
        nested_seq = seq_by_name[name]

    # D) Ensure we have some name
    if not name:
        name = _deep_name_scan(obj) or _basename_from_paths(obj)

    return name, nested_seq


def extract_rows(root, sequence_name: str, expand_nested: bool = True, include_parent: bool = False):
    """
    Return list of dict rows with StartTicks/EndTicks in main-sequence time.

    expand_nested=True  → flatten all nested sequences (children shown)
    expand_nested=False → keep a single parent row for the nested sequence
    include_parent=True → when expanding, also keep the parent row as a header
    """
    by_id, by_uid = _collect_objects(root)
    seq_by_name = _discover_sequences(root)

    # Find main sequence by name
    main_seq = seq_by_name.get(sequence_name)
    if main_seq is None:
        for elem in root.iter():
            if elem.tag.endswith("Sequence") and _first_text(elem, "Name") == sequence_name:
                main_seq = elem
                break
    if main_seq is None:
        return []

    # Resolve track groups (look in both First and Second)
    video_groups, audio_groups = [], []
    for tg in main_seq.findall(".//TrackGroups/TrackGroup"):
        ref = None
        for slot in ("First", "Second"):
            slot_el = tg.find(slot)
            if slot_el is not None:
                ref = slot_el.attrib.get("ObjectRef") or slot_el.attrib.get("ObjectURef")
                if ref:
                    break
        obj = (by_id.get(ref) or by_uid.get(ref)) if ref else None
        if obj is None:
            continue
        if obj.tag.endswith("VideoTrackGroup"):
            video_groups.append(obj)
        elif obj.tag.endswith("AudioTrackGroup"):
            audio_groups.append(obj)

    def tracks_from_groups(groups):
        out = []
        for g in groups:
            for tr in g.findall(".//Tracks/Track"):
                idx = tr.attrib.get("Index")
                guid = tr.attrib.get("ObjectURef") or tr.attrib.get("ObjectUID")
                track_obj = by_uid.get(guid)
                if track_obj is not None:
                    out.append((int(idx) if idx and idx.isdigit() else None, track_obj))
        out.sort(key=lambda x: (9999 if x[0] is None else x[0]))
        return out

    video_tracks = tracks_from_groups(video_groups)
    audio_tracks = tracks_from_groups(audio_groups)

    rows = []

    def add_items(track_elem, kind, track_no, offset_ticks=0):
        # Be permissive: collect any descendant TrackItem
        for obj in track_elem.findall(".//TrackItem"):
            start = _ticks(obj, "Start")
            end = _ticks(obj, "End")
            if start is None or end is None:
                continue

            name, nested_seq = _resolve_name_and_nested(obj, by_id, by_uid, seq_by_name)

            parent_row = {
                "Type": kind,
                "Track": track_no,
                "Name": name,
                "ClipType": _classify(name, kind)[0],
                "Source": _classify(name, kind)[1],
                "StartTicks": start + offset_ticks,
                "EndTicks": end + offset_ticks,
            }

            if nested_seq is not None and expand_nested:
                if include_parent:
                    rows.append(parent_row)
                # Recurse into nested sequence, preserving parent track number
                n_vid_groups, n_aud_groups = [], []
                for tg2 in nested_seq.findall(".//TrackGroups/TrackGroup"):
                    ref2 = None
                    for slot in ("First", "Second"):
                        slot2 = tg2.find(slot)
                        if slot2 is not None:
                            ref2 = slot2.attrib.get("ObjectRef") or slot2.attrib.get("ObjectURef")
                            if ref2:
                                break
                    o2 = (by_id.get(ref2) or by_uid.get(ref2)) if ref2 else None
                    if o2 is None:
                        continue
                    if o2.tag.endswith("VideoTrackGroup"):
                        n_vid_groups.append(o2)
                    elif o2.tag.endswith("AudioTrackGroup"):
                        n_aud_groups.append(o2)
                for idx2, tr2 in tracks_from_groups(n_vid_groups):
                    add_items(tr2, "Video", track_no, offset_ticks=start + offset_ticks)
                for idx2, tr2 in tracks_from_groups(n_aud_groups):
                    add_items(tr2, "Audio", track_no, offset_ticks=start + offset_ticks)
            else:
                rows.append(parent_row)

    for idx, tr in video_tracks:
        add_items(tr, "Video", idx or 0, 0)
    for idx, tr in audio_tracks:
        add_items(tr, "Audio", idx or 0, 0)

    return rows