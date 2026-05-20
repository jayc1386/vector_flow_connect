"""One-shot reconnaissance of the AMAC 私募 fund disclosure site.

Decides Path A (JSON XHR endpoint -> httpx) vs Path B (DOM scraping -> Playwright)
and inventories the live detail-page fields (the schema source).

Output (data/raw/amac_recon/, gitignored):
  - network.jsonl       every request/response with content-type, status, size
  - index_page.html     rendered list page after JS settles
  - detail_page.html    rendered detail page for one fund
  - detail_fields.json  {label: value} pairs from the detail page (schema source)
  - screenshot.png      visual sanity check
  - summary.json        machine-readable decision payload

Run:
  uv run python -m amac.recon
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import Page, Request, Response, async_playwright

INDEX_URL = "http://gs.amac.org.cn/amac-infodisc/res/pof/fund/index.html"
OUT_DIR = Path("data/raw/amac_recon")
PAGE_LOAD_TIMEOUT_MS = 30_000
SETTLE_MS = 2_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _capture_network(page: Page, log: list[dict]) -> None:
    """Attach listeners that append every request/response to `log`."""

    def on_request(req: Request) -> None:
        log.append(
            {
                "kind": "request",
                "ts": _now_iso(),
                "method": req.method,
                "url": req.url,
                "resource_type": req.resource_type,
                "post_data": req.post_data,
            }
        )

    async def on_response(resp: Response) -> None:
        entry = {
            "kind": "response",
            "ts": _now_iso(),
            "url": resp.url,
            "status": resp.status,
            "headers": dict(resp.headers),
        }
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype.lower() or "javascript" in ctype.lower():
            try:
                body = await resp.text()
                if len(body) < 200_000:
                    entry["body_preview"] = body[:50_000]
            except Exception as exc:
                entry["body_error"] = str(exc)
        log.append(entry)

    page.on("request", on_request)
    page.on("response", lambda r: asyncio.create_task(on_response(r)))


async def _try_first_detail_link(page: Page) -> str | None:
    """Find the first fund-detail link on the index page, if rendered."""
    # Old selector: //tbody/tr/td/a[@class='ajaxify'] -- try plus fallbacks.
    candidates = [
        "a.ajaxify[href*='fund']",
        "table#fundlist a[href*='fund']",
        "a[href*='detail']",
        "table tbody a",
    ]
    for sel in candidates:
        try:
            href = await page.locator(sel).first.get_attribute("href", timeout=2_000)
            if href:
                return href if href.startswith("http") else f"http://gs.amac.org.cn{href}"
        except Exception:
            continue
    return None


async def _inventory_detail(page: Page) -> dict[str, str]:
    """Pull every label/value pair we can find on the detail page.

    Returns a dict keyed by label text (Chinese); values are stripped strings.
    Tries multiple heuristics since the layout may have changed since 2014.
    """
    fields: dict[str, str] = {}

    # Heuristic 1: legacy `.td-content` divs/spans (ordered)
    try:
        td_contents = await page.locator(".td-content").all_inner_texts()
        if td_contents:
            for i, val in enumerate(td_contents):
                fields[f"td-content[{i}]"] = val.strip()
    except Exception as exc:
        fields["_td_content_error"] = str(exc)

    # Heuristic 2: definition-list-style label/value tables (th/td rows)
    try:
        rows = await page.locator("table tr").all()
        for row in rows:
            try:
                cells = await row.locator("th, td").all_inner_texts()
                # Pair adjacent label/value cells
                for i in range(0, len(cells) - 1, 2):
                    label = cells[i].strip().rstrip(":：")
                    value = cells[i + 1].strip()
                    if label and value and label not in fields:
                        fields[label] = value
            except Exception:
                continue
    except Exception as exc:
        fields["_table_error"] = str(exc)

    # Heuristic 3: label-text + sibling-text pattern
    try:
        labels = await page.locator("label, .label, dt").all_inner_texts()
        for lbl in labels:
            stripped = lbl.strip().rstrip(":：")
            if stripped and stripped not in fields:
                fields.setdefault(f"_label:{stripped}", "")
    except Exception:
        pass

    return fields


async def recon() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    network_log: list[dict] = []
    summary: dict = {"started_at": _now_iso(), "url": INDEX_URL}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="zh-CN",
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()
        await _capture_network(page, network_log)

        # --- Index page load -------------------------------------------
        print(f"[recon] GET {INDEX_URL}")
        try:
            await page.goto(INDEX_URL, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded")
        except Exception as exc:
            summary["goto_error"] = str(exc)
        # networkidle is best-effort.
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=PAGE_LOAD_TIMEOUT_MS)
        await page.wait_for_timeout(SETTLE_MS)

        # Save index HTML + screenshot
        (OUT_DIR / "index_page.html").write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=str(OUT_DIR / "screenshot_index.png"), full_page=True)

        # Count rows visible to see if data rendered without further interaction
        try:
            row_count_initial = await page.locator("table tbody tr").count()
        except Exception:
            row_count_initial = -1
        summary["row_count_on_load"] = row_count_initial

        # --- Try a search to see if/which XHRs fire ---------------------
        # Old code: select 100-per-page, click last-page.
        # Just attempt: change page size if dropdown exists; record any XHRs.
        try:
            select = page.locator("select[name='fundlist_length']")
            if await select.count() > 0:
                await select.select_option(index=3)  # 100 per page
                await page.wait_for_timeout(SETTLE_MS)
                summary["page_size_change_ok"] = True
        except Exception as exc:
            summary["page_size_change_error"] = str(exc)

        # Pagination next-page click to capture pagination XHR
        try:
            nxt = page.locator("a.paginate_button.next, a.next")
            if await nxt.count() > 0:
                await nxt.first.click()
                await page.wait_for_timeout(SETTLE_MS)
                summary["pagination_click_ok"] = True
        except Exception as exc:
            summary["pagination_click_error"] = str(exc)

        # --- Detail page -----------------------------------------------
        detail_url = await _try_first_detail_link(page)
        summary["first_detail_url"] = detail_url
        if detail_url:
            print(f"[recon] GET detail {detail_url}")
            try:
                await page.goto(
                    detail_url, timeout=PAGE_LOAD_TIMEOUT_MS, wait_until="domcontentloaded"
                )
                await page.wait_for_load_state("networkidle", timeout=PAGE_LOAD_TIMEOUT_MS)
            except Exception as exc:
                summary["detail_goto_error"] = str(exc)
            await page.wait_for_timeout(SETTLE_MS)

            (OUT_DIR / "detail_page.html").write_text(await page.content(), encoding="utf-8")
            await page.screenshot(path=str(OUT_DIR / "screenshot_detail.png"), full_page=True)

            fields = await _inventory_detail(page)
            (OUT_DIR / "detail_fields.json").write_text(
                json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            summary["detail_field_count"] = len(fields)

        await browser.close()

    # --- Dump network log ---------------------------------------------
    with (OUT_DIR / "network.jsonl").open("w", encoding="utf-8") as f:
        for entry in network_log:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # --- Analyze: did we see JSON XHR endpoints? ----------------------
    json_responses = [
        e
        for e in network_log
        if e.get("kind") == "response"
        and "json" in e.get("headers", {}).get("content-type", "").lower()
        and "amac" in e.get("url", "")
    ]
    summary["json_xhr_count"] = len(json_responses)
    summary["json_xhr_urls"] = sorted({e["url"] for e in json_responses})[:20]
    summary["path_recommendation"] = (
        "A (httpx + JSON)" if json_responses else "B (Playwright + DOM)"
    )
    summary["finished_at"] = _now_iso()

    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n[recon] === SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(recon())
