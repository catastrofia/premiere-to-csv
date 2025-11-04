# app.py
# Premiere → CSV (Flattened, 24 fps) — Streamlit app
# - Upload .prproj or gzipped XML (.txt)
# - Pick sequence, expand nested sequences into main timeline
# - Export CSV with: Type, Track, Name, Title, ClipType, Source, StockID, StartTC, EndTC

import os
import re
import pandas as pd
import streamlit as st
import xml.etree.ElementTree as ET

# Import parser utilities
from parser.timecode import ticks_to_tc_24fps
from parser.prproj_reader import load_xml_tree
# Try to import the non-cached sequence finder first; fallback to the cached/discover version.
try:
    from parser.prproj_reader import find_sequences as _get_sequences
except Exception:
    try:
        from parser.prproj_reader import discover_sequences as _get_sequences
    except Exception:
        _get_sequences = None  # minimal fallback implemented below

from parser.timeline_flatten import extract_rows

# --- Version Tracking ---
_VERSION = "v0.2.2 (Alpha)" # Version bumped to 0.2.2 for critical error-handling fix
# ------------------------

# -------------------- PAGE CONFIG --------------------
st.set_page_config(page_title="Premiere → CSV (Flattened, 24 fps)", layout="wide")
st.title("Premiere → CSV (Flattened, 24 fps)")

st.markdown(
    "Upload a Premiere Pro project (`.prproj`) or gzipped XML (`.txt`). "
    "Files are processed **in-memory** and not stored on the server."
)

with st.sidebar:
    if st.button("🔄 Clear cache (dev)"):
        st.cache_data.clear()
        st.success("Cleared Streamlit cache. Re-upload your file.")
    
    # --- Version Display ---
    st.markdown("---")
    st.caption(f"App Version: **{_VERSION}**")
    # -----------------------

# -------------------- FILE UPLOAD --------------------
uploaded = st.file_uploader(
    "Upload .prproj or gzipped XML (.txt)",
    type=["prproj", "txt"],
    accept_multiple_files=False
)

# Initialize variables for debugging
xml_root = None
seq_map = {}
rows_list = []
main_seq = ""

if not uploaded:
    st.info("Drop a file above to start.")
    st.stop()

# Parse XML tree (cached inside load_xml_tree)
try:
    with st.spinner("Decompressing and parsing XML tree..."):
        xml_root = load_xml_tree(uploaded.getvalue())
except Exception as e:
    st.error("Could not parse the uploaded project file.")
    st.exception(e)
    st.stop()

# -------------------- SEQUENCE DISCOVERY --------------------
def _fallback_discover_sequences(root):
    """Minimal fallback if prproj_reader has no sequence finder."""
    seqs = {}
    try:
        for elem in root.iter():
            if elem.tag.endswith("Sequence"):
                # try direct child Name, then deep
                name = None
                for c in elem:
                    if c.tag.endswith("Name") and c.text and c.text.strip():
                        name = c.text.strip()
                        break
                if not name:
                    n = elem.find(".//Name")
                    if n is not None and n.text and n.text.strip():
                        name = n.text.strip()
                if name:
                    seqs[name] = elem
    except Exception:
        pass
    return seqs

try:
    with st.spinner("Finding sequences..."):
        seq_map = _get_sequences(xml_root) if _get_sequences else _fallback_discover_sequences(xml_root)
except Exception as e:
    st.error(f"Error finding sequences: {e}")
    seq_map = {}

if not seq_map:
    st.error("No sequences found in the project.")
    st.stop()

default_seq = "SteelV1" if "SteelV1" in seq_map else sorted(seq_map.keys())[0]

# -------------------- OPTIONS UI --------------------
col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
with col1:
    options = sorted(seq_map.keys())
    main_seq = st.selectbox("Select sequence", options=options, index=options.index(default_seq))
with col2:
    expand_nested = st.checkbox("Expand nested sequences", value=True)
with col3:
    include_parent = st.checkbox("Keep parent row when expanded", value=False)
with col4:
    track_one_based = st.checkbox("1-based track numbering", value=True)

# -------------------- EXTRACT ROWS (SAFE) --------------------
try:
    with st.spinner(f"Extracting clips from '{main_seq}'..."):
        rows_list = extract_rows(
            root=xml_root,
            sequence_name=main_seq,
            expand_nested=expand_nested,   # from timeline_flatten.py
            include_parent=include_parent
        )
except Exception as e:
    st.error("🚨 **CRITICAL ERROR** 🚨 Failed to read timeline items from the selected sequence.")
    st.exception(e)
    # st.stop() # <-- REMOVED: This was preventing the Debug Console from showing!
    rows_list = [] # Ensure rows_list is empty on a crash

if not rows_list:
    st.warning(
        f"No clips found in sequence '{main_seq}'. "
        "Check the Debugging Console below for details on what was parsed."
    )
    # The app will continue execution to show the debug console

# -------------------- TICKS → TIMECODE BEFORE BUILDING DF --------------------
for r in rows_list:
    s = r.pop("StartTicks", None)
    e = r.pop("EndTicks", None)
    r["StartTC"] = ticks_to_tc_24fps(s) if isinstance(s, int) else ""
    r["EndTC"]   = ticks_to_tc_24fps(e) if isinstance(e, int) else ""

# Build DataFrame (ensure expected columns exist)
expected_cols = ["Type","Track","Name","ClipType","Source","StartTC","EndTC"]
df = pd.DataFrame.from_records(rows_list)
for c in expected_cols:
    if c not in df.columns:
        df[c] = ""
df = df[expected_cols]

# Track numbering (1-based if requested)
if track_one_based and "Track" in df.columns:
    df["Track"] = df["Track"].apply(lambda x: (x + 1) if pd.notna(x) else x)

# -------------------- Title & StockID rules --------------------

def derive_title_and_stock(name: str, source: str):
    if not name: return "", ""
    base, _ext = os.path.splitext(name)
    title = base
    stock_id = ""
    low = base.lower()
    parts = base.split("_") if "_" in base else []

    # Artlist
    if ("artlist" in low) or (source == "Artlist"):
        if len(parts) >= 2 and parts[0].isdigit():
            stock_id = parts[0]
            title = parts[1]

    # Imago → imago + digits (lowercase)
    if not stock_id:
        m = re.search(r"(?i)\bimago(\d+)\b", base)
        if m:
            stock_id = f"imago{m.group(1)}"

    # Colourbox/Colorbox → COLOURBOX + digits (uppercase)
    if not stock_id:
        m = re.search(r"(?i)\bcolo(u)?rbox[-_ ]?(\d+)\b", base)
        if m:
            stock_id = f"COLOURBOX{m.group(2)}"

    # Fallback: descriptive title after first underscore
    if "_" in base and (("artlist" not in low) and not stock_id):
        first, rest = base.split("_", 1)
        if rest.strip():
            title = rest.strip()

    return title.strip(), stock_id.strip()

# Apply title/stock
from pandas import Series

# FIX: Only attempt to apply if the DataFrame is not empty
if not df.empty:
    df[["Title","StockID"]] = df.apply(
        lambda r: Series(derive_title_and_stock(r["Name"], r["Source"])),
        axis=1
    )
else:
    # Add empty columns to prevent issues if a download button were available
    df["Title"] = ""
    df["StockID"] = ""


# Final column order
df = df[["Type","Track","Name","Title","ClipType","Source","StockID","StartTC","EndTC"]]

# -------------------- SORT, PREVIEW, DOWNLOAD --------------------

def _to_sec(tc: str) -> int:
    if tc and re.match(r"^\d{2}:\d{2}:\d{2}$", tc):
        h, m, s = map(int, tc.split(":"))
        return 3600*h + 60*m + s
    return -1

if not df.empty:
    df["_o"] = df["Type"].map({"Video": 0, "Audio": 1})
    df["_s"] = df["StartTC"].map(_to_sec)
    df = df.sort_values(["_o", "_s", "Track", "Name"]).drop(columns=["_o", "_s"])

if not rows_list:
    # If no rows, we skip the rest of the display
    pass
else:
    st.subheader("Preview")
    st.dataframe(df.head(50), use_container_width=True, height=420)

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label=f"Download {main_seq}_timecodes.csv",
        data=csv_bytes,
        file_name=f"{main_seq}_timecodes.csv",
        mime="text/csv"
    )

# -------------------- DEBUGGING CONSOLE --------------------
st.markdown("---")
with st.expander("🛠️ Debugging Console"):
    st.subheader("Processing Steps and Data Structures")
    
    st.markdown("#### 1. File Upload & XML Parsing")
    st.code(f"App Version: {_VERSION}")
    if uploaded:
        st.code(f"File Name: {uploaded.name} | Size: {len(uploaded.getvalue())} bytes")
    if xml_root is not None:
        st.code(f"XML Root Tag: {xml_root.tag}")
    else:
        st.warning("XML Root not available.")
        
    st.markdown("#### 2. Sequence Discovery")
    if seq_map:
        st.code(f"Total Sequences Found: {len(seq_map)}")
        st.write("Found Sequence Names:")
        st.json(sorted(seq_map.keys()))
    else:
        st.warning("Sequence map is empty.")

    st.markdown(f"#### 3. Timeline Extraction for '{main_seq}'")
    if rows_list:
        st.success(f"Successfully extracted {len(rows_list)} clip rows.")
        st.markdown("First 5 raw rows extracted (before timecode conversion):")
        try:
            st.json(rows_list[:5])
        except Exception:
            st.warning("Could not display raw rows.")
    else:
        st.error(f"Timeline extraction returned 0 rows for '{main_seq}'.")
        if xml_root is not None and main_seq:
            # This is the key diagnostic block
            seq_elem = seq_map.get(main_seq)
            if seq_elem is not None:
                track_items = seq_elem.findall(".//TrackItem")
                st.info(f"The XML element for sequence '{main_seq}' was found.")
                st.info(f"It contains {len(track_items)} raw <TrackItem> elements.")
                if len(track_items) > 0:
                    st.error("The raw XML has clip items, but the `extract_rows` parser failed to read them. **The bug is in `parser/timeline_flatten.py`.**")
                else:
                    st.warning("The XML element for the selected sequence contains no <TrackItem> elements. This sequence is empty in the project.")
            else:
                st.error("Error: The selected sequence name was not found in the XML after selection. This is a severe internal error.")
