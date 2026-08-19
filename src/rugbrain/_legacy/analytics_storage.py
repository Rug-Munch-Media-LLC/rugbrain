"""
Historical Data Storage & Analytics Module
==========================================

Provides persistent storage for:
- Transaction history
- Entity relationships
- Alert history
- Analytics queries

Uses Redis for fast-access cache and optional PostgreSQL for long-term storage.
"""

# NOTE (deprecated): archived in _legacy/ — kept for reference only, has zero
# live references, and is not type-checked in CI. Per-line pyright ignores
# below silence diagnostics that would otherwise require porting private
# monorepo dependencies that do not exist in the OSS rugbrain package.

import json
import logging
from datetime import datetime
from typing import Any, cast

import redis
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ─── PERSISTENT MODELS ────────────────────────────────────────────────


class TransactionRecord(BaseModel):
    """Represents a transaction record."""

    tx_hash: str
    chain: str = "ethereum"
    from_address: str
    to_address: str
    value: float = 0.0
    gas_used: int = 0
    gas_price: int = 0
    block_number: int = 0
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    status: int = 1  # 1 = success, 0 = failed
    function_name: str = ""
    token_transfers: list[dict[str, Any]] = Field(default_factory=list)


class WalletHistory(BaseModel):
    """Complete transaction history for a wallet."""

    wallet_address: str
    chain: str = "ethereum"
    transactions: list[TransactionRecord] = Field(default_factory=list)
    first_seen: datetime = Field(default_factory=datetime.utcnow)
    last_seen: datetime = Field(default_factory=datetime.utcnow)
    total_tx_count: int = 0
    total_volume: float = 0.0
    unique_interactions: int = 0


class EntityAlertRecord(BaseModel):
    """Record of an alert for an entity."""

    alert_id: str
    entity_id: str
    alert_type: str
    severity: str
    message: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
    resolved: bool = False


# ─── REDIS STORAGE ────────────────────────────────────────────────────


class RedisStorage:
    """Redis-backed storage for historical data."""

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0):
        self.host = host
        self.port = port
        self.db = db
        self.client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
        logger.info("Connected to Redis at %s:%s/%s", host, port, db)

    def key_prefix(self, category: str, identifier: str) -> str:
        """Generate a Redis key."""
        return f"rmi:{category}:{identifier}"

    def save_transaction(self, tx: TransactionRecord) -> bool:
        """Save a transaction record."""
        try:
            key = self.key_prefix("transaction", tx.tx_hash)
            self.client.setex(
                key,
                86400 * 30,  # 30 days TTL
                tx.json(),
            )

            # Index by wallet
            wallet_key = self.key_prefix("wallet", f"{tx.from_address}_{tx.chain}")
            self.client.zadd(wallet_key, {tx.tx_hash: tx.timestamp.timestamp()})

            # Update wallet history
            self._update_wallet_history(
                tx.wallet_address if hasattr(tx, "wallet_address") else tx.from_address,  # type: ignore[attr-defined]
                tx,  # pyright: ignore[reportAttributeAccessIssue]
            )

            return True
        except Exception as e:
            logger.error("Failed to save transaction: %s", e)
            return False

    def get_transaction(self, tx_hash: str) -> dict[str, Any] | None:
        """Get a transaction record."""
        try:
            key = self.key_prefix("transaction", tx_hash)
            data = self.client.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            logger.error("Failed to get transaction: %s", e)
            return None

    def get_wallet_history(
        self, wallet_address: str, chain: str = "ethereum"
    ) -> WalletHistory | None:
        """Get complete wallet history."""
        try:
            key = self.key_prefix("wallet", f"{wallet_address}_{chain}")

            if not self.client.exists(key):
                return None

            # decode_responses=True → members are str, scores are float at runtime;
            # redis types zrange as a union of overloads, so narrow explicitly.
            tx_hashes = cast(
                "list[tuple[str, float]]",
                self.client.zrange(key, 0, -1, withscores=True),
            )

            transactions = []
            for tx_hash, score in tx_hashes:
                tx = self.get_transaction(tx_hash)
                if tx:
                    tx_record = TransactionRecord(**tx)
                    tx_record.timestamp = datetime.fromtimestamp(score)
                    transactions.append(tx_record)

            # Sort by timestamp
            transactions.sort(key=lambda x: x.timestamp)

            return WalletHistory(
                wallet_address=wallet_address,
                chain=chain,
                transactions=transactions,
                total_tx_count=len(transactions),
            )
        except Exception as e:
            logger.error("Failed to get wallet history: %s", e)
            return None

    def save_alert(self, alert: EntityAlertRecord) -> bool:
        """Save an alert record."""
        try:
            key = self.key_prefix("alert", alert.alert_id)
            self.client.setex(
                key,
                86400 * 90,  # 90 days TTL
                alert.json(),
            )

            # Index by entity
            entity_key = self.key_prefix("entity_alert", alert.entity_id)
            self.client.zadd(entity_key, {alert.alert_id: alert.timestamp.timestamp()})

            return True
        except Exception as e:
            logger.error("Failed to save alert: %s", e)
            return False

    def get_entity_alerts(self, entity_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get alerts for an entity."""
        try:
            key = self.key_prefix("entity_alert", entity_id)

            if not self.client.exists(key):
                return []

            # decode_responses=True → alert ids are str at runtime; narrow the
            # zrange union so get_alert(str) type-checks.
            alert_ids = cast("list[str]", self.client.zrange(key, 0, limit - 1))

            alerts = []
            for alert_id in alert_ids:
                alert = self.get_alert(alert_id)
                if alert:
                    alerts.append(alert)

            return alerts
        except Exception as e:
            logger.error("Failed to get entity alerts: %s", e)
            return []

    def get_alert(self, alert_id: str) -> dict[str, Any] | None:
        """Get an alert record."""
        try:
            key = self.key_prefix("alert", alert_id)
            data = self.client.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            logger.error("Failed to get alert: %s", e)
            return None

    def save_entity_relation(self, from_entity: str, to_entity: str, relation: str) -> None:
        """Save an entity relationship."""
        try:
            key = self.key_prefix("entity_relation", from_entity)
            self.client.sadd(key, json.dumps({"to": to_entity, "relation": relation}))
        except Exception as e:
            logger.error("Failed to save entity relation: %s", e)

    def get_entity_relations(self, entity_id: str) -> list[dict[str, str]]:
        """Get relations for an entity."""
        try:
            key = self.key_prefix("entity_relation", entity_id)
            relations = self.client.smembers(key)
            return [json.loads(r) for r in relations]
        except Exception as e:
            logger.error("Failed to get entity relations: %s", e)
            return []

    def save_wallet_cluster(self, cluster_id: str, members: list[str], labels: list[str]) -> None:
        """Save a wallet cluster."""
        try:
            key = self.key_prefix("cluster", cluster_id)
            data = {
                "cluster_id": cluster_id,
                "members": members,
                "labels": labels,
                "created_at": datetime.utcnow().isoformat(),
            }
            self.client.setex(key, 86400 * 365, json.dumps(data))  # 1 year TTL
        except Exception as e:
            logger.error("Failed to save cluster: %s", e)

    def get_wallet_cluster(self, cluster_id: str) -> dict[str, Any] | None:
        """Get a wallet cluster."""
        try:
            key = self.key_prefix("cluster", cluster_id)
            data = self.client.get(key)
            return json.loads(data) if data else None
        except Exception as e:
            logger.error("Failed to get cluster: %s", e)
            return None

    def get_or_create_wallet_history(
        self, wallet_address: str, chain: str = "ethereum"
    ) -> WalletHistory:
        """Get or create wallet history."""
        history = self.get_wallet_history(wallet_address, chain)
        if history is None:
            history = WalletHistory(wallet_address=wallet_address, chain=chain)
        return history

    def _update_wallet_history(self, wallet_address: str, tx: TransactionRecord) -> None:
        """Update wallet history metadata."""
        history = self.get_or_create_wallet_history(wallet_address, tx.chain)

        # Update last seen
        history.last_seen = tx.timestamp

        # Update total volume
        history.total_volume += tx.value

        # Update interaction count
        if tx.to_address not in [t.to_address for t in history.transactions]:
            history.unique_interactions += 1

        # Update transaction count
        history.total_tx_count = len(history.transactions) + 1


# ─── DATABASE WRAPPER ─────────────────────────────────────────────────


class AnalyticsDatabase:
    """Database wrapper for analytics queries."""

    def __init__(self, redis_host: str = "localhost", redis_port: int = 6379, redis_db: int = 0):
        self.redis = RedisStorage(host=redis_host, port=redis_port, db=redis_db)

    def store_transaction(self, tx: TransactionRecord) -> bool:
        """Store a transaction."""
        return self.redis.save_transaction(tx)

    def store_entity_alert(self, alert: EntityAlertRecord) -> bool:
        """Store an entity alert."""
        return self.redis.save_alert(alert)

    def store_entity_relation(self, from_entity: str, to_entity: str, relation: str) -> None:
        """Store an entity relationship."""
        self.redis.save_entity_relation(from_entity, to_entity, relation)

    def store_wallet_cluster(self, cluster_id: str, members: list[str], labels: list[str]) -> None:
        """Store a wallet cluster."""
        self.redis.save_wallet_cluster(cluster_id, members, labels)

    def get_wallet_history(
        self, wallet_address: str, chain: str = "ethereum"
    ) -> WalletHistory | None:
        """Get wallet history."""
        return self.redis.get_wallet_history(wallet_address, chain)

    def get_entity_alerts(self, entity_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get entity alerts."""
        return self.redis.get_entity_alerts(entity_id, limit)

    def get_entity_relations(self, entity_id: str) -> list[dict[str, str]]:
        """Get entity relations."""
        return self.redis.get_entity_relations(entity_id)

    def get_wallet_cluster(self, cluster_id: str) -> dict[str, Any] | None:
        """Get wallet cluster."""
        return self.redis.get_wallet_cluster(cluster_id)

    def get_transaction(self, tx_hash: str) -> dict[str, Any] | None:
        """Get transaction by hash."""
        return self.redis.get_transaction(tx_hash)

    # ─── ANALYTICS QUERIES ────────────────────────────────────────────

    def get_wallet_activity_summary(
        self, wallet_address: str, chain: str = "ethereum"
    ) -> dict[str, Any]:
        """Get activity summary for a wallet."""
        history = self.redis.get_wallet_history(wallet_address, chain)

        if history is None or not history.transactions:
            return {
                "wallet_address": wallet_address,
                "chain": chain,
                "total_transactions": 0,
                "total_volume": 0,
                "first_seen": None,
                "last_seen": None,
                "unique_contracts": 0,
            }

        return {
            "wallet_address": wallet_address,
            "chain": chain,
            "total_transactions": len(history.transactions),
            "total_volume": history.total_volume,
            "first_seen": history.first_seen.isoformat(),
            "last_seen": history.last_seen.isoformat(),
            "unique_contracts": history.unique_interactions,
        }

    def get_wallet_similarity(self, address1: str, address2: str) -> dict[str, Any]:
        """Calculate similarity between two wallets based on interactions."""
        history1 = self.redis.get_wallet_history(address1, "ethereum")
        history2 = self.redis.get_wallet_history(address2, "ethereum")

        if not history1 or not history2 or not history1.transactions or not history2.transactions:
            return {"similarity": 0, "shared_contracts": [], "reason": "Insufficient data"}

        # Get unique contract interactions
        contracts1 = {t.to_address for t in history1.transactions}
        contracts2 = {t.to_address for t in history2.transactions}

        # Calculate Jaccard similarity
        intersection = contracts1 & contracts2
        union = contracts1 | contracts2

        jaccard = len(intersection) / len(union) if union else 0

        return {
            "similarity": round(jaccard, 4),
            "shared_contracts": list(intersection)[:10],  # Top 10
            "total_shared": len(intersection),
            "unique_1": len(contracts1 - contracts2),
            "unique_2": len(contracts2 - contracts1),
        }

    def get_entity_network(self, entity_id: str, depth: int = 2) -> dict[str, Any]:
        """Get entity's network of connected entities."""
        relations = self.redis.get_entity_relations(entity_id)

        network: dict[str, Any] = {
            "entity_id": entity_id,
            "direct_relations": relations,
            "depth": depth,
        }

        # If depth > 0, get relations of related entities
        if depth > 0:
            related_entities = [r["to"] for r in relations]
            network["related_entities"] = related_entities

            if depth >= 2:
                network["second_degree"] = []
                for related in related_entities:
                    second_degree = self.redis.get_entity_relations(related)
                    network["second_degree"].extend(second_degree)

        return network


# ─── SINGLETON INSTANCE ───────────────────────────────────────────────

_db_instance: AnalyticsDatabase | None = None


def get_analytics_database(
    redis_host: str | None = None, redis_port: int | None = None, redis_db: int | None = None
) -> AnalyticsDatabase:
    """Get the analytics database instance."""
    global _db_instance

    if _db_instance is None:
        _db_instance = AnalyticsDatabase(
            redis_host=redis_host or "localhost",
            redis_port=redis_port or 6379,
            redis_db=redis_db or 0,
        )

    return _db_instance


# ─── INITIAL DATA IMPORT ──────────────────────────────────────────────


def initialize_analytics() -> None:
    """Initialize analytics storage with default data."""
    get_analytics_database()

    # Clear old data (optional - for fresh starts)
    # db.redis.client.flushdb()

    logger.info("Analytics database initialized")


if __name__ == "__main__":
    # Test the analytics database
    db = get_analytics_database()

    # Create a test transaction
    tx = TransactionRecord(
        tx_hash="0x" + "a" * 64,
        chain="ethereum",
        from_address="0x1234567890123456789012345678901234567890",
        to_address="0xabcdef1234567890abcdef1234567890abcdef12",
        value=1.5,
        gas_used=21000,
        block_number=1000000,
        function_name="transfer",
    )

    db.store_transaction(tx)

    # Get the transaction back
    stored = db.get_transaction(tx.tx_hash)
    print(f"Stored transaction: {stored}")

    # Get wallet history
    history = db.get_wallet_history(tx.from_address, "ethereum")
    if history:
        print(f"Wallet history: {history.total_tx_count} transactions")

    # Get activity summary
    summary = db.get_wallet_activity_summary(tx.from_address, "ethereum")
    print(f"Activity summary: {summary}")
