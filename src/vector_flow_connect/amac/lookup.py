"""Targeted-lookup helpers built on top of `AMACClient`.

v1 entry point. Bulk crawl + incrementer (v2) will live alongside this.
"""

from __future__ import annotations

from vector_flow_connect.amac.client import AMACClient
from vector_flow_connect.amac.parse import (
    merge_detail_into_record,
    parse_detail_html,
    parse_search_response,
)
from vector_flow_connect.amac.schema import AMACRecord


def search_by_name(
    client: AMACClient,
    name: str,
    *,
    max_results: int = 100,
    page_size: int = 50,
    enrich_with_detail: bool = False,
) -> list[AMACRecord]:
    """Substring-search AMAC fundName field; return up to `max_results` rows.

    AMAC's `keyword` filter is a fuzzy substring match against `fundName`.
    Full formal names often miss (the registered name has a `...私募证券投资基金`
    suffix that the colloquial DKU name lacks) — callers should pass shorter
    distinguishing tokens (e.g. `睿见1号`, not `睿远基金睿见1号`).

    If `enrich_with_detail=True`, each row is enriched by fetching its detail
    page and merging in the detail-only fields. This adds 1 request per row.
    """
    all_rows: list[AMACRecord] = []
    page = 0
    while True:
        payload = client.search(keyword=name, page=page, size=page_size)
        rows = parse_search_response(payload)
        all_rows.extend(rows)
        if len(all_rows) >= max_results or payload.get("last", True) or not rows:
            break
        page += 1

    all_rows = all_rows[:max_results]

    if enrich_with_detail:
        for i, row in enumerate(all_rows):
            iid = row.get("internal_id")
            if not iid:
                continue
            html = client.fetch_detail_html(iid)
            detail = parse_detail_html(html)
            all_rows[i] = merge_detail_into_record(row, detail)

    return all_rows
