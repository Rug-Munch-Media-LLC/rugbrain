"""Configuration for the X GraphQL lane.

X rotates GraphQL query ids without notice. When the lane starts answering
404 with an empty body, the fix is an env override — never a code change:

    X_QUERY_USER_BY_SCREEN_NAME=...
    X_QUERY_USER_TWEETS=...

The bearer below is the **public web-client token**, shipped inside X's own
JavaScript bundle. It is not an operator credential and grants no account
access on its own; the account context comes from the session cookies.
"""

from __future__ import annotations

import os

PUBLIC_BEARER = os.getenv(
    "X_BEARER_TOKEN",
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
)

GRAPHQL_BASE = os.getenv("X_GRAPHQL_BASE", "https://x.com/i/api/graphql")
ACTIVATE_URL = os.getenv("X_ACTIVATE_URL", "https://api.twitter.com/1.1/guest/activate.json")

QUERY_USER_BY_SCREEN_NAME = os.getenv("X_QUERY_USER_BY_SCREEN_NAME", "sLVLhk0bGj3MVFEKTdax1w")
QUERY_USER_TWEETS = os.getenv("X_QUERY_USER_TWEETS", "E3opETHurmVJflFsUBVuUQ")

# A real Firefox UA is load-bearing: the Cloudflare edge answers 404 with an
# empty body for requests whose UA is missing or non-browser.
USER_AGENT = os.getenv(
    "X_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
)

COOKIE_PATH = os.getenv("X_COOKIE_PATH", "/srv/secrets/firefox-cookies.json")
COOKIE_DOMAINS = (".x.com", "x.com", ".twitter.com", "twitter.com")

#: Politeness floor — one request per second, always.
REQUESTS_PER_SECOND = float(os.getenv("X_REQUESTS_PER_SECOND", "1.0"))
MAX_429_RETRIES = int(os.getenv("X_MAX_429_RETRIES", "2"))
MAX_TIMELINE_PAGES = int(os.getenv("X_MAX_TIMELINE_PAGES", "2"))

#: Trust by source class (consumed by the news ranking formula).
SOURCE_TRUST: dict[str, float] = {
    "security": 0.95,
    "news": 0.85,
    "research": 0.85,
    "ecosystem": 0.80,
}

USER_FEATURES: dict[str, bool] = {
    "hidden_profile_subscriptions_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}

TWEET_FEATURES: dict[str, bool] = {
    "rweb_video_screen_enabled": False,
    "payments_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}
