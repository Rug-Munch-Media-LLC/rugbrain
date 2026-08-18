"""rugbrain Intelligence Dashboard — Streamlit UI for RAG system."""

import json
import os
from datetime import datetime

import httpx
import streamlit as st

RAG_API = os.getenv("RAG_API_URL", "http://127.0.0.1:8012")
PRESIDIO_URL = os.getenv("PRESIDIO_URL", "http://127.0.0.1:8090")

st.set_page_config(
    page_title="rugbrain Intelligence Dashboard",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)


async def _get_health() -> dict:
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.get(f"{RAG_API}/health")
        return r.json()


async def _search(query: str, collection: str, limit: int = 10) -> list:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{RAG_API}/rag/search", json={"query": query, "collection": collection, "limit": limit}
        )
        return r.json().get("results", [])


async def _confidence(sources: list, query: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{RAG_API}/rag/confidence", json={"results": sources, "query": query})
        return r.json()


async def _redact(text: str) -> dict:
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.post(f"{PRESIDIO_URL}/redact", json={"text": text, "language": "en"})
        return r.json()


async def _analyze(text: str) -> list:
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.post(f"{PRESIDIO_URL}/analyze", json={"text": text, "language": "en"})
        return r.json()


st.title("🧠 rugbrain Intelligence Dashboard")
st.markdown("Crypto Intelligence RAG — Qdrant · Infinity · Presidio PII · Multi-hop")

tab1, tab2, tab3, tab4 = st.tabs(["🔍 Search", "📊 Health", "🔒 PII Scanner", "✅ Confidence"])

# ── Tab 1: Search ──
with tab1:
    st.subheader("Semantic Search")
    col1, col2 = st.columns([3, 1])
    with col1:
        query = st.text_input("Query", placeholder="e.g. 'recent ethereum hack'")
    with col2:
        collection = st.selectbox(
            "Collection",
            ["news_v2", "threat_intel", "defi_exploits", "scam_patterns", "wallet_profiles"],
        )
    limit = st.slider("Results", 1, 20, 10)

    if st.button("Search", type="primary") and query:
        with st.spinner("Searching..."):
            try:
                import asyncio

                results = asyncio.run(_search(query, collection, limit))
                if results:
                    st.success(f"Found {len(results)} results")
                    for r in results:
                        score = r.get("score", 0)
                        color = "green" if score > 0.8 else "orange" if score > 0.5 else "red"
                        with st.expander(f"{r.get('source', '?')} — score: {score:.4f}"):
                            st.markdown(f"**Content:** {r.get('content', '')[:500]}")
                            st.json(r.get("metadata", {}))
                else:
                    st.warning("No results found")
            except Exception as e:
                st.error(f"Search failed: {e}")

# ── Tab 2: Health ──
with tab2:
    st.subheader("System Health")
    if st.button("Refresh Health", type="primary"):
        with st.spinner("Checking..."):
            try:
                import asyncio

                h = asyncio.run(_get_health())
                col1, col2, col3 = st.columns(3)
                sub = h.get("subscriber_stats", {})
                col1.metric("Subscriber Running", sub.get("running", "?"))
                col2.metric("Ingested Count", sub.get("ingested_count", 0))
                col3.metric("Last Ingested", sub.get("last_ingested_at", "—")[:19])

                st.subheader("Qdrant Collections")
                qdrant = h.get("qdrant", {})
                if qdrant:
                    cols = st.columns(4)
                    for i, (name, count) in enumerate(sorted(qdrant.items())):
                        if isinstance(count, (int, float)):
                            cols[i % 4].metric(name, count)
                else:
                    st.info("Qdrant stats unavailable")
            except Exception as e:
                st.error(f"Health check failed: {e}")

# ── Tab 3: PII Scanner ──
with tab3:
    st.subheader("PII Redaction Scanner")
    pii_text = st.text_area(
        "Text to scan", placeholder="Paste text with wallet addresses, emails, etc.", height=100
    )
    if st.button("Scan & Redact", type="primary") and pii_text:
        with st.spinner("Scanning..."):
            try:
                import asyncio

                redacted = asyncio.run(_redact(pii_text))
                entities = asyncio.run(_analyze(pii_text))
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("**Original**")
                    st.code(pii_text, language="text")
                with col2:
                    st.markdown("**Redacted**")
                    st.code(redacted.get("redacted", "?"), language="text")
                if entities:
                    st.subheader("Entities Found")
                    for e in entities:
                        et = e.get("entity_type") or e.get("type", "?")
                        st.write(
                            f"- {et}: pos {e.get('start')}-{e.get('end')} score={e.get('score', 0):.2f}"
                        )
                else:
                    st.success("No PII detected")
            except Exception as e:
                st.error(f"PII scan failed: {e}")

# ── Tab 4: Confidence ──
with tab4:
    st.subheader("Confidence Scorer")
    conf_text = st.text_input("Claim to evaluate", placeholder="e.g. '0x1234... is a honeypot'")
    if st.button("Score Confidence", type="primary") and conf_text:
        with st.spinner("Scoring..."):
            try:
                import asyncio

                results = asyncio.run(_search(conf_text, "threat_intel", 5))
                if results:
                    conf = asyncio.run(_confidence(results, conf_text))
                    score = conf.get("confidence_score", 0)
                    label = conf.get("confidence_label", "unknown")
                    if score >= 0.8:
                        st.success(f"Confidence: {score:.4f} ({label})")
                    elif score >= 0.5:
                        st.warning(f"Confidence: {score:.4f} ({label})")
                    else:
                        st.error(f"Confidence: {score:.4f} ({label})")
                    st.json(conf.get("breakdown", {}))
                else:
                    st.warning("No sources found to score against")
            except Exception as e:
                st.error(f"Confidence scoring failed: {e}")

st.sidebar.markdown(f"**Last refreshed:** {datetime.now().isoformat()[:19]}")
st.sidebar.markdown("---")
st.sidebar.markdown("**rugbrain** — Standalone Crypto Intelligence RAG")
