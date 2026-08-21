"""Firehose adapter: pull a public RSS/Atom changelog feed into FirehoseEntry values.

Stdlib-only (urllib + ElementTree) per the proportionality doctrine. Scheduling is the
caller's job (cron/APScheduler invokes `pre ingest-firehose`).
"""

from __future__ import annotations

import re
import urllib.request
from datetime import UTC, datetime
from xml.etree import ElementTree

from pre.change_corpus import FirehoseEntry

USER_AGENT = "PersonalRelevanceEngine/0.1 (+firehose reader)"
_TIMEOUT_SECONDS = 30


def fetch_feed(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        raw: bytes = response.read()
    return raw.decode("utf-8", errors="replace")


def _text(node: ElementTree.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _parse_datetime(value: str) -> datetime | None:
    value = value.strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            parsed = datetime.strptime(value, fmt)  # noqa: DTZ007 -- naive handled below
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def parse_feed(xml: str) -> list[FirehoseEntry]:
    """Parse RSS 2.0 or Atom into entries. Product name is derived from the feed/item."""
    root = ElementTree.fromstring(xml)
    entries: list[FirehoseEntry] = []

    channel = root.find("channel")
    if channel is not None:  # RSS 2.0
        feed_title = _text(channel.find("title"))
        for item in channel.findall("item"):
            title = _text(item.find("title"))
            if not title:
                continue
            link = _text(item.find("link")) or None
            pub = _parse_datetime(_text(item.find("pubDate")))
            description = re.sub(r"<[^>]+>", "", _text(item.find("description")))
            entries.append(
                FirehoseEntry(
                    product_name=_text(item.find("category")) or feed_title or "unknown",
                    title=title,
                    url=link,
                    published_at=pub,
                    summary=description[:512],
                )
            )
        return entries

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    feed_title = _text(root.find("atom:title", ns))
    for entry in root.findall("atom:entry", ns):
        title = _text(entry.find("atom:title", ns))
        if not title:
            continue
        link_node = entry.find("atom:link", ns)
        link = link_node.get("href") if link_node is not None else None
        published = _parse_datetime(
            _text(entry.find("atom:published", ns)) or _text(entry.find("atom:updated", ns))
        )
        summary = re.sub(r"<[^>]+>", "", _text(entry.find("atom:summary", ns)))
        entries.append(
            FirehoseEntry(
                product_name=feed_title or "unknown",
                title=title,
                url=link,
                published_at=published,
                summary=summary[:512],
            )
        )
    return entries
