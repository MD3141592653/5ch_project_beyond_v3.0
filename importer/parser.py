"""JSONL parser and data normalizer for 5ch archive."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

URL_RE = re.compile(r"https?://[^\s<>\]\[\"']+", re.I)
ANCHOR_RE = re.compile(r">>(\d+)")
DATE_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})\([^)]*\)\s*(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?")


def parse_datetime(date_str: str | None) -> datetime | None:
    """Parses 5ch date string into a datetime object."""
    if not date_str:
        return None
    match = DATE_RE.search(date_str)
    if not match:
        return None
    year, month, day, hour, minute, second, fraction = match.groups()
    try:
        microsecond = int(fraction.ljust(6, "0")[:6]) if fraction else 0
        return datetime(int(year), int(month), int(day), int(hour), int(minute), int(second), microsecond)
    except (ValueError, OverflowError):
        return None


def parse_post_id(post_id_val: Any) -> int:
    """Safely converts post_id into an integer."""
    if isinstance(post_id_val, int):
        return post_id_val
    s = str(post_id_val or "").strip()
    if s.isdigit():
        return int(s)
    # Extract digits if any exist
    m = re.search(r"\d+", s)
    if m:
        return int(m.group(0))
    return 0


def normalize_post(obj: dict) -> dict[str, Any]:
    """Normalizes raw post JSON dictionary into database record format."""
    content = str(obj.get("content") or "")
    date_str = str(obj.get("date") or "")
    uid = str(obj.get("uid") or "").strip()
    post_id = parse_post_id(obj.get("post_id"))

    # System post detection
    post_type = (
        "system"
        if date_str == "Over 1000" or uid == "Thread" or post_id > 1000
        else "normal"
    )

    if uid.upper() in {"NG", "NONE", "NULL", "-", "ID:NONE", "ID:???"}:
        uid = ""

    post_dt = parse_datetime(date_str)

    # Extract URLs and anchors
    links = URL_RE.findall(content)
    anchors = [int(a) for a in ANCHOR_RE.findall(content) if a.isdigit()]

    return {
        "board": str(obj.get("board") or ""),
        "thread_id": str(obj.get("thread_id") or ""),
        "post_id": post_id,
        "name": str(obj.get("name") or ""),
        "uid": uid,
        "post_datetime": post_dt,
        "content": content,
        "post_type": post_type,
        "content_length": len(content),
        "links": links,
        "anchors": anchors,
    }


def normalize_thread(obj: dict) -> dict[str, Any]:
    """Normalizes raw thread JSON dictionary into database record format."""
    return {
        "thread_id": str(obj.get("thread_id") or ""),
        "board": str(obj.get("board") or ""),
        "title": str(obj.get("title") or ""),
        "url": str(obj.get("url") or ""),
        "raw_sha256": str(obj.get("raw_sha256") or ""),
    }
