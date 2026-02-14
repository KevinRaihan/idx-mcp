"""News site scraping for Indonesian financial media (Kontan, Bisnis, CNBC Indonesia)."""

import asyncio
import logging
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from ..utils.time_utils import WIB

logger = logging.getLogger("idx-mcp.scrapers.news_sites")

HEADERS = {
    "User-Agent": "idx-mcp/1.0 (personal research tool)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
}

# News source configurations
NEWS_SOURCES = [
    {
        "name": "kontan.co.id",
        "search_url": "https://www.kontan.co.id/search/?search={query}",
        "article_selector": "ul.list-berita li",
        "title_selector": "h1 a, h2 a, h3 a, .title a",
        "link_selector": "a",
        "snippet_selector": "p, .lead, .txt",
        "date_selector": ".date, time, .fs12",
    },
    {
        "name": "bisnis.com",
        "search_url": "https://www.bisnis.com/index?c=0&q={query}",
        "article_selector": ".list-news .item, .indeks-news .item, article",
        "title_selector": "h2 a, h3 a, .title a",
        "link_selector": "a",
        "snippet_selector": "p, .description",
        "date_selector": ".date, time, span.text-muted",
    },
    {
        "name": "cnbcindonesia.com",
        "search_url": "https://www.cnbcindonesia.com/search?query={query}",
        "article_selector": ".list .media, .box_list, article",
        "title_selector": "h2 a, h4 a, .title a, a.title",
        "link_selector": "a",
        "snippet_selector": "p, .desc",
        "date_selector": ".date, time, span.text-xs",
    },
]


async def scrape_news(ticker: str, limit: int = 10) -> dict:
    """Scrape recent news for a ticker from Indonesian financial media.

    Returns dict with articles list and metadata.
    """
    all_articles = []
    sources_searched = []
    sources_failed = []

    async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
        for source in NEWS_SOURCES:
            source_name = source["name"]
            try:
                await asyncio.sleep(1.5)  # Politeness delay between sources
                url = source["search_url"].format(query=ticker)
                resp = await client.get(url)
                resp.raise_for_status()

                soup = BeautifulSoup(resp.text, "lxml")
                articles = _parse_articles(soup, source, source_name)
                all_articles.extend(articles)
                sources_searched.append(source_name)

            except Exception as e:
                logger.warning(f"Failed to scrape {source_name} for {ticker}: {e}")
                sources_failed.append(source_name)
                continue

    # Deduplicate by title similarity
    all_articles = _deduplicate_articles(all_articles)

    # Sort by date (newest first), then limit
    all_articles.sort(key=lambda x: x.get("published", ""), reverse=True)
    all_articles = all_articles[:limit]

    result = {
        "articles": all_articles,
        "article_count": len(all_articles),
        "sources_searched": sources_searched,
    }

    if sources_failed:
        result["sources_failed"] = sources_failed

    return result


def _parse_articles(soup: BeautifulSoup, source: dict, source_name: str) -> list[dict]:
    """Parse articles from a news source page."""
    articles = []

    # Try the configured selector first, then fallback to generic
    article_elements = soup.select(source["article_selector"])
    if not article_elements:
        # Fallback: look for common article patterns
        article_elements = soup.find_all(["article", "li", "div"], class_=True, limit=20)

    for elem in article_elements[:20]:
        try:
            # Extract title
            title_elem = elem.select_one(source["title_selector"])
            if not title_elem:
                title_elem = elem.find("a")
            if not title_elem:
                continue

            title = title_elem.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            # Extract link
            link_elem = elem.select_one(source["link_selector"])
            url = ""
            if link_elem and link_elem.get("href"):
                url = link_elem["href"]
                if url.startswith("/"):
                    url = f"https://{source_name}{url}"

            # Extract snippet
            snippet = ""
            snippet_elem = elem.select_one(source["snippet_selector"])
            if snippet_elem:
                snippet = snippet_elem.get_text(strip=True)[:200]

            # Extract date
            published = ""
            date_elem = elem.select_one(source["date_selector"])
            if date_elem:
                published = date_elem.get_text(strip=True)

            articles.append({
                "title": title,
                "source": source_name,
                "url": url,
                "published": published,
                "snippet": snippet,
            })

        except Exception:
            continue

    return articles


def _deduplicate_articles(articles: list[dict]) -> list[dict]:
    """Remove duplicate articles based on title similarity."""
    seen_titles = set()
    unique = []

    for article in articles:
        # Normalize title for comparison
        normalized = article["title"].lower().strip()
        # Simple dedup: check if exact or very similar title seen
        if normalized not in seen_titles:
            seen_titles.add(normalized)
            unique.append(article)

    return unique
