"""Backward-compatible watchlist classes + the scam alert listener."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger("rugbrain.realtime_alerts")


class WatchlistManager:
    """Manage user watchlists and match new scam patterns."""

    @staticmethod
    async def add_watch(
        address: str, user_id: str, channels: list[str] | None = None
    ) -> dict[str, Any]:
        channels = channels or ["websocket"]
        key = f"rag:watch:{user_id}:{address}"
        watch = {
            "address": address.lower(),
            "user_id": user_id,
            "channels": channels,
            "added_at": datetime.now(UTC).isoformat(),
        }
        try:
            from rugbrain.compat import get_redis_async

            r = await get_redis_async()
            if r:
                await r.set(key, json.dumps(watch))
        except Exception as e:
            log.warning("add_watch: redis failed: %s", e)
        return watch

    @staticmethod
    async def remove_watch(address: str, user_id: str) -> bool:
        key = f"rag:watch:{user_id}:{address.lower()}"
        try:
            from rugbrain.compat import get_redis_async

            r = await get_redis_async()
            if r:
                await r.delete(key)
                return True
        except Exception as e:
            log.warning("remove_watch: redis failed: %s", e)
        return False

    @staticmethod
    async def get_watches(user_id: str) -> list[dict[str, Any]]:
        watches = []
        pattern = f"rag:watch:{user_id}:*"
        try:
            from rugbrain.compat import get_redis_async

            r = await get_redis_async()
            if r:
                keys = await r.keys(pattern)
                for key in keys:
                    data = await r.get(key)
                    if data:
                        watches.append(json.loads(data))
        except Exception as e:
            log.warning("get_watches: redis failed: %s", e)
        return watches

    @staticmethod
    async def match_alert(scam_doc: dict[str, Any]) -> list[tuple[Any, dict[str, Any]]]:
        """Check new scam against all active watches. Returns [(watch, alert)]."""
        matches = []
        scam_addresses = set()
        if scam_doc.get("address"):
            scam_addresses.add(scam_doc["address"].lower())
        if scam_doc.get("metadata", {}).get("contract_addresses"):
            for addr in scam_doc["metadata"]["contract_addresses"]:
                scam_addresses.add(addr.lower())
        try:
            from rugbrain.compat import get_redis_async

            r = await get_redis_async()
            if r:
                keys = await r.keys("rag:watch:*")
                for key in keys:
                    addr = key.decode().split(":")[-1]
                    if addr in scam_addresses:
                        data = await r.get(key)
                        if data:
                            watch = json.loads(data)
                            alert = {
                                "type": "scam_detected",
                                "address": scam_doc.get("address", addr),
                                "chain": scam_doc.get("chain", "unknown"),
                                "scam_type": scam_doc.get("scam_type", "unknown"),
                                "risk": scam_doc.get("severity", "medium"),
                                "summary": scam_doc.get("summary", "New scam pattern detected"),
                                "timestamp": datetime.now(UTC).isoformat(),
                            }
                            matches.append((watch, alert))
        except Exception as e:
            log.warning("match_alert: redis failed: %s", e)
        return matches


class AlertDispatcher:
    """Dispatch matched scam alerts to the channels a watch subscribes to."""

    @staticmethod
    async def dispatch(watch: dict[str, Any], alert: dict[str, Any]) -> None:
        channels = watch.get("channels", ["websocket"])
        for channel in channels:
            try:
                if channel == "websocket":
                    await AlertDispatcher._dispatch_websocket(watch["user_id"], alert)
                elif channel == "telegram":
                    await AlertDispatcher._dispatch_telegram(watch["user_id"], alert)
            except Exception as e:
                log.warning("dispatch %s failed: %s", channel, e)

    @staticmethod
    async def _dispatch_websocket(user_id: str, alert: dict[str, Any]) -> None:
        try:
            from rugbrain.compat import get_redis_async

            r = await get_redis_async()
            if r:
                channel = f"rag:alerts:{user_id}"
                await r.publish(channel, json.dumps(alert))
                log.info("alert websocket user=%s address=%s", user_id, alert.get("address"))
        except Exception as e:
            log.warning("websocket dispatch failed: %s", e)

    @staticmethod
    async def _dispatch_telegram(user_id: str, alert: dict[str, Any]) -> None:
        try:
            from rugbrain.compat import get_redis_async

            r = await get_redis_async()
            if r:
                msg = (
                    f"\U0001f6a8 *RMI Scam Alert*\n"
                    f"Address: `{alert.get('address')}`\n"
                    f"Type: {alert.get('scam_type')}\n"
                    f"Risk: {alert.get('risk')}\n"
                    f"Summary: {alert.get('summary')}\n"
                    f"Chain: {alert.get('chain')}"
                )
                await r.lpush("rag:telegram_queue", json.dumps({"user_id": user_id, "text": msg}))
        except Exception as e:
            log.warning("telegram dispatch failed: %s", e)


async def start_alert_listener() -> None:
    """Background task: listen for new scams, check against watchlists, dispatch."""
    log.info("rag_alert_listener: starting")
    while True:
        try:
            from rugbrain.compat import get_redis

            r = get_redis()
            if r:
                pubsub = r.pubsub()
                pubsub.subscribe("rag:new_scam")
                for message in pubsub.listen():
                    if message["type"] == "message":
                        try:
                            scam_doc = json.loads(message["data"])
                            matches = await WatchlistManager.match_alert(scam_doc)
                            for watch, alert in matches:
                                await AlertDispatcher.dispatch(watch, alert)
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            log.warning("alert_listener_error: %s — retrying in 5s", e)
            await asyncio.sleep(5)
