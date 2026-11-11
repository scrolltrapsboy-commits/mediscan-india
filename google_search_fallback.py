"""Google Search fallback used only when DrugSetu returns a genuine empty result.

If an existing Google Custom Search JSON API account is configured, fetches
structured results. Otherwise returns a Google Search URL for the user to open.
Never scrapes Google HTML and never treats snippets as verified medical facts.
"""
from __future__ import annotations

import os
from urllib.parse import urlencode

import requests

GOOGLE_CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
TIMEOUT = (4, 10)


def build_query(names: list[str], strengths: list[str], manufacturers: list[str]) -> str:
    terms = []
    for value in (names or [])[:2] + (strengths or [])[:2] + (manufacturers or [])[:1]:
        value = " ".join(str(value).split()).strip()
        if value and value.lower() not in [x.lower() for x in terms]:
            terms.append(value)
    if not terms:
        terms = ["medicine identification"]
    return " ".join(terms) + " medicine India"


def search_medicine(names: list[str], strengths: list[str], manufacturers: list[str]) -> dict:
    query = build_query(names, strengths, manufacturers)
    search_url = "https://www.google.com/search?" + urlencode({"q": query})
    api_key = os.getenv("GOOGLE_CSE_API_KEY", "").strip()
    cx = os.getenv("GOOGLE_CSE_CX", "").strip()

    result = {"configured": bool(api_key and cx), "items": [], "search_url": search_url}
    if not (api_key and cx):
        return result

    try:
        response = requests.get(
            GOOGLE_CSE_ENDPOINT,
            params={"key": api_key, "cx": cx, "q": query, "num": 5, "gl": "in", "hl": "en"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        # Keep the manual Google Search link available if the API is unavailable.
        return result

    for item in payload.get("items", [])[:5]:
        link = item.get("link")
        if not isinstance(link, str) or not link.startswith("https://"):
            continue
        result["items"].append({
            "title": str(item.get("title") or link)[:300],
            "link": link,
            "snippet": str(item.get("snippet") or "")[:700],
            "displayLink": str(item.get("displayLink") or "")[:150],
        })
    return result
