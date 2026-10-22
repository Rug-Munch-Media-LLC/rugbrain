"""Classify verified accounts: category, trust, and squat/impersonation verdicts."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

SHILL_RE = re.compile(
    r"(paid (promo|shill)|dm (me )?for (calls|promo)|signals? group|join .{0,20}calls?\b)",
    re.IGNORECASE,
)
ENTITY_RE = re.compile(r"(official|foundation|news|protocol|labs|team|alert)\b", re.IGNORECASE)
STOP = {
    "the",
    "inc",
    "labs",
    "lab",
    "team",
    "official",
    "foundation",
    "protocol",
    "network",
    "finance",
    "xyz",
    "hq",
    "app",
    "io",
    "dao",
}


def _tokens(value: str | None) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (value or "").lower()) if t and t not in STOP}


def _days_old(created_at: str | None) -> float | None:
    try:
        return (datetime.now(tz=timezone.utc) - datetime.fromisoformat(created_at or "")).days
    except ValueError:
        return None


def _name_match(expected: str | None, observed: str | None, handle: str) -> bool:
    """Brand-name overlap between what we asked for and what X returned."""
    obs = _tokens(observed)
    return (
        bool(_tokens(expected) & obs)
        or handle.lower() in {t.lower() for t in (observed or "").split()}
        or handle.lower().replace("_", "")
        in (observed or "").lower().replace(" ", "").replace("_", "")
    )


def _squat(item: dict[str, Any], observed: dict[str, Any], reason: str) -> dict[str, Any]:
    user = observed.get("screen_name")
    return {
        "handle": item["handle"],
        "observed_handle": user,
        "observed_name": observed.get("name"),
        "followers": observed.get("followers"),
        "created_at": observed.get("created_at"),
        "category": "squat",
        "trust": 0.0,
        "status": "rejected",
        "reason": reason,
        "evidence": (
            f"api.fxtwitter.com/{item['handle']} -> @{user} "
            f'"{observed.get("name")}" followers={observed.get("followers")} '
            f"created={str(observed.get('created_at'))[:10]}"
        ),
        "evidence_url": f"https://x.com/{user}" if user else None,
        "provenance": item["provenance"],
    }


def verdict(
    item: dict[str, Any],
    policy: dict[str, Any],
    probes: set[str],
    renames: dict[str, str],
    probe_targets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    observed = item["x"]
    handle = item["handle"]
    site = observed.get("site") or {}
    if observed.get("status") != "ok":
        return {
            "handle": handle,
            "category": "unknown",
            "trust": 0.0,
            "status": "rejected",
            "reason": "no_such_account",
            "evidence": f"api.fxtwitter.com/{handle} -> {observed.get('message')} "
            f"(attempts {observed.get('attempts')})",
            "followers": None,
            "provenance": item["provenance"],
        }

    screen = observed.get("screen_name") or ""
    followers = int(observed.get("followers") or 0)
    age_days = _days_old(observed.get("created_at"))
    category = item.get("category") or "kol"
    expected_name = item.get("name") or handle
    target = probe_targets.get(handle.lower())
    if target:
        category, expected_name = target["category"], target["name"]
    matched = (_name_match(expected_name, observed.get("name"), handle)
               or bool(site.get("linked"))
               or (screen.lower() == handle.lower() and followers >= 5000))
    floor = policy["follower_floor"].get(category, 300)

    if handle.lower() in probes and not target:
        return _squat(item, observed, "squat_probe_hit")
    if followers < floor:
        return {
            "handle": handle,
            "observed_handle": screen,
            "observed_name": observed.get("name"),
            "category": "unknown",
            "trust": 0.0,
            "status": "rejected",
            "reason": "below_follower_floor",
            "evidence": f"@{screen} followers={followers} < floor {floor} for {category}",
            "followers": followers,
            "created_at": observed.get("created_at"),
            "provenance": item["provenance"],
        }
    if not matched and handle.lower() not in renames:
        return _squat(item, observed, "name_mismatch_impersonation")
    max_age = policy.get("squat_max_age_days", 120)
    if (
        category in ("news_primary", "research", "security", "aggregator")
        and age_days is not None
        and age_days < max_age
        and followers < 50_000
    ):
        return _squat(item, observed, "new_account_claims_established_entity")
    if SHILL_RE.search(observed.get("bio") or "") and "trust" not in item:
        category = "shill"

    trust = float(item.get("trust", policy["category_trust"].get(category, 0.4)))
    if category == "shill":
        trust = policy["category_trust"]["shill"]
    return {
        "handle": screen,
        "name": observed.get("name"),
        "category": category,
        "followers": followers,
        "trust": round(trust, 2),
        "verified_at": item["verified_at"],
        "evidence_url": f"https://x.com/{screen}",
        "site_url": site.get("url"),
        "site_status": ("linked" if site.get("linked") else site.get("status", "n/a")),
        "site_links": site.get("handles") or [],
        "created_at": observed.get("created_at"),
        "account_age_days": age_days,
        "verified_badge": observed.get("verified_badge"),
        "provenance": item["provenance"],
        "status": "verified",
    }
