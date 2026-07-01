"""Qdrant snapshot backup to HuggingFace S3.

Run daily via cron to back up all 20 RAG collections.
"""
import boto3
from datetime import datetime
from qdrant_client import QdrantClient

S3_ENDPOINT = "https://s3.hf.co/cryptorugmunch"
S3_BUCKET = "rmi-rag"

def snapshot_all_collections():
    client = QdrantClient(host="rmi-qdrant", port=6333)
    collections = client.get_collections().collections
    s3 = boto3.client("s3", endpoint_url=S3_ENDPOINT, region_name="us-east-1")
    date = datetime.now().strftime("%Y-%m-%d")

    for col in collections:
        name = col.name
        snapshot = client.create_snapshot(collection_name=name)
        path = f"/tmp/{name}_{date}.snapshot"
        client.download_snapshot(collection_name=name, snapshot_id=snapshot.name, path=path)

        key = f"snapshots/{name}_{date}.snapshot"
        s3.upload_file(path, S3_BUCKET, key)
        print(f"Backed up {name} to s3://{S3_BUCKET}/{key}")

if __name__ == "__main__":
    snapshot_all_collections()