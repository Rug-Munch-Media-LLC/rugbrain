"""The HTTP surface — declared once.

One job: attach every router to the app in one place, so ``main.py`` stays a
composition root. Importing this module pulls in the whole API surface; keep it
last in ``main.py`` so ``install_import_guard()`` has already run.
"""

from __future__ import annotations

from fastapi import FastAPI

from rugbrain.alert_routes import router as alerts_router
from rugbrain.endpoints import router as rag_router
from rugbrain.endpoints import scam_check_router
from rugbrain.metrics import metrics_router
from rugbrain.news_feed import news_feed_router
from rugbrain.routes.content import router as content_router
from rugbrain.routes.core import embed_text as embed_text_route
from rugbrain.routes.daily_rug import router as daily_rug_router
from rugbrain.routes.search_federated import router as federated_router
from rugbrain.routes.ta import router as ta_router
from rugbrain.scanner_feedback import scanner_feedback_router
from rugbrain.sentiment_engine import sentiment_router
from rugbrain.system_health import router as health_router


def include_all_routers(app: FastAPI) -> None:
    """Mount every router on ``app``."""
    app.include_router(rag_router)
    app.include_router(scam_check_router)
    app.include_router(metrics_router)  # /metrics — Prometheus scrape target
    app.include_router(scanner_feedback_router)  # /api/v1/feedback/* + /api/v1/similar/*
    app.include_router(sentiment_router)  # /api/v1/sentiment/*
    app.include_router(alerts_router)  # /api/v1/alerts/* — exploits + webhooks
    app.include_router(news_feed_router)  # /api/v1/news/feed, /story, /trending, /topics
    app.include_router(health_router)  # /api/v1/health/full — aggregated dashboard
    app.include_router(content_router)  # /api/v1/content/* — distribution drafts
    app.include_router(daily_rug_router)  # /api/v1/content/daily-rug — publish + read
    app.include_router(federated_router)  # /api/v1/search/federated — multi-backend
    app.include_router(ta_router)  # /api/v1/ta/* — technical-analysis signals
    # Compatibility alias for older workers that call POST /embed directly.
    # The canonical documented route remains /api/v1/embed.
    app.add_api_route(
        "/embed",
        embed_text_route,
        methods=["POST"],
        tags=["rag-optimization"],
    )
