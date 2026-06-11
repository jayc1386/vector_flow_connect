"""Fund identity resolver — cluster fragmented source strings to canonical IDs.

DKU's master workbook uses the same fund under multiple spellings as the
naming convention evolved. Examples observed:

    "华夏纯债" / "华夏纯债债券A" / "华夏纯债债券A (000015)"      → fund code 000015
    "华夏鼎茂" / "华夏鼎茂债券A" / "华夏鼎茂债券A (004042)"      → fund code 004042
    "易方达裕祥" / "易方达裕祥回报债券" / "易方达裕祥回报债券 (002351)" → fund code 002351
    "海富通安颐 收益混合A (519050)" / "海富通安颐 收益混合基金"   → same fund, no code on alias

A naive `fund_id = hash(string)` stub fragments these into separate
fund_ids and consequently into separate lot_ids. This module collapses
them.

Algorithm:

1.  **Code-based clustering (definitive).** Strings carrying a parenthetical
    6-digit code `(NNNNNN)` cluster by that code. The canonical fund_id
    for the cluster is `fnd_<code>`. Each cluster has a *canonical name*
    = the longest string that includes the code (typically the most
    specific spelling).

2.  **Common-prefix fallback (heuristic).** Code-less strings are
    matched against the canonical names from step 1 by longest common
    Chinese-character prefix. A match attaches the alias to the
    canonical fund. The minimum prefix length is configurable; default
    is **4 characters**, low enough to catch "海富通安颐 收益混合基金" ↔
    "海富通安颐 收益混合A (519050)" (shared prefix "海富通安颐 收益混合"
    is 10 chars) but high enough to avoid false matches on common
    manager prefixes like "兴全" / "华夏" alone.

3.  **Codeless leftovers.** Strings that match no code-bearing cluster
    are kept as their own fund_id. ID is `fnd_<8-char-hash>` of the
    normalized string.

Ambiguous resolutions (a codeless string that matches multiple canonical
names with equal-length prefixes) are surfaced as `flagged` in the
resolver's debug log.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

CODE_RE = re.compile(r"\((\d{4,6})\)")


def _normalize(s: str) -> str:
    return (s or "").replace("\n", " ").strip()


def extract_fund_code(s: str) -> str | None:
    m = CODE_RE.search(s or "")
    return m.group(1) if m else None


def _strip_code(s: str) -> str:
    return CODE_RE.sub("", s or "").strip()


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


@dataclass
class FundCluster:
    fund_id: str
    canonical_name: str
    fund_code: str | None
    aliases: set[str] = field(default_factory=set)


@dataclass
class ResolverResult:
    """Returned by `build_resolver`. Carries the mapping plus a debug log."""

    mapping: dict[str, str]  # source_fund_string → fund_id
    clusters: dict[str, FundCluster]  # fund_id → cluster
    ambiguous: list[tuple[str, list[str]]]  # (source, list of equally-good fund_ids)

    def resolve(self, source_fund_string: str) -> str:
        return self.mapping.get(
            _normalize(source_fund_string), _fallback_id(_normalize(source_fund_string))
        )


def _fallback_id(normalized: str) -> str:
    return "fnd_" + _short_hash(normalized)


def build_resolver(
    source_fund_strings: Iterable[str],
    *,
    min_prefix_len: int = 4,
    fuzzy_min_prefix_len: int = 8,
    fuzzy_min_overlap: float = 0.85,
) -> ResolverResult:
    """Build a fund_id resolver from the observed universe of fund strings.

    Pure-function: pass in every source_fund_string you've seen, get
    back a deterministic mapping.
    """
    strings = {_normalize(s) for s in source_fund_strings if _normalize(s)}

    clusters: dict[str, FundCluster] = {}  # keyed by fund_id
    mapping: dict[str, str] = {}
    ambiguous: list[tuple[str, list[str]]] = []

    # Pass 1: cluster by explicit fund code.
    coded: dict[str, list[str]] = {}  # code → list of strings
    codeless: list[str] = []
    for s in strings:
        code = extract_fund_code(s)
        if code:
            coded.setdefault(code, []).append(s)
        else:
            codeless.append(s)

    for code, members in coded.items():
        fund_id = f"fnd_{code}"
        canonical = max(members, key=len)
        cluster = FundCluster(
            fund_id=fund_id,
            canonical_name=canonical,
            fund_code=code,
            aliases=set(members),
        )
        clusters[fund_id] = cluster
        for s in members:
            mapping[s] = fund_id

    # Pass 2: greedy common-prefix matching for codeless strings.
    canonical_bare = {fid: _strip_code(c.canonical_name) for fid, c in clusters.items()}
    for s in codeless:
        s_bare = _strip_code(s)
        # rank candidates by common-prefix length, applying two tiers:
        # - strict: shorter string is a full prefix of the longer
        #   (cp >= min_prefix_len AND cp == shorter). Cheap, safe.
        # - fuzzy: long shared prefix with high overlap ratio
        #   (cp >= fuzzy_min_prefix_len AND cp/shorter >= fuzzy_min_overlap).
        #   Catches "海富通安颐 收益混合A" vs "海富通安颐 收益混合基金"
        #   (cp=10/shorter=11 ratio=0.91) but NOT 睿远...1号 vs 睿远...5号
        #   (cp=6 < fuzzy_min_prefix_len=8).
        ranked: list[tuple[int, str]] = []
        for fid, canon_bare in canonical_bare.items():
            cp = _common_prefix_len(s_bare, canon_bare)
            shorter = min(len(s_bare), len(canon_bare))
            if shorter == 0:
                continue
            strict_match = cp >= min_prefix_len and cp == shorter
            fuzzy_match = cp >= fuzzy_min_prefix_len and (cp / shorter) >= fuzzy_min_overlap
            if strict_match or fuzzy_match:
                ranked.append((cp, fid))
        if not ranked:
            # codeless leftover — own fund_id
            fid = _fallback_id(s_bare)
            clusters[fid] = FundCluster(fund_id=fid, canonical_name=s, fund_code=None, aliases={s})
            mapping[s] = fid
            continue
        ranked.sort(reverse=True)
        best_cp, best_fid = ranked[0]
        # check for ties
        ties = [fid for cp, fid in ranked if cp == best_cp]
        if len(ties) > 1:
            ambiguous.append((s, ties))
            # still attach to the first one deterministically (sorted)
            best_fid = sorted(ties)[0]
        clusters[best_fid].aliases.add(s)
        mapping[s] = best_fid

    return ResolverResult(mapping=mapping, clusters=clusters, ambiguous=ambiguous)


def resolver_to_dataframe_rows(result: ResolverResult) -> list[dict]:
    """Flatten the resolver into rows suitable for a fund_aliases table."""
    rows = []
    for fid, cluster in result.clusters.items():
        for alias in sorted(cluster.aliases):
            rows.append(
                {
                    "fund_id": fid,
                    "fund_code": cluster.fund_code,
                    "canonical_name": cluster.canonical_name,
                    "alias": alias,
                    "is_canonical": alias == cluster.canonical_name,
                }
            )
    return rows
