import os

import httpx
import streamlit as st

API_URL = os.getenv("TENDERCITE_API_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="TenderCite", page_icon="📄", layout="wide")
st.title("TenderCite")
st.caption("Evidence-grounded AI for public procurement documents")

uploaded = st.file_uploader("Upload a tender PDF", type=["pdf"])
if uploaded is not None and st.button("Import document", type="primary"):
    with st.spinner("Parsing document…"):
        response = httpx.post(
            f"{API_URL}/api/v1/documents",
            files={"file": (uploaded.name, uploaded.getvalue(), "application/pdf")},
            timeout=120,
        )
    if response.is_success:
        doc = response.json()
        st.success(
            f"Imported {doc['filename']} — {doc['page_count']} pages, {doc['chunk_count']} chunks"
        )
    else:
        st.error(f"Import failed: {response.text}")

st.subheader("Imported documents")
try:
    response = httpx.get(f"{API_URL}/api/v1/documents", timeout=10)
    response.raise_for_status()
    documents = response.json()
except httpx.HTTPError as exc:
    st.warning(f"API unavailable: {exc}")
    documents = []

if documents:
    st.dataframe(
        [
            {
                "filename": d["filename"],
                "pages": d["page_count"],
                "chunks": d["chunk_count"],
                "sha256": d["sha256"][:12] + "…",
            }
            for d in documents
        ],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No documents imported yet.")

st.divider()
st.caption(
    "v0.1 validates the ingestion and source-traceability foundation. Retrieval, structured "
    "requirement extraction, human review and Go/No-Go workflows follow in the next milestones."
)
