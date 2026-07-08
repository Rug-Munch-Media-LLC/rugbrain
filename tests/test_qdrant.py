import pytest


@pytest.mark.asyncio
async def test_qdrant_store_importable():
    from rmi_rag.qdrant_store import get_qdrant_store
    store = await get_qdrant_store()
    assert hasattr(store, "search")
    assert hasattr(store, "insert")
    assert hasattr(store, "hybrid_search")

@pytest.mark.asyncio
async def test_qdrant_store_singleton():
    from rmi_rag.qdrant_store import get_qdrant_store
    s1 = await get_qdrant_store()
    s2 = await get_qdrant_store()
    assert s1 is s2
