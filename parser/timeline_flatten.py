# parser/timeline_flatten.py
# -----------------------------------------------------------------------------
# Purpose
#   Robustly flatten a Premiere Pro timeline into exportable rows.
#
# What changed (Nov 2, 2025)
#   1) Reads tracks the way your test project stores them:
#        Sequence
#          └─ TrackGroups
#               └─ TrackGroup → First/Second → VideoTrackGroup/AudioTrackGroup
#                    └─ TrackGroup
#                         └─ Tracks
#                              ├─ Track Index="n" ObjectURef="...VideoClipTrack UID..."
#                              └─ Track Index="m" ObjectURef="...AudioClipTrack UID..."
#      (i.e., ClipTrack-based, not the older VideoTrack/AudioTrack XML nodes)
#
#   2) Follows each ClipTrack's TrackItem stub:
#        TrackItem (stub with ObjectRef) → VideoClipTrackItem/AudioClipTrackItem
#            └─ ClipTrackItem/TrackItem/Start + End  (timings live here)
#
#   3) Keeps a SAFETY NET when expanding nested sequences:
#        If a nested expansion adds 0 children, we keep the parent row
#        even when include_parent == False (prevents “No timeline items found…”)
# -----------------------------------------------------------------------------

from typing import List, Tuple, Optional, Dict, Iterable
import xml.etree.ElementTree as ET


# ---------- small utilities ---------------------------------------------------

def _build_id_maps(root: ET.Element) -> Tuple[Dict[str, ET.Element], Dict[str, ET.Element]]:
    """Index all nodes by ObjectID and ObjectUID for fast deref."""
    by_id = {}
    by_uid = {}
    for el in root.iter():
        oid = el.get("ObjectID")
        if oid:
            by_id[oid] = el
        uid = el.get("ObjectUID")
        if uid:
            by_uid[uid] = el
    return by_id, by_uid


def _find_sequence_by_name(root: ET.Element, sequence_name: str) -> Optional[ET.Element]:
    """Return the <Sequence> element whose <Name> equals sequence_name."""
    for el in root.iter():
        if el.tag.endswith("Sequence"):
            nm = el.findtext("Name")
            if nm == sequence_name:
                return el
    return None


def _classify(name: Optional[str], kind: str) -> Tuple[str, str]:
    """
    Very light clip classifier used by the UI.
    Returns (ClipType, SourceString).
    """
    if kind == "Audio":
        return ("Audio", name or "")
    return ("Video", name or "")


# ---------- core: reading ClipTracks from a Sequence --------------------------

def _collect_cliptracks_from_sequence(
    seq_el: ET.Element, by_id: Dict[str, ET.Element], by_uid: Dict[str, ET.Element]
) -> Tuple[List[Tuple[int, ET.Element]], List[Tuple[int, ET.Element]]]:
    """
    Discover ClipTracks for the sequence through its TrackGroups.

    Returns:
        (video_cliptracks, audio_cliptracks)
        where each item is (track_index, cliptrack_element)
    """
    videos: List[Tuple[int, ET.Element]] = []
    audios: List[Tuple[int, ET.Element]] = []

    tg = seq_el.find("TrackGroups")
    if tg is None:
        return videos, audios

    def _resolve_group(tg_el: ET.Element) -> Optional[ET.Element]:
        # Obtain referenced top-level VideoTrackGroup/AudioTrackGroup
        ref = None
        for slot in ("First", "Second"):
            sl = tg_el.find(slot)
            if sl is not None:
                ref = sl.get("ObjectRef") or sl.get("ObjectURef") or ref
        return (by_id.get(ref) or by_uid.get(ref)) if ref else None

    for tgg in tg.findall("TrackGroup"):
        grp = _resolve_group(tgg)
        if grp is None:
            continue
        # Each grp (VideoTrackGroup/AudioTrackGroup) embeds an inner TrackGroup
        inner = grp.find("TrackGroup")
        if inner is None:
            continue
        tracks = inner.find("Tracks")
        if tracks is None:
            continue

        for tr in tracks.findall("Track"):
            idx = int(tr.get("Index", "0"))
            r = tr.get("ObjectRef") or tr.get("ObjectURef")
            if not r:
                continue
            target = by_id.get(r) or by_uid.get(r)
            if target is None:
                continue
            if target.tag == "VideoClipTrack":
                videos.append((idx, target))
            elif target.tag == "AudioClipTrack":
                audios.append((idx, target))
    return videos, audios


def _iter_items_on_cliptrack(
    cliptrack_el: ET.Element, by_id: Dict[str, ET.Element]
) -> Iterable[Tuple[int, int, ET.Element]]:
    """
    Iterate items on a ClipTrack, yielding (start_ticks, end_ticks, item_object).
    Timings live at: ClipTrackItem/TrackItem/Start + End
    """
    ti_root = cliptrack_el.find("ClipTrack/ClipItems/TrackItems")
    if ti_root is None:
        return
    for stub in ti_root.findall("TrackItem"):
        ref = stub.get("ObjectRef") or stub.get("ObjectURef")
        if not ref:
            continue
        item = by_id.get(ref)  # VideoClipTrackItem / AudioClipTrackItem
        if item is None:
            continue
        cti = item.find("ClipTrackItem")
        inner_ti = cti.find("TrackItem") if cti is not None else None
        if inner_ti is None:
            continue
        s = inner_ti.findtext("Start")
        e = inner_ti.findtext("End")
        if s is None or e is None:
            continue
        yield int(s), int(e), item


# ---------- optional: nested sequence support --------------------------------

def _maybe_get_nested_sequence(
    item_obj: ET.Element, by_id: Dict[str, ET.Element], by_uid: Dict[str, ET.Element]
) -> Optional[ET.Element]:
    """
    Best-effort: if this ClipTrackItem effectively references a nested Sequence,
    return that Sequence element. This is intentionally conservative—if we don't
    find a clear Sequence, we return None.
    """
    cti = item_obj.find("ClipTrackItem")
    if cti is None:
        return None

    # 1) Sometimes there is a numeric <Sequence> id somewhere under the item.
    #    Try to interpret it as the sequence's <ID> value.
    for el in cti.iter():
        if el.tag.endswith("Sequence") and el.text and el.text.isdigit():
            target_id_text = el.text
            # Find a Sequence node whose <ID> matches target_id_text
            for seq in by_id.values():
                if seq.tag.endswith("Sequence") and (seq.findtext("ID") == target_id_text):
                    return seq
            # Also scan by tree if not present in by_id map:
            parent = cti
            root = parent
            while getattr(root, "getparent", None) and root.getparent() is not None:
                root = root.getparent()

    # 2) If there is a ComponentOwner reference, and it resolves to a Sequence
    comp = cti.find("ComponentOwner")
    if comp is not None:
        ref = comp.get("ObjectRef") or comp.get("ObjectURef")
        if ref:
            owner = by_id.get(ref) or by_uid.get(ref)
            if owner is not None and owner.tag.endswith("Sequence"):
                return owner

    return None


# ---------- public API --------------------------------------------------------

def extract_rows(
    root: ET.Element,
    sequence_name: str,
    *,
    expand_nested: bool = True,
    include_parent: bool = False
) -> List[Dict[str, object]]:
    """
    Build a flat list of timeline rows for the given sequence.

    Row keys:
        Type:        "Video" | "Audio"
        Track:       int (track index)
        Name:        str (may be "")
        ClipType:    str (coarse classifier)
        Source:      str (coarse classifier complement)
        StartTicks:  int
        EndTicks:    int
    """
    rows: List[Dict[str, object]] = []
    by_id, by_uid = _build_id_maps(root)

    seq_el = _find_sequence_by_name(root, sequence_name)
    if seq_el is None:
        return rows

    v_cliptracks, a_cliptracks = _collect_cliptracks_from_sequence(seq_el, by_id, by_uid)

    def add_from_cliptrack(kind: str, track_no: int, cliptrack_el: ET.Element, offset_ticks: int = 0) -> None:
        for start, end, item in _iter_items_on_cliptrack(cliptrack_el, by_id):
            # (Optional) resolve a display name; your test project often leaves it empty near timings.
            display_name: Optional[str] = None
            clip_type, src = _classify(display_name, kind)

            nested_seq = _maybe_get_nested_sequence(item, by_id, by_uid)
            if nested_seq is not None and expand_nested:
                # Parent row datastructure
                parent_row = {
                    "Type": kind,
                    "Track": track_no,
                    "Name": display_name or "",
                    "ClipType": clip_type,
                    "Source": src,
                    "StartTicks": start + offset_ticks,
                    "EndTicks": end + offset_ticks,
                }
                before = len(rows)
                if include_parent:
                    rows.append(parent_row)

                # Recurse into the nested sequence; keep the original track index
                nv, na = _collect_cliptracks_from_sequence(nested_seq, by_id, by_uid)
                for _idx2, tr2 in nv:
                    add_from_cliptrack("Video", track_no if track_no is not None else 0, tr2, offset_ticks + start)
                for _idx2, tr2 in na:
                    add_from_cliptrack("Audio", track_no if track_no is not None else 0, tr2, offset_ticks + start)

                # SAFETY NET: if expansion produced no children, keep the parent (even when include_parent=False)
                if len(rows) == before and not include_parent:
                    rows.append(parent_row)
            else:
                rows.append({
                    "Type": kind,
                    "Track": track_no,
                    "Name": display_name or "",
                    "ClipType": clip_type,
                    "Source": src,
                    "StartTicks": start + offset_ticks,
                    "EndTicks": end + offset_ticks,
                })

    # main sequence: enumerate video then audio
    for idx, tr in sorted(v_cliptracks, key=lambda x: x[0]):
        add_from_cliptrack("Video", idx, tr, 0)
    for idx, tr in sorted(a_cliptracks, key=lambda x: x[0]):
        add_from_cliptrack("Audio", idx, tr, 0)

    return rows
