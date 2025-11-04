# app.py
# Premiere → CSV (Flattened, 24 fps) — Streamlit app
# - Upload .prproj or gzipped XML (.txt)
# - Pick sequence, expand nested sequences into main timeline
# - Export CSV with: Type, Track, Name, Title, ClipType, Source, StockID, StartTC, EndTC

import os
import re
import pandas as pd
import streamlit as st

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

# --- NEW ---
# Define the app version
_VERSION = "v0.2.0 (Alpha)"
# --- END NEW ---

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

    # --- NEW ---
    # Display the app version at the bottom of the sidebar
    st.markdown("---")
    st.caption(f"App Version: **{_VERSION}**")
    # --- END NEW ---

# -------------------- UPLOAD --------------------
uploaded_file = st.file_uploader(
    "Upload a Premiere Pro Project File (.prproj) or Gzipped XML (.txt)",
    type=["prproj", "txt", "xml", "gz"],
    accept_multiple_files=False
)

if uploaded_file is None:
    st.info("Waiting for file upload...")
    st.stop()

# -------------------- PARSE XML --------------------
try:
    with st.spinner("Decompressing and parsing XML tree..."):
        raw_bytes = uploaded_file.getvalue()
        xml_root = load_xml_tree(raw_bytes)
except Exception as e:
    st.error(f"Failed to parse XML. Is this a valid .prproj or gzipped-XML file? \n\nError: {e}")
    st.stop()

# -------------------- SEQUENCE SELECTION --------------------
with st.spinner("Finding sequences..."):
    if _get_sequences:
        sequences = _get_sequences(xml_root)
    else:
        # Fallback in case parser fails (should not happen, but safe)
        sequences = {
            elem.find("Name").text: elem
            for elem in xml_root.iter()
            if elem.tag.endswith("Sequence") and elem.find("Name") is not None and elem.find("Name").text
        }

if not sequences:
    st.error("No sequences found in the project file.")
    st.stop()

seq_names = sorted(sequences.keys(), key=lambda s: s.lower())
seq_choice = st.selectbox(
    "Select your main sequence",
    options=seq_names,
    index=0
)

col_opt, col_blank = st.columns([1, 3])
with col_opt:
    expand_nested = st.checkbox("Expand nested sequences", value=True, help="Recursively expand all nested sequences and 'flatten' them into the main timeline.")
    # include_parent = st.checkbox("Include nested parent clip", value=False, help="Include the 'parent' clip that contains the nested sequence.")

if not seq_choice:
    st.warning("Please select a sequence to proceed.")
    st.stop()

# -------------------- PROCESS SEQUENCE --------------------
try:
    with st.spinner(f"Parsing timeline for '{seq_choice}'..."):
        rows = extract_rows(xml_root, seq_choice, expand_nested)
        if not rows:
            st.warning(f"No clips found in sequence '{seq_choice}'.")
            st.stop()
        df = pd.DataFrame(rows)
except Exception as e:
    st.error(f"An error occurred during timeline parsing: {e}")
    st.exception(e)
    st.stop()

# -------------------- DATA CLEANUP & DERIVATION --------------------
st.markdown("---")
st.subheader("Processing Output")

# Ticks to Timecode
with st.spinner("Calculating timecodes (24 fps)..."):
    df["StartTC"] = df["StartTicks"].apply(ticks_to_tc_24fps)
    df["EndTC"] = df["EndTicks"].apply(ticks_to_tc_24fps)

# Title/Stock ID Derivation
with st.spinner("Deriving Title and Stock IDs..."):
    def derive_title_and_stock(name, source):
        name = name or ""
        title = name
        stock_id = ""
        low = name.lower()

        # Artlist: "Artlist_Music_SongTitle_ID-Number"
        if "artlist" in low and ("_id-" in name or "_ID-" in name):
            parts = name.split("_")
            if len(parts) >= 4:
                title = parts[2].strip()
                stock_id = parts[-1].strip()
        
        # Imago: "Imago_12345678"
        elif "imago" in low:
            parts = name.split("_")
            if len(parts) > 1 and parts[1].isdigit():
                stock_id = parts[1].strip()
                # Title for imago is often the name itself or empty
                title = "" 
        
        # Colourbox: "Colourbox_12345678"
        elif "colourbox" in low:
            parts = name.split("_")
            if len(parts) > 1 and parts[1].isdigit():
                stock_id = parts[1].strip()
                title = "" # Same as imago

        # Fallback for "Title_ID"
        if not stock_id and "_" in name:
            parts = name.rsplit("_", 1)
            if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) >= 6:
                title = parts[0].strip()
                stock_id = parts[1].strip()
        
        # Fallback for "Title ID"
        if not stock_id and " " in name:
            parts = name.rsplit(" ", 1)
            if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) >= 6:
                title = parts[0].strip()
                stock_id = parts[1].strip()

        # Clean title if it's just a filename
        base, ext = os.path.splitext(title)
        if ext.lower().lstrip(".") in ("mp4","mov","m4v","avi","mxf","mkv","mp3","wav","aif","aiff"):
            title = base

        # Handle simple "Name_Title" from underscore
        if "_" in base and (("artlist" not in low) and not stock_id):
            first, rest = base.split("_", 1)
            if rest.strip():
                title = rest.strip()

        return title.strip(), stock_id.strip()

    # Apply title/stock
    from pandas import Series

    df[["Title","StockID"]] = df.apply(
        lambda r: Series(derive_title_and_stock(r["Name"], r["Source"])),
        axis=1
    )

    # Final column order
    df = df[["Type","Track","Name","Title","ClipType","Source","StockID","StartTC","EndTC"]]

# -------------------- SORT, PREVIEW, DOWNLOAD --------------------

def _to_sec(tc: str) -> int:
    if tc and re.match(r"^\d{2}:\d{2}:\d{2}$", tc):
        h, m, s = map(int, tc.split(":"))
        return 3600*h + 60*m + s
    return -1

df["_o"] = df["Type"].map({"Video": 0, "Audio": 1})
df["_s"] = df["StartTC"].map(_to_sec)
df = df.sort_values(["_o", "_s", "Track", "Name"]).drop(columns=["_o", "_s"])

st.subheader("Preview")
st.dataframe(df.head(50), use_container_width=True, height=420)

csv_bytes = df.to_csv(index=False).encode("utf-8")
st.download_button(
    label="Download Full List as CSV",
    data=csv_bytes,
    file_name=f"{os.path.splitext(uploaded_file.name)[0]}_{seq_choice}.csv",
    mime="text/csv"
)
