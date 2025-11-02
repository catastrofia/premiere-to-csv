import streamlit as st

def _first_text(elem, tag):
    if elem is None:
        return None
    for c in elem:
        if c.tag.endswith(tag):
            return (c.text or "").strip()
    t = elem.find(".//" + tag)
    return (t.text or "").strip() if t is not None and t.text else None

def _collect_objects(root):
    by_id, by_uid = {}, {}
    for e in root.iter():
        oid = e.attrib.get("ObjectID")
        if oid: by_id[oid] = e
        ouid = e.attrib.get("ObjectUID")
        if ouid: by_uid[ouid] = e
    return by_id, by_uid

def _ticks(el, tag):
    t = el.find(f".//{tag}")
    if t is not None and t.text:
        s = t.text.strip()
        return int(s) if s.isdigit() else None
    return None

def _classify(name, typ):
    n = (name or "").lower()
    source = "Artlist" if "artlist" in n else ("Colourbox" if ("colourbox" in n or "colorbox" in n) else ("Imago" if "imago" in n else ""))
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

@st.cache_data(show_spinner=True)
def extract_rows(root, sequence_name: str, include_nested: bool = True, include_parent: bool = False):
    """Return list of dict rows with StartTicks/EndTicks in main-sequence time."""
    by_id, by_uid = _collect_objects(root)

    # Find main sequence by name
    main_seq = None
    for elem in root.iter():
        if elem.tag.endswith("Sequence"):
            if _first_text(elem, "Name") == sequence_name:
                main_seq = elem
                break
    if main_seq is None:
        return []

    # Resolve track groups
    video_groups, audio_groups = [], []
    for tg in main_seq.findall(".//TrackGroups/TrackGroup"):
        sec = tg.find("Second")
        ref = sec.attrib.get("ObjectRef") if sec is not None else None
        obj = by_id.get(ref) if ref else None
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
        for ti in track_elem.findall(".//ClipTrack/ClipItems/TrackItems/TrackItem"):
            ref = ti.attrib.get("ObjectRef")
            obj = by_id.get(ref)
            if obj is None:
                continue
            start = _ticks(obj, "Start")
            end = _ticks(obj, "End")
            if start is None or end is None:
                continue
            name = _first_text(obj, "Name")

            # Check for nested sequence
            seq_ref = obj.find(".//SubClip")
            nested_seq = None
            if seq_ref is not None:
                sr = seq_ref.attrib.get("ObjectRef") or seq_ref.attrib.get("ObjectURef")
                nested_seq = by_id.get(sr) or by_uid.get(sr)
                if nested_seq is not None and not nested_seq.tag.endswith("Sequence"):
                    nested_seq = None

            parent_row = {
                "Type": kind,
                "Track": track_no,
                "Name": name,
                "ClipType": _classify(name, kind)[0],
                "Source": _classify(name, kind)[1],
                "StartTicks": start + offset_ticks,
                "EndTicks": end + offset_ticks,
            }

            if nested_seq is not None and include_nested:
                if include_parent:
                    rows.append(parent_row)
                # Recurse into nested sequence, preserving track number and adding offset
                n_vid_groups, n_aud_groups = [], []
                for tg2 in nested_seq.findall(".//TrackGroups/TrackGroup"):
                    sec2 = tg2.find("Second")
                    rf2 = sec2.attrib.get("ObjectRef") if sec2 is not None else None
                    o2 = by_id.get(rf2) if rf2 else None
                    if o2 is None: 
                        continue
                    if o2.tag.endswith("VideoTrackGroup"): n_vid_groups.append(o2)
                    elif o2.tag.endswith("AudioTrackGroup"): n_aud_groups.append(o2)
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
