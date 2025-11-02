# parser/timeline_flatten.py
# Robust timeline extraction with nested-sequence expansion for Premiere .prproj XML.

import os
import xml.etree.ElementTree as ET


def _first_text(elem: ET.Element | None, tag: str) -> str | None:
    """Return the first non-empty text for a given tag from elem or its descendants."""
    if elem is None:
        return None
    # Direct child first (slightly faster / more precise)
    for c in elem:
        if c.tag.endswith(tag):
            t = (c.text or "").strip()
            if t:
                return t
    # Fallback: deep search
    t = elem.find(".//" + tag)
    return (t.text or "").strip() if t is not None and t.text else None


def _collect_objects(root: ET.Element) -> tuple[dict[str, ET.Element], dict[str, ET.Element]]:
    """Build lookup maps by ObjectID / ObjectUID for quick dereferencing."""
    by_id, by_uid = {}, {}
    for e in root.iter():
        oid = e.attrib.get("ObjectID")
        if oid:
            by_id[oid] = e
        ouid = e.attrib.get("ObjectUID")
        if ouid:
            by_uid[ouid] = e
    return by_id, by_uid


def _discover_sequences(root: ET.Element) -> dict[str, ET.Element]:
    """Return dict: Name -> Sequence element for all sequences in the project."""
    seqs = {}
    for e in root.iter():
        if e.tag.endswith("Sequence"):
            nm = _first_text(e, "Name")
            if nm:
                seqs[nm] = e
    return seqs


def _ticks(el: ET.Element, tag: str) -> int | None:
    """Read a numeric tick value from a child/descendant tag (e.g., Start/End)."""
    t = el.find(f".//{tag}")
    if t is not None and t.text:
        s = t.text.strip()
        if s.lstrip("-+").isdigit():
            try:
                return int(s)
            except Exception:
                return None
    return None


def _classify(name: str | None, typ: str) -> tuple[str, str]:
    """Return (ClipType, Source) based on name and track type."""
    n = (name or "").lower()
    source = (
        "Artlist" if "artlist" in n
        else "Colourbox" if ("colourbox" in n or "colorbox" in n)
        else "Imago" if "imago" in n
        else ""
    )
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


def _basename_from_paths(elem: ET.Element) -> str | None:
    """Try common path tags and return basename as a last-resort name."""
    for tag in ("AbsolutePath", "RelativePath", "Path", "FilePath"):
        p = _first_text(elem, tag)
        if p:
            base = os.path.basename(p.replace("\\", "/"))
            if base:
                return base
    return None


def _deep_name_scan(elem: ET.Element) -> str | None:
    """Search for reasonable name-like fields on a clip/project item."""
    # Prefer explicit name-like tags first
    for tag in ("ClipName", "DisplayName", "Title", "Name"):
        t = elem.find(f".//{tag}")
        if t is not None and t.text and t.text.strip():
            return t.text.strip()
    # Then any descendant whose tag endswith 'Name'
    for d in elem.iter():
        if d.tag.endswith("Name") and d.text and d.text.strip():
            return d.text.strip()
    return None


def _find_sequence_reference(elem: ET.Element, by_id: dict, by_uid: dict) -> ET.Element | None:
    """Return a Sequence element if elem (or its descendants) reference one via ObjectRef/URef."""
    # Direct attributes on this element
    for key in ("ObjectRef", "ObjectURef"):
        ref = elem.attrib.get(key)
        if ref:
            target = by_id.get(ref) or by_uid.get(ref)
            if target is not None and target.tag.endswith("Sequence"):
                return target
    # Any descendant may carry the ref
    for sub in elem.iter():
        for key in ("ObjectRef", "ObjectURef"):
            ref = sub.attrib.get(key)
            if ref:
                target = by_id.get(ref) or by_uid.get(ref)
                if target is not None and target.tag.endswith("Sequence"):
                    return target
    return None


def _resolve_name_and_nested(
    obj: ET.Element,
    by_id: dict[str, ET.Element],
    by_uid: dict[str, ET.Element],
    seq_by_name: dict[str, ET.Element],
) -> tuple[str | None, ET.Element | None]:
    """
    Return (display_name, nested_sequence_or_None) for a TrackItem target.

    Order of detection:
      A) SubClip -> Sequence
      B) Any ObjectRef/URef chain -> Sequence
      C) Fallback: if the TrackItem display name equals a known Sequence Name
      D) Finally, derive a name from the target or file path
    """
    # Start with TrackItem's own Name
    name = _first_text(obj, "Name")
    nested_seq = None

    # A) Standard SubClip reference
    seq_ref = obj.find(".//SubClip")
    if seq_ref is not None:
        sr = seq_ref.attrib.get("ObjectRef") or seq_ref.attrib.get("ObjectURef")
        target = by_id.get(sr) or by_uid.get(sr)
        if target is not None:
            if target.tag.endswith("Sequence"):
                nested_seq = target
                name = name or _first_text(nested_seq, "Name")
            else:
                # ProjectItem/Clip: try to get a descriptive name
                name = name or _first_text(target, "Name") or _deep_name_scan(target) or _basename_from_paths(target)

    # B) Any other ObjectRef/URef chain → Sequence
    if nested_seq is None:
        seq = _find_sequence_reference(obj, by_id, by_uid)
        if seq is not None:
            nested_seq = seq
            name = name or _first_text(nested_seq, "Name")

    # C) Fallback by name: if TrackItem name equals a known sequence, treat as nested
    if nested_seq is None and name and name in seq_by_name:
        nested_seq = seq_by_name[name]

    # D) Ensure we have some display name
    if not name:
        name = _deep_name_scan(obj) or _basename_from_paths(obj)

    return name, nested_seq


def extract_rows(
    root: ET.Element,
    sequence_name: str,
    expand_nested: bool = True,
    include_parent: bool = False,
) -> list[dict]:
    """
    Extract timeline rows from `sequence_name` within `root`.

    Parameters
    ----------
    root : ET.Element
        Project XML root.
    sequence_name : str
        Name of the main sequence to export from.
    expand_nested : bool
        If True, flatten nested sequences so child clips appear inline in the main timeline.
        If False, keep a single parent row for the nested sequence.
    include_parent : bool
        When expanding, also include a parent "header" row for the nested sequence.

    Returns
    -------
    list[dict]
        Each dict includes:
          - 'Type'      : 'Video' or 'Audio'
          - 'Track'     : track index (0-based; app can convert to 1-based)
          - 'Name'      : clip/sequence display name
          - 'ClipType'  : 'video' | 'image' | 'audio' | 'graphic'
          - 'Source'    : 'Artlist' | 'Colourbox' | 'Imago' | ''
          - 'StartTicks': start in Premiere ticks, relative to the main sequence
          - 'EndTicks'  : end in Premiere ticks, relative to the main sequence
    """
    by_id, by_uid = _collect_objects(root)
    seq_by_name = _discover_sequences(root)

    # Find main sequence by name (map lookup, then fallback scan)
    main_seq = seq_by_name.get(sequence_name)
    if main_seq is None:
        for elem in root.iter():
            if elem.tag.endswith("Sequence") and _first_text(elem, "Name") == sequence_name:
                main_seq = elem
                break
    if main_seq is None:
        return []

    # Resolve track groups (look in both First and Second slots)
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

    def tracks_from_groups(groups: list[ET.Element]) -> list[tuple[int | None, ET.Element]]:
        out = []
        for g in groups:
            for tr in g.findall(".//Tracks/Track"):
                idx = tr.attrib.get("Index")
                guid = tr.attrib.get("ObjectURef") or tr.attrib.get("ObjectUID")
                track_obj = by_uid.get(guid)
                if track_obj is not None:
                    out.append((int(idx) if idx and idx.isdigit() else None, track_obj))
        # Keep natural order by numeric index; unknowns at the end
        out.sort(key=lambda x: (9999 if x[0] is None else x[0]))
        return out

    video_tracks = tracks_from_groups(video_groups)
    audio_tracks = tracks_from_groups(audio_groups)

    rows: list[dict] = []

    def add_items(track_elem: ET.Element, kind: str, track_no: int | None, offset_ticks: int = 0):
        """
        Append rows for all TrackItems under `track_elem`.

        For nested sequences:
        - If expand_nested=True: recurse into the nested sequence and add its child clips at
          (start + offset_ticks). Track number is preserved from the parent.
        - If expand_nested=False: only a parent row is emitted for the nested sequence.
        """
        # Be permissive: gather ANY descendant TrackItem
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

                # Recurse into this nested sequence; preserve parent track number
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

                for _idx2, tr2 in tracks_from_groups(n_vid_groups):
                    add_items(tr2, "Video", track_no if track_no is not None else 0, offset_ticks=start + offset_ticks)
                for _idx2, tr2 in tracks_from_groups(n_aud_groups):
                    add_items(tr2, "Audio", track_no if track_no is not None else 0, offset_ticks=start + offset_ticks)
            else:
                # Keep as a single row (either non-nested, or nested but not expanding)
                rows.append(parent_row)

    # Walk main-sequence tracks
    for idx, tr in video_tracks:
        add_items(tr, "Video", idx if idx is not None else 0, 0)
    for idx, tr in audio_tracks:
        add_items(tr, "Audio", idx if idx is not None else 0, 0)

    return rows