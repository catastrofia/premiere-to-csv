import gzip
import xml.etree.ElementTree as ET
import streamlit as st

@st.cache_data(show_spinner=False)
def load_xml_tree(raw_bytes: bytes):
    """Return XML root element. Accepts gzipped or plain XML bytes."""
    try:
        xml_bytes = gzip.decompress(raw_bytes)
    except Exception:
        # Find XML start just in case header has junk
        start = raw_bytes.find(b'<?xml')
        if start == -1:
            start = raw_bytes.find(b'<')
        xml_bytes = raw_bytes[start:] if start >= 0 else raw_bytes
    return ET.fromstring(xml_bytes)

@st.cache_data(show_spinner=False)
def discover_sequences(root):
    """Return dict: name -> element for all sequences found."""
    seqs = {}
    for elem in root.iter():
        if elem.tag.endswith("Sequence"):
            name = None
            n = elem.find("Name")
            if n is None:
                n = elem.find(".//Name")
            if n is not None and (n.text or "").strip():
                name = n.text.strip()
            if name:
                seqs[name] = elem
    return seqs
