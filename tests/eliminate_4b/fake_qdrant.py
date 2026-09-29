"""A fake Qdrant that is faithful enough to test the id-diff against.

Qdrant :6334 is PRODUCTION. Nothing in this test package may touch it — a
suite that asserts against the live service is how a test run becomes an
outage. These fakes reproduce the two shapes that actually bit us:

  * the unnamed default vector serialises as ``{"": {...}}``, NOT as
    ``{4096: {...}}`` — reading the key as a name corrupts every upsert;
  * scroll returns ``{"result": {"points": [...], "next_page_offset": ...}}``
    and a named-``dense`` + ``sparse`` collection is a distinct shape from a
    dense-only one.
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class FakeQdrant:
    def __init__(self, cols: dict[str, dict[str, Any]]) -> None:
        self.cols = cols
        self.upserts: list[tuple[str, list[dict[str, Any]]]] = []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=_Transport(self), base_url="http://fake")

    def info(self, name: str) -> dict[str, Any] | None:
        col = self.cols.get(name)
        if col is None:
            return None
        size, vname, sparse = col["dim"], col.get("vname"), col.get("sparse")
        vectors = (
            {vname: {"size": size, "distance": "Cosine"}}
            if vname
            else {"": {"size": size, "distance": "Cosine"}}
        )
        params: dict[str, Any] = {"vectors": vectors}
        if sparse:
            params["sparse_vectors"] = {"sparse": {}}
        return {"points_count": len(col["points"]), "config": {"params": params}}

    def scroll(self, name: str, limit: int) -> list[dict[str, Any]]:
        return self.cols[name]["points"][:limit]


class _Transport(httpx.BaseTransport):
    def __init__(self, fq: FakeQdrant) -> None:
        self.fq = fq

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not path.startswith("/collections/"):
            return httpx.Response(404)
        name, _, tail = path[len("/collections/") :].partition("/")
        if request.method == "PUT" and tail == "":
            # Create. Qdrant refuses to overwrite an existing collection, and
            # so must the fake — otherwise a bug that recreates a live twin
            # would pass here and fail in production.
            if name in self.fq.cols:
                return httpx.Response(409, json={"status": {"error": "collection already exists"}})
            body = json.loads(request.content or b"{}")
            spec_name, spec = next(iter(body["vectors"].items()))
            self.fq.cols[name] = col(spec["size"], [], vname=(spec_name or None))
            return httpx.Response(200, json={"result": True})
        info = self.fq.info(name)
        if info is None:
            return httpx.Response(404, json={"status": {"error": "not found"}})
        if tail == "":
            return httpx.Response(200, json={"result": info})
        body = json.loads(request.content) if request.content else {}
        if tail == "points/scroll":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "points": self.fq.scroll(name, body.get("limit", 512)),
                        "next_page_offset": None,
                    }
                },
            )
        if tail == "points":
            self.fq.upserts.append((name, body["points"]))
            target = self.fq.cols[name]
            by_id = {str(p["id"]): p for p in target["points"]}
            for p in body["points"]:
                by_id[str(p["id"])] = p
            target["points"] = list(by_id.values())
            return httpx.Response(200, json={"result": {"status": "completed"}})
        return httpx.Response(404)


def col(dim: int, points: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return {"dim": dim, "points": points, **kw}


def pid(n: int) -> str:
    return f"0000{n:04d}-0000-4000-8000-000000000000"


def docs(count: int, start: int = 0) -> list[dict[str, Any]]:
    return [{"id": pid(i), "payload": {"content": f"doc {i}"}} for i in range(start, start + count)]
