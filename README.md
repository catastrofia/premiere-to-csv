# Premiere → CSV (Flattened, 24 fps)

A zero-install, web-based tool (Streamlit) that parses Adobe Premiere Pro project files, **flattens nested sequences**, and exports a clean CSV with:

**Columns:** `Type, Track, Name, ClipType, Source, StartTC, EndTC`

**Sorting:** Video first, then Audio, both chronological.

**Timecode:** 24 fps rule (≤ 12 frames keep; ≥ 13 frames round up).

---

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

> Default per-file upload limit is 200 MB; you can change it in `.streamlit/config.toml` via `server.maxUploadSize`. See Streamlit's file uploader docs.  

## Deploy (free) on Streamlit Community Cloud

1. Push this folder to a **public GitHub repo**.
2. Visit **share.streamlit.io** and click **Create app**.
3. Select your repo/branch and set **main file = app.py**.
4. Deploy. Your app gets a public URL and redeploys on each `git push`.

Helpful references:
- Streamlit Community Cloud deploy docs  
- `st.file_uploader` docs (upload limits & types)  
- Caching (`st.cache_data`) & execution model  

## Notes
- Uploads are processed **in memory** only (no disk writes).  
- The parser uses only Python standard libraries for portability.
- If you need even faster XML lookups for huge projects, you can swap in `lxml` later.
