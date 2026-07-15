"""`resolve_as_of` — collision-aware snapshot date resolution.

Two DKU workbook sheets can resolve to the same as_of when a stale
title cell names another sheet's valuation date (tab `20210208` titled
截止02月18日 collides with the real `20210218` sheet). Left unresolved
the two are aggregated together downstream → doubled holdings. The
resolver keeps the tab-native sheet on the contended date and falls the
other back to its own tab date, flagging the collision.
"""

from __future__ import annotations

from datetime import date

from vector_flow_connect.dku.master_record.snapshot import resolve_as_of


def _kinds(issues: list[dict], kind: str) -> list[dict]:
    return [i for i in issues if i["kind"] == kind]


def test_collision_splits_to_tab_dates() -> None:
    # 20210208's title (Feb 18) collides with the real 20210218 sheet.
    resolved, issues = resolve_as_of(
        {
            "20210208": (date(2021, 2, 18), date(2021, 2, 8)),
            "20210218": (date(2021, 2, 18), date(2021, 2, 18)),
        }
    )
    # Native sheet keeps the contended date; the other falls back to its tab.
    assert resolved["20210218"] == date(2021, 2, 18)
    assert resolved["20210208"] == date(2021, 2, 8)
    collisions = _kinds(issues, "as_of_collision")
    assert len(collisions) == 1
    assert collisions[0]["sheet"] == "20210208,20210218"


def test_benign_title_relabel_is_kept() -> None:
    # Tab 20210129 titled 截止01月28日 — no other sheet claims Jan 28,
    # so the title date wins (it is the valuation date) and NO collision.
    resolved, issues = resolve_as_of(
        {"20210129": (date(2021, 1, 28), date(2021, 1, 29))},
    )
    assert resolved["20210129"] == date(2021, 1, 28)
    assert _kinds(issues, "title_date_mismatch")
    assert not _kinds(issues, "as_of_collision")


def test_no_title_falls_back_to_tab() -> None:
    resolved, issues = resolve_as_of({"20200121": (None, date(2020, 1, 21))})
    assert resolved["20200121"] == date(2020, 1, 21)
    assert not _kinds(issues, "title_date_mismatch")
    assert not _kinds(issues, "as_of_collision")


def test_unparseable_sheet_flags_no_as_of() -> None:
    resolved, issues = resolve_as_of({"summary": (None, None)})
    assert resolved["summary"] is None
    assert _kinds(issues, "no_as_of_date")


def test_agreeing_dates_are_silent() -> None:
    resolved, issues = resolve_as_of({"20210218": (date(2021, 2, 18), date(2021, 2, 18))})
    assert resolved["20210218"] == date(2021, 2, 18)
    assert issues == []
