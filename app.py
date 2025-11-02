import streamlit as st
import pandas as pd

from parser.prproj_reader import load_xml_tree, find_sequences
from parser.timeline_flatten import extract_rows
from parser.timecode import ticks_to_tc_24fps

st.set_page_config(page_title="Premiere → CSV", layout="wide")
st.title("Premiere → CSV (Flattened, 24 fps)")

with st.sidebar:
    if st.button("🔄 Clear cache (dev)"):
        st.cache_data.clear()
        st.success("Cleared cached data. Please re-upload your file.")

st.markdown(
    "Upload a Premiere Pro project (`.prproj`) or gzipped XML (`.txt`). "
    "Files are processed **in-memory** and not stored on the server."
)

uploaded = st.file_uploader(
    "Upload .prproj or gzipped XML (.txt)",
    type=["prproj", "txt"], accept_multiple_files=False
)

if not uploaded:
    st.info("Drop a file above to start.")
    st.stop()

# Parse XML tree (cached for performance)
xml_root = load_xml_tree(uploaded.getvalue())

# Discover sequences and pick main
seq_map = find_sequences(xml_root)
if not seq_map:
    st.error("No sequences found in the project.")
    st.stop()

default_seq = "SteelV1" if "SteelV1" in seq_map else sorted(seq_map.keys())[0]

# Options UI
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

# Extract flattened rows with new parameter names
rows = extract_rows(
    root=xml_root,
    sequence_name=main_seq,
    expand_nested=expand_nested,
    include_parent=include_parent
)

# Convert ticks to 24 fps timecode & finalize dataframe
for r in rows:
    r["StartTC"] = ticks_to_tc_24fps(r.pop("StartTicks"))
    r["EndTC"]   = ticks_to_tc_24fps(r.pop("EndTicks"))

# ---- Build DataFrame (existing code above computed rows & timecodes) ----
cols = ["Type","Track","Name","ClipType","Source","StartTC","EndTC"]
df = pd.DataFrame(rows)[cols]

# Track numbering 1-based (if toggled)
if track_one_based and "Track" in df.columns:
    df["Track"] = df["Track"].apply(lambda x: (x + 1) if pd.notna(x) else x)

# --- Derive strict Title and StockID rules (Artlist / Imago / Colourbox) ---
import os, re

def derive_title_and_stock(name: str, source: str):
    if not name:
        return "", ""

    base, _ext = os.path.splitext(name)
    title = base
    stock_id = ""
    low = base.lower()
    parts = base.split("_") if "_" in base else []

    # Artlist
    if ("artlist" in low) or (source == "Artlist"):
        # Stock ID: first numeric token before first underscore
        if len(parts) >= 2 and parts[0].isdigit():
            stock_id = parts[0]
            # Title strictly between first and second underscore
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

    # Generic fallback: if we didn't set a special Artlist title and there is an underscore,
    # use the part after the first underscore as a descriptive title.
    if "_" in base and (("artlist" not in low) and not stock_id):
        first, rest = base.split("_", 1)
        if rest.strip():
            title = rest.strip()

    return title.strip(), stock_id.strip()

df[["Title","StockID"]] = df.apply(
    lambda r: pd.Series(derive_title_and_stock(r["Name"], r["Source"])),
    axis=1
)

# Reorder columns
df = df[["Type","Track","Name","Title","ClipType","Source","StockID","StartTC","EndTC"]]

# ---- Sorting (Video → Audio; chronological; track; name) ----
type_order = {"Video":0, "Audio":1}
df["_o"] = df["Type"].map(type_order)

def _to_sec(tc: str) -> int:
    h,m,s = map(int, tc.split(':'))
    return 3600*h + 60*m + s

df["_s"] = df["StartTC"].map(_to_sec)
df = df.sort_values(["_o","_s","Track","Name"]).drop(columns=["_o","_s"])

# ---- UI ----
st.subheader("Preview")
st.dataframe(df.head(50), use_container_width=True, height=420)

csv_bytes = df.to_csv(index=False).encode("utf-8")
st.download_button(
    label=f"Download {main_seq}_timecodes.csv",
    data=csv_bytes,
    file_name=f"{main_seq}_timecodes.csv",
    mime="text/csv"
)

with st.expander("What happens to my file?"):
    st.markdown("""
- Your upload is kept in **RAM** for this session and cleared on rerun/close.
- No data is persisted on disk by the app.
- Parsing is cached only for this content to speed up UI tweaks.
    """)