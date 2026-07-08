"""Threat intelligence ingestion pipeline.

Pulls from GoPlus, PhishTank, Chainabuse, and community submissions.
Embeds with BGE-M3 and stores in Qdrant threat_intel collection.
"""
import asyncio

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from sentence_transformers import SentenceTransformer

COLLECTION = "threat_intel"
MODEL_NAME = "BAAI/bge-m3"

async def fetch_goplus_threats() -> list[dict]:
    """Fetch from GoPlus Security API."""
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.gopluslabs.io/api/v1/phishing_site")
        return resp.json().get("result", [])

def embed_and_store(threats: list[dict]):
    model = SentenceTransformer(MODEL_NAME)
    client = QdrantClient(host="rmi-qdrant", port=6333)
    points = []
    for threat in threats:
        text = f"{threat.get('url', '')} {threat.get('description', '')}"
        vector = model.encode(text).tolist()
        points.append(PointStruct(
            id=hash(text) & 0x7FFFFFFFFFFFFFFF,
            vector=vector,
            payload=threat
        ))
    client.upsert(collection_name=COLLECTION, points=points)
    print(f"Ingested {len(points)} threats")

if __name__ == "__main__":
    threats = asyncio.run(fetch_goplus_threats())
    embed_and_store(threats)
