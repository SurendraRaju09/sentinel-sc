# -*- coding: utf-8 -*-
"""
integrations/serp_client.py
===========================
SerpAPI Ingestion Client with 3-Tier Fallback Chain.

Tier 1: Live SerpAPI Google News query (requires SERPAPI_API_KEY)
Tier 2: Local cache (integrations/cache/serp_cache.json)
Tier 3: Hardcoded canonical scenario fallback (demo day insurance)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import requests
from dotenv import load_dotenv

logger = logging.getLogger("SerpClient")

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
CACHE_FILE = BASE_DIR / "integrations" / "cache" / "serp_cache.json"

DEFAULT_SEARCH_QUERY = os.getenv(
    "DEFAULT_SEARCH_QUERY",
    "Port Klang port disruption OR delay OR strike OR weather"
).strip()

# Canonical fallback scenario block: Ensures demo never hangs on network or quota
HARDCODED_SCENARIO: Dict[str, Any] = {
    "search_metadata": {
        "status": "Hardcoded Scenario Fallback",
        "scenario_id": "SCENARIO-PORT-KLANG-01"
    },
    "search_parameters": {
        "engine": "google_news",
        "q": DEFAULT_SEARCH_QUERY
    },
    "news_results": [
        {
            "position": 1,
            "title": "Severe Tropical Storm Causes Major Congestion and Berth Delays at Port Klang",
            "link": "https://www.maritimenews-example.com/port-klang-congestion-2026",
            "source": {
                "name": "Maritime Trade & Logistics Gazette"
            },
            "date": "2026-10-02",
            "snippet": "Vessel operations at Malaysia's premier hub Port Klang face extensive delays of 7 to 10 days following tropical storm squalls and equipment downtime, impacting container feeder schedules including MV Sentinel Star across Malacca Strait."
        }
    ]
}


def fetch_from_live_api(query: str, timeout_seconds: int = 10) -> Optional[Dict[str, Any]]:
    """Fetches news results from SerpAPI Google News engine."""
    api_key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not api_key:
        logger.warning("No SERPAPI_API_KEY configured. Skipping Tier 1 live call.")
        return None

    url = "https://serpapi.com/search.json"
    params = {
        "engine": "google_news",
        "q": query,
        "api_key": api_key,
        "hl": "en",
        "gl": "us",
    }

    try:
        response = requests.get(url, params=params, timeout=timeout_seconds)
        if response.status_code == 200:
            data = response.json()
            if "error" in data:
                logger.warning(f"SerpAPI returned error: {data['error']}")
                return None
            return data
        logger.warning(f"SerpAPI returned status code {response.status_code}")
        return None
    except Exception as e:
        logger.warning(f"Live SerpAPI request failed: {e}")
        return None


def fetch_from_cache(cache_path: Path = CACHE_FILE) -> Optional[Dict[str, Any]]:
    """Loads results from local cache file."""
    if not cache_path.exists():
        logger.warning(f"Cache file {cache_path} does not exist.")
        return None

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read cache file: {e}")
        return None


def save_to_cache(data: Dict[str, Any], cache_path: Path = CACHE_FILE) -> bool:
    """Saves live results to cache file."""
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.warning(f"Failed to write to cache file: {e}")
        return False


def fetch_disruption_news(
    query: Optional[str] = None,
    force_tier: Optional[int] = None,
    timeout_seconds: int = 10,
    cache_path: Path = CACHE_FILE
) -> Tuple[Dict[str, Any], str]:
    """
    Executes the 3-Tier Fallback Chain:
    - Tier 1: Live SerpAPI query (saves to cache on success)
    - Tier 2: Read cache
    - Tier 3: Hardcoded scenario fallback
    
    Returns:
        (data_dict, source_tier_name)
    """
    search_query = query or DEFAULT_SEARCH_QUERY

    # Explicit force tier overrides (deterministic testing)
    if force_tier == 1:
        data = fetch_from_live_api(search_query, timeout_seconds=timeout_seconds)
        if data:
            save_to_cache(data, cache_path=cache_path)
            return data, "live_api"
        raise RuntimeError("Tier 1 (Live API) forced but failed.")

    if force_tier == 2:
        cached = fetch_from_cache(cache_path=cache_path)
        if cached:
            return cached, "cache"
        raise RuntimeError("Tier 2 (Cache) forced but cache file missing/invalid.")

    if force_tier == 3:
        return HARDCODED_SCENARIO, "hardcoded_scenario"

    # Dynamic fallback order
    live_data = fetch_from_live_api(search_query, timeout_seconds=timeout_seconds)
    if live_data and "news_results" in live_data and len(live_data["news_results"]) > 0:
        save_to_cache(live_data, cache_path=cache_path)
        return live_data, "live_api"

    cached_data = fetch_from_cache(cache_path=cache_path)
    if cached_data and "news_results" in cached_data and len(cached_data["news_results"]) > 0:
        return cached_data, "cache"

    return HARDCODED_SCENARIO, "hardcoded_scenario"
