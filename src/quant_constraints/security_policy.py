"""Buy-only owner scope; metadata declarations do not issue real/PIT certificates."""

import re
from urllib.parse import urlsplit

from .models import ALLOW, Action, Decision, Intent, MarketContext

POLICY_VERSION = "exclude_canada_and_adrs_v1"
ADR_TYPES = frozenset({"ADR", "ADS", "ADRC", "ADRP", "ADRW", "ADRR", "ADRU"})


def security_entry_decision(intent: Intent, context: MarketContext, policy: str) -> Decision:
    """Recheck submission and every fill; never block a liquidation or cash credit."""
    if intent.quantity < 0:
        return ALLOW
    if context.issuer_country == "CA" or context.tax_source_country == "CA":
        return Decision(Action.REJECT, "canadian_security_excluded")
    if (
        isinstance(context.security_type, str) and context.security_type in ADR_TYPES
    ) or context.is_depositary_receipt is True:
        return Decision(Action.REJECT, "adr_security_excluded")
    if policy == "legacy":
        return ALLOW
    if policy != "exclude_canada_and_adrs":
        return Decision(Action.REJECT, "security_entry_policy_invalid")
    if (
        not isinstance(context.security_id, str)
        or not context.security_id.strip()
        or not isinstance(context.security_type, str)
        or context.security_type not in {"CS", "ETF"}
        or context.is_depositary_receipt is not False
        or any(
            not isinstance(country, str)
            or re.fullmatch(r"[A-Z]{2}", country) is None
            or country in {"XX", "ZZ"}
            for country in (context.issuer_country, context.tax_source_country)
        )
        or context.security_metadata_available_at is None
        or context.security_metadata_available_at > context.asof
        or not isinstance(context.security_metadata_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", context.security_metadata_sha256) is None
        or not _source_uri(context.security_metadata_source)
    ):
        return Decision(Action.REJECT, "security_metadata_unverified")
    if (
        context.security_metadata_symbol != intent.asset
        or context.security_metadata_security_id != context.security_id
    ):
        return Decision(Action.REJECT, "security_identity_conflict")
    if (context.instrument, context.security_type) not in {
        ("equity", "CS"),
        ("plain_sector_etf", "ETF"),
    }:
        return Decision(Action.REJECT, "security_type_conflict")
    return ALLOW


def _source_uri(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uri = urlsplit(value)
        return bool(
            uri.scheme == "https" and uri.hostname and not uri.username and not uri.password
        )
    except ValueError:
        return False
