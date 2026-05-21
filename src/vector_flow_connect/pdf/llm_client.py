"""Anthropic SDK wrapper: render PDF, call Sonnet with tool-use, return validated payload."""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
from PIL import Image

from .canonical import file_sha256
from .prompts import SYSTEM_PROMPT
from .render import render_pages
from .schema import RECORD_MONTHLY_REPORT_SCHEMA, TOOL_DESCRIPTION, TOOL_NAME

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 8000


@dataclass
class LLMResult:
    payload: dict[str, Any]
    raw_response: dict[str, Any]
    usage: dict[str, Any]


def _jpeg_b64(img: Image.Image, quality: int = 85) -> str:
    """JPEG-encode at the given quality. ~4x smaller than PNG; identical
    numeric extraction accuracy at quality 85 for 200-DPI report scans.
    """
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def extract_pdf(
    pdf_path: str | Path,
    *,
    client: anthropic.Anthropic | None = None,
    dpi: int = 200,
    pages: list[int] | None = None,
) -> LLMResult:
    """Render the PDF, send pages + tool schema to Sonnet, return the validated tool call.

    `pages` is 1-based; None = render all pages. For multi-fund PDFs the runner
    passes a single page (or contiguous range) to extract that fund only.
    """
    client = client or anthropic.Anthropic()
    images = render_pages(pdf_path, pages=pages, dpi=dpi)

    content: list[dict[str, Any]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": _jpeg_b64(img),
            },
        }
        for img in images
    ]
    content.append(
        {
            "type": "text",
            "text": (
                f"Extract the monthly-report data from this {len(images)}-page PDF. "
                f"Call the {TOOL_NAME} tool exactly once."
            ),
        }
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[
            {
                "name": TOOL_NAME,
                "description": TOOL_DESCRIPTION,
                "input_schema": RECORD_MONTHLY_REPORT_SCHEMA,
            }
        ],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": content}],
    )

    payload: dict[str, Any] | None = None
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            payload = dict(block.input)
            break
    if payload is None:
        raise RuntimeError(f"Sonnet did not call {TOOL_NAME}; stop_reason={response.stop_reason}")

    return LLMResult(
        payload=payload,
        raw_response=response.model_dump(),
        usage=response.usage.model_dump(),
    )


def _page_spec(pages: list[int] | None) -> str | None:
    """Render a stable filename-safe page suffix for the cache key.

    Returns None for full-PDF extractions — caller keeps the legacy filename
    `{hash}.{version}.json` for backward compat with the existing cache.
    """
    if pages is None:
        return None
    return "p" + "-".join(str(p) for p in sorted(pages))


def extract_pdf_cached(
    pdf_path: str | Path,
    *,
    cache_dir: str | Path | None,
    extractor_version: str,
    client: anthropic.Anthropic | None = None,
    bypass_cache: bool = False,
    dpi: int = 200,
    pages: list[int] | None = None,
) -> tuple[LLMResult, dict[str, Any]]:
    """Same as `extract_pdf` plus disk cache keyed by `(artifact_hash, page_spec,
    extractor_version)`.

    For backward-compat, when `pages is None` the cache filename omits the
    page spec (matches existing cached responses). Multi-fund extractions
    use a per-page key like `{hash}.p2.{version}.json`.
    """
    artifact_hash = file_sha256(pdf_path)
    cache_path: Path | None = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        page_spec = _page_spec(pages)
        if page_spec is None:
            cache_path = cache_dir / f"{artifact_hash}.{extractor_version}.json"
        else:
            cache_path = cache_dir / f"{artifact_hash}.{page_spec}.{extractor_version}.json"
        if cache_path.exists() and not bypass_cache:
            data = json.loads(cache_path.read_text())
            return (
                LLMResult(
                    payload=data["payload"],
                    raw_response=data["raw_response"],
                    usage=data["usage"],
                ),
                {
                    "hit": True,
                    "cache_path": str(cache_path),
                    "artifact_hash": artifact_hash,
                    "pages": pages,
                },
            )

    result = extract_pdf(pdf_path, client=client, dpi=dpi, pages=pages)

    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "payload": result.payload,
                    "raw_response": result.raw_response,
                    "usage": result.usage,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )

    return result, {
        "hit": False,
        "cache_path": str(cache_path) if cache_path else None,
        "artifact_hash": artifact_hash,
        "pages": pages,
    }
