"""Crawl-state persistence for resumable AMAC bulk + incremental runs.

Atomic write via tmp-file + os.replace. Corrupt JSON returns None so the
caller starts fresh rather than crashing.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CrawlState:
    mode: str = "bulk"  # "bulk" | "incr"
    started_at: str = field(default_factory=_now_iso)
    finished_at: str | None = None
    page_size: int = 100
    sleep_seconds: float = 0.5
    filter_body: dict[str, Any] = field(default_factory=dict)
    sort: str | None = None
    total_pages_at_start: int | None = None
    total_elements_at_start: int | None = None
    last_page_completed: int = -1  # -1 means "no page done yet"
    rows_collected: int = 0
    max_put_on_record_date_seen: str | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    batches_written: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> CrawlState:
        data = json.loads(raw)
        return cls(**data)


def write_state(path: Path, state: CrawlState) -> None:
    """Atomic write of crawl state to JSON path.

    Writes to `{path}.tmp` first, fsyncs, then os.replace()'s into place.
    A crash partway through leaves only the tmp file (cleaned up on the
    next successful write).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = state.to_json()
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_state(path: Path) -> CrawlState | None:
    """Read state from JSON path. Returns None if missing or corrupt."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        return CrawlState.from_json(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, TypeError, KeyError):
        return None
