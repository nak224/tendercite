"""Thin Streamlit client: all persistence, grounding and decisions live in the API."""

import os

import httpx
import streamlit as st

API_URL = os.environ.get("TENDERCITE_API_URL", "http://localhost:8000").rstrip("/")
st.set_page_config(page_title="TenderCite", page_icon="📑", layout="wide")
st.title("TenderCite")
st.caption("Evidence-grounded AI for public procurement documents")
st.warning(
    "AI findings require human review. This tool does not provide legal or procurement advice."
)


def api(method, path, **kwargs):
    try:
        response = httpx.request(method, API_URL + path, timeout=180, **kwargs)
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as exc:
        # API errors are sanitized server-side. Do not display raw provider bodies.
        try:
            detail = exc.response.json().get("detail", "Request failed")
        except ValueError:
            detail = "Request failed"
        st.error(f"API {exc.response.status_code}: {detail}")
    except httpx.RequestError:
        st.error("Cannot reach the API. Check that the backend is running.")
    return None


def evidence_view(evidence):
    if not evidence:
        st.error("MISSING_EVIDENCE — no supporting source was supplied.")
    for index, ref in enumerate(evidence):
        state = ref["grounding_status"]
        label = f"{state} · document {ref['document_id']} · page {ref['page_number']}"
        if state == "VERIFIED_QUOTE":
            st.info(label + " · Quote matched; interpretation still requires review.")
        else:
            st.error(label)
        st.text(ref["quote"])
        if st.button("Show source page", key=f"page-{ref['document_id']}-{index}"):
            response = api(
                "GET", f"/api/v1/documents/{ref['document_id']}/pages/{ref['page_number']}"
            )
            if response is not None:
                st.text(response.json()["text"])


response = api("GET", "/api/v1/documents")
if response is None:
    st.stop()
documents = response.json()
labels = {d["id"]: f"{d['filename']} ({d['id'][:8]})" for d in documents}
section = st.sidebar.radio(
    "Workflow", ["Documents", "Search", "Analysis & review", "Go / No-Go", "Export"]
)
selected = st.sidebar.multiselect(
    "Source documents", list(labels), format_func=labels.get, default=list(labels)
)
st.sidebar.caption(
    "Local embeddings; analysis sends selected retrieved passages to your configured LLM."
)

if section == "Documents":
    st.subheader("Document library")
    uploads = st.file_uploader("PDF documents", type=["pdf"], accept_multiple_files=True)
    if st.button("Import PDFs", disabled=not uploads):
        for upload in uploads:
            result = api(
                "POST",
                "/api/v1/documents",
                files={"file": (upload.name, upload.getvalue(), "application/pdf")},
            )
            if result is not None:
                st.success(f"Imported {upload.name}: {result.json()['page_count']} pages")
        st.rerun()
    st.dataframe(documents, hide_index=True)
    if documents:
        doc = st.selectbox("Manage document", list(labels), format_func=labels.get)
        left, right = st.columns(2)
        if left.button("Reindex document"):
            result = api("POST", f"/api/v1/documents/{doc}/index")
            if result is not None:
                st.success(f"Indexed {result.json()['indexed_chunks']} chunks")
        confirm_delete = right.checkbox("Confirm document deletion")
        if right.button("Delete document", disabled=not confirm_delete):
            if api("DELETE", f"/api/v1/documents/{doc}") is not None:
                st.rerun()
        st.caption(
            "Sources referenced by analyses are retained for audit and cannot be deleted here."
        )

elif section == "Search":
    st.subheader("Search with sources")
    query = st.text_input("Search query")
    top_k = st.slider("Results", 1, 20, 5)
    if st.button("Search", disabled=not query.strip() or not selected):
        response = api(
            "POST",
            "/api/v1/search",
            json={"query": query, "document_ids": selected, "top_k": top_k},
        )
        if response is not None:
            hits = response.json()
            if not hits:
                st.info("No matching indexed chunks.")
            for hit in hits:
                st.markdown(f"**{hit['document_name']} — page {hit['page_number']}**")
                st.caption(f"Cosine similarity: {hit['score']:.3f} · chunk {hit['chunk_id']}")
                st.text(hit["text"])

elif section == "Analysis & review":
    st.subheader("Structured analysis")
    config_response = api("GET", "/api/v1/configuration")
    config = config_response.json() if config_response is not None else {}
    st.caption(
        f"Provider: {config.get('llm_destination') or 'not configured'} · "
        f"Model: {config.get('llm_model') or 'not configured'}"
    )
    question = st.text_input(
        "Analysis focus",
        "Mandatory requirements, deadlines, references, insurance, award criteria and security",
    )
    consent = st.checkbox("Send selected retrieved passages to the configured provider")
    if st.button(
        "Start analysis",
        disabled=not selected
        or not consent
        or not config.get("llm_configured")
        or not question.strip(),
    ):
        with st.spinner("Retrieving sources and validating extracted evidence…"):
            response = api(
                "POST",
                "/api/v1/analyses",
                json={"document_ids": selected, "query": question, "top_k": 12},
            )
        if response is not None:
            st.success(f"Analysis saved: {response.json()['id']}")
    response = api("GET", "/api/v1/findings")
    findings = response.json() if response is not None else []
    status_filter = st.selectbox(
        "Review state", ["ALL", "UNREVIEWED", "CONFIRMED", "MODIFIED", "REJECTED"]
    )
    findings = [f for f in findings if status_filter in ("ALL", f["review_status"])]
    if not findings:
        st.info("No findings in this view.")
    else:
        finding = st.selectbox(
            "Finding",
            findings,
            format_func=lambda f: f["effective_value"]["statement"],
            key="selected-finding",
        )
        st.caption(
            f"Analysis {finding['analysis_run_id']} · {finding['review_status']} · "
            f"{finding['grounding_status']}"
        )
        st.text(finding["effective_value"]["statement"])
        st.caption(f"Model confidence: {finding['confidence']} (not a calibrated probability)")
        evidence_view(finding["evidence"])
        with st.expander("Original AI output"):
            st.json(
                {k: finding[k] for k in ["statement", "category", "requirement_type", "evidence"]}
            )
        with st.form("review-" + finding["id"]):
            action = st.selectbox("Action", ["CONFIRM", "MODIFY", "REJECT"])
            value = finding["effective_value"]
            statement = st.text_area("Reviewed statement (used for MODIFY)", value["statement"])
            categories = ["MUST", "SCORING", "INFORMATION", "RISK"]
            category = st.selectbox(
                "Category", categories, index=categories.index(value["category"])
            )
            types = [
                "DEADLINE",
                "ELIGIBILITY",
                "REFERENCE",
                "FINANCIAL",
                "INSURANCE",
                "TECHNICAL",
                "CERTIFICATE",
                "EVIDENCE",
                "AWARD",
                "PRICE",
                "CONTRACT",
                "LIABILITY",
                "PRIVACY",
                "SECURITY",
                "OTHER",
            ]
            kind = st.selectbox(
                "Requirement type", types, index=types.index(value["requirement_type"])
            )
            comment = st.text_area("Review comment")
            if st.form_submit_button("Save review"):
                body = {"action": action, "comment": comment}
                if action == "MODIFY":
                    body["reviewed_value"] = {
                        "statement": statement,
                        "category": category,
                        "requirement_type": kind,
                    }
                if api("POST", f"/api/v1/findings/{finding['id']}/reviews", json=body) is not None:
                    st.rerun()
        with st.expander("Review history"):
            response = api("GET", f"/api/v1/findings/{finding['id']}/reviews")
            if response is not None:
                st.json(response.json())

elif section == "Go / No-Go":
    st.subheader("Human-managed decision matrix")
    st.info(
        "Only confirmed or modified findings appear. Supply bidder facts and a rationale yourself."
    )
    response = api("GET", "/api/v1/go-no-go")
    rows = response.json() if response is not None else []
    if not rows:
        st.info("Review findings first to build the matrix.")
    else:
        st.dataframe(
            [{k: v for k, v in r.items() if k != "evidence"} for r in rows], hide_index=True
        )
        row = st.selectbox("Criterion", rows, format_func=lambda r: r["criterion"])
        evidence_view(row["evidence"])
        with st.form("assessment-" + row["finding_id"]):
            statuses = ["FULFILLED", "PARTIAL", "MISSING", "CLARIFICATION_NEEDED", "NOT_APPLICABLE"]
            status = st.selectbox("Assessment", statuses, index=statuses.index(row["status"]))
            rationale = st.text_area("Bidder facts / rationale", row["rationale"] or "")
            if st.form_submit_button("Save assessment"):
                if (
                    api(
                        "PATCH",
                        "/api/v1/go-no-go/" + row["finding_id"],
                        json={"status": status, "rationale": rationale},
                    )
                    is not None
                ):
                    st.rerun()
else:
    st.subheader("Export findings, evidence and decisions")
    st.caption("Exports include unreviewed and rejected findings, explicitly labeled.")
    for format, extension, mime in [
        ("json", "json", "application/json"),
        ("csv", "csv", "text/csv"),
        ("markdown", "md", "text/markdown"),
    ]:
        response = api("GET", "/api/v1/exports", params={"format": format})
        if response is not None:
            st.download_button(
                f"Download {format.upper()}",
                response.content,
                file_name=f"tendercite.{extension}",
                mime=mime,
            )
