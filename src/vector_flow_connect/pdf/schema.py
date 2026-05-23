"""JSON schema for the `record_monthly_report` Anthropic tool.

Single tool surface for every 私募 manager — clean funds emit `events: []`;
dirty funds populate the events array. All percentages are emitted as the
literal display number (38.67, not 0.3867). Currency is RMB unless the PDF
says otherwise — currency is not in the schema for v1.
"""

from __future__ import annotations

RECORD_MONTHLY_REPORT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "fund_name_zh",
        "report_period_end",
        "nav_per_unit",
        "extraction_notes",
    ],
    "properties": {
        "fund_name_zh": {"type": "string"},
        "fund_code": {"type": ["string", "null"]},
        "share_class": {
            "type": ["string", "null"],
            "description": "Share class designator if shown — e.g. 'A类', 'C类', 'A', 'C'. Null if the report doesn't distinguish.",
        },
        "report_period_end": {"type": "string", "format": "date"},
        "inception_date": {"type": ["string", "null"], "format": "date"},
        "nav_per_unit": {
            "type": "number",
            "description": "单位净值 — per-unit NAV, the post-distribution value. For funds without distributions this equals nav_cumulative.",
        },
        "nav_cumulative": {
            "type": ["number", "null"],
            "description": "累计单位净值 — cumulative NAV including the value of distributions ever paid. Null if the report does not break this out separately. For clean funds without distributions, equals nav_per_unit.",
        },
        "since_inception_return_pct": {"type": ["number", "null"]},
        "monthly_returns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["year", "month", "return_pct"],
                "properties": {
                    "year": {"type": "integer"},
                    "month": {"type": "integer", "minimum": 1, "maximum": 12},
                    "return_pct": {"type": "number"},
                },
            },
        },
        "top_holdings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rank", "weight_pct"],
                "properties": {
                    "rank": {"type": "integer", "minimum": 1},
                    "security_name_zh": {"type": ["string", "null"]},
                    "ticker": {"type": ["string", "null"]},
                    "weight_pct": {"type": "number"},
                    "weight_pct_prior": {"type": ["number", "null"]},
                },
            },
        },
        "sector_breakdown": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["sector_label_zh", "weight_pct"],
                "properties": {
                    "sector_label_zh": {"type": "string"},
                    "weight_pct": {"type": "number"},
                    "taxonomy_hint": {
                        "type": ["string", "null"],
                        "enum": [
                            "GICS",
                            "CSRC",
                            "CITIC",
                            "SHENWAN",
                            "MANAGER_INTERNAL",
                            None,
                        ],
                    },
                },
            },
        },
        "geographic_breakdown": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["region_label_zh", "weight_pct"],
                "properties": {
                    "region_label_zh": {"type": "string"},
                    "weight_pct": {"type": "number"},
                },
            },
        },
        "position_counts": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "long": {"type": ["integer", "null"]},
                "short": {"type": ["integer", "null"]},
                "net": {"type": ["integer", "null"]},
            },
        },
        "cash_allocation_band": {"type": ["string", "null"]},
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["event_type", "event_date", "confidence_self"],
                "properties": {
                    "event_type": {
                        "type": "string",
                        "enum": [
                            "distribution_cash",
                            "distribution_units",
                            "perf_fee",
                            "subscription_fee",
                            "other",
                        ],
                    },
                    "event_date": {"type": "string", "format": "date"},
                    "units_delta": {"type": ["number", "null"]},
                    "cash_delta": {"type": ["number", "null"]},
                    "per_unit_amount": {"type": ["number", "null"]},
                    "notes_raw": {"type": "string"},
                    "confidence_self": {
                        "type": "string",
                        "enum": ["clean", "fuzzy"],
                    },
                },
            },
        },
        "manager_commentary_summary": {
            "type": ["string", "null"],
            "maxLength": 800,
        },
        "extraction_notes": {"type": "string"},
    },
}

TOOL_NAME = "record_monthly_report"
TOOL_DESCRIPTION = (
    "Record the structured contents of one 私募 (private hedge fund) "
    "monthly report. Called exactly once per report."
)
