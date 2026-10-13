"""Owner's capture-universe policy bound to independently reviewable public metadata.

This is acquisition permission, not a daily historical capitalization or PIT certificate.
No runtime caller can register its own instrument evidence or mid-cap approval.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from .archive_contracts import canonical, digest
from .demand import deny
from .sample_contracts import frozen_batch

DENIED_SYMBOLS = frozenset({"GME", "AMC", "KOSS"})
DENIED_SECURITY_IDS = frozenset({"CIK:0001326380", "CIK:0001411579", "CIK:0000056701"})
LARGE_CAP_USD = 10_000_000_000
MID_CAP_USD = 2_000_000_000
BOUNDARY_BUFFER = 0.05
_REVIEWED_EVIDENCE = tuple(digest(canonical(v)) for v in frozen_batch()["securities"])
_ACCEPTED_MIDCAP_REVIEWS: tuple[dict, ...] = ()


def admit_instrument(
    symbol: str,
    day: date,
    plan_sha256: str,
    evidence: dict,
    as_of: datetime,
    justification: dict | None = None,
) -> dict:
    if (
        type(symbol) is not str
        or re.fullmatch(r"[A-Z]{1,10}", symbol) is None
        or type(day) is not date
        or type(as_of) is not datetime
        or as_of.utcoffset() is None
        or type(evidence) is not dict
        or type(plan_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", plan_sha256) is None
    ):
        deny("INSTRUMENT_POLICY_INPUT_INVALID")
    if (
        symbol in DENIED_SYMBOLS
        or any(evidence.get("symbol") == s for s in DENIED_SYMBOLS)
        or any(evidence.get("security_id") == s for s in DENIED_SECURITY_IDS)
        or evidence.get("wsb_status") == "confirmed_meme"
    ):
        deny("DENIED_MEME_OR_SMALLCAP_INSTRUMENT")
    bound = digest(canonical(evidence))
    if bound not in _REVIEWED_EVIDENCE:
        deny("UNREVIEWED_INSTRUMENT_EVIDENCE")
    if (
        evidence.get("symbol") != symbol
        or evidence.get("currency") != "USD"
        or evidence.get("source_kind") != "reviewed_public_fact_summary"
        or evidence.get("historical_universe_PIT_verified") is not False
        or evidence.get("wsb_status") != "reviewed_non_meme"
        or not date.fromisoformat(evidence["valid_from"])
        <= day
        <= date.fromisoformat(evidence["valid_to"])
        or date.fromisoformat(evidence["reviewed_on"]) > as_of.date()
    ):
        deny("INSTRUMENT_SCOPE_OR_IDENTITY_CONFLICT")
    if evidence.get("security_type") == "ETF":
        if evidence.get("cap_basis") != "issuer_etf" or not re.fullmatch(
            r"ISIN:[A-Z]{2}[A-Z0-9]{10}", evidence["security_id"]
        ):
            deny("ETF_IDENTITY_UNVERIFIED")
        category = "etf"
    elif evidence.get("security_type") == "CS":
        if not re.fullmatch(r"CIK:[0-9]{10}", evidence["security_id"]):
            deny("STOCK_IDENTITY_UNVERIFIED")
        value = evidence.get("cap_lower_bound_usd")
        basis = evidence.get("cap_basis")
        if (
            type(value) is not int
            or value <= 0
            or basis not in {"total_market_cap", "nonaffiliate_lower_bound"}
        ):
            deny("CAPITALIZATION_EVIDENCE_INVALID")
        cap_day = date.fromisoformat(evidence["cap_basis_date"])
        if not 0 <= (day - cap_day).days <= 365:
            deny("CAPITALIZATION_DATE_UNVERIFIED")
        if value >= LARGE_CAP_USD * (1 + BOUNDARY_BUFFER):
            category = "large_cap"
        elif basis == "nonaffiliate_lower_bound":
            deny("CAPITALIZATION_CLASS_UNCERTAIN")
        elif value < MID_CAP_USD * (1 - BOUNDARY_BUFFER):
            deny("DENIED_MEME_OR_SMALLCAP_INSTRUMENT")
        elif (
            not MID_CAP_USD * (1 + BOUNDARY_BUFFER)
            <= value
            <= LARGE_CAP_USD * (1 - BOUNDARY_BUFFER)
        ):
            deny("CAPITALIZATION_BOUNDARY_REVIEW_REQUIRED")
        else:
            _midcap_review(symbol, plan_sha256, bound, as_of, justification)
            category = "mid_cap_independently_reviewed"
    else:
        deny("SECURITY_TYPE_UNVERIFIED")
    return {
        "schema_version": "research_instrument_admission_v1",
        "symbol": symbol,
        "security_id": evidence["security_id"],
        "security_type": evidence["security_type"],
        "category": category,
        "evidence_sha256": bound,
        "source_ref": evidence["source_ref"],
        "cap_basis_date": evidence["cap_basis_date"],
        "plan_sha256": plan_sha256,
        "historical_universe_PIT_verified": False,
        "policy_allows_capture": True,
    }


def _midcap_review(
    symbol: str, plan: str, evidence: str, as_of: datetime, justification: dict | None
) -> None:
    required = {
        "hypothesis",
        "why_no_largecap_or_etf_substitute",
        "liquidity",
        "identity_and_cap_basis",
        "exact_request_scope",
        "budget_and_exit_cache_rules",
    }
    if (
        type(justification) is not dict
        or set(justification) != required
        or any(type(v) is not str or not 12 <= len(v) <= 1024 for v in justification.values())
    ):
        deny("MIDCAP_JUSTIFICATION_REQUIRED")
    rationale = digest(canonical(justification))
    for review in _ACCEPTED_MIDCAP_REVIEWS:
        if (
            review["symbol"] == symbol
            and review["plan_sha256"] == plan
            and review["evidence_sha256"] == evidence
            and review["justification_sha256"] == rationale
            and re.fullmatch(
                r"https://github.com/dczhhh/quant-strategies/pull/[0-9]+#pullrequestreview-[0-9]+",
                review["review_ref"],
            )
            and datetime.fromisoformat(review["reviewed_at"])
            <= as_of
            <= datetime.fromisoformat(review["expires_at"])
        ):
            return
    deny("MIDCAP_INDEPENDENT_REVIEW_REQUIRED")
