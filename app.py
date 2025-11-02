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

col1, col2, col3, col4 = st.columns([2,1,1,1])
with col1:
    options = sorted(seq_map.keys())
    main_seq = st.selectbox("Main sequence", options=options, index=options.index(default_seq))
with col2:
    include_nested = st.checkbox("Include nested sequences", value=True)
with col3:
    include_parent = st.checkbox("Include parent nest row", value=False)
with col4:
    track_one_based = st.checkbox("1-based track numbering", value=True)

# Extract flattened rows
rows = extract_rows(
    root=xml_root,
    sequence_name=main_seq,
    include_nested=include_nested,
    include_parent=include_parent
)

# Convert ticks to 24 fps timecode & finalize dataframe
for r in rows:
    r["StartTC"] = ticks_to_tc_24fps(r.pop("StartTicks"))
    r["EndTC"]   = ticks_to_tc_24fps(r.pop("EndTicks"))

cols = ["Type","Track","Name","ClipType","Source","StartTC","EndTC"]
df = pd.DataFrame(rows)[cols]

# Track numbering
if track_one_based and "Track" in df.columns:
    df["Track"] = df["Track"].apply(lambda x: (x + 1) if pd.notna(x) else x)

# Sort (Video → Audio; chronological; track; name)
type_order = {"Video":0, "Audio":1}
df["_o"] = df["Type"].map(type_order)

def _to_sec(tc: str) -> int:
    h,m,s = map(int, tc.split(':'))
    return 3600*h + 60*m + s

df["_s"] = df["StartTC"].map(_to_sec)
df = df.sort_values(["_o","_s","Track","Name"]).drop(columns=["_o","_s"])

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