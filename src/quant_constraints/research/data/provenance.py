"""5E-1 deterministic, redacted schema/binding reports; never engine credentials."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from enum import StrEnum

from .pit import _canonical
from .verification_contracts import CONTRACT_VERSION, FrozenDatasetInput, _deny, _reconstruct

PROVENANCE_SCHEMA = "data_provenance_v1"


class ResearchMode(StrEnum):
    VERIFIED_RESEARCH = "verified_research"
    SYNTHETIC_TEST = "synthetic_test"
    LEGACY = "legacy_unverified"


class VerificationStatus(StrEnum):
    SYNTHETIC_CONTRACT_PASS = "synthetic_contract_pass"
    REAL_EVIDENCE_PENDING = "real_evidence_pending"
    TRUSTED_VERIFIED = "trusted_verified"  # Reserved; no current code path issues it.
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class AuthorityDecision:
    mode: ResearchMode
    status: VerificationStatus
    code: str

    @property
    def may_validate_synthetic(self) -> bool:
        return (
            self.mode is ResearchMode.SYNTHETIC_TEST
            and self.status is VerificationStatus.SYNTHETIC_CONTRACT_PASS
        )

    @property
    def may_build_engine(self) -> bool:
        return False  # 5E-1 does not implement even the isolated synthetic engine harness.


def contract_decision(
    snapshot: FrozenDatasetInput, mode: ResearchMode, *, reviewed_root_sha256: str
) -> AuthorityDecision:
    snapshot = _reconstruct(snapshot, FrozenDatasetInput, "snapshot")
    snapshot.assert_bound(reviewed_root_sha256)
    if type(mode) is not ResearchMode:
        _deny("INVALID_MODE", "mode", "authority")
    if mode is ResearchMode.LEGACY:
        return AuthorityDecision(mode, VerificationStatus.REJECT, "LEGACY_UNVERIFIED")
    if snapshot.request.input_kind == "real":
        return AuthorityDecision(
            mode, VerificationStatus.REAL_EVIDENCE_PENDING, "REAL_EVIDENCE_DEFERRED"
        )
    if mode is ResearchMode.VERIFIED_RESEARCH:
        return AuthorityDecision(mode, VerificationStatus.REJECT, "SYNTHETIC_NOT_TRUSTED")
    return AuthorityDecision(
        mode, VerificationStatus.SYNTHETIC_CONTRACT_PASS, "SCHEMA_BOUND_SYNTHETIC"
    )


def data_provenance(
    snapshot: FrozenDatasetInput, mode: ResearchMode, *, reviewed_root_sha256: str
) -> dict:
    """Bind the exact declarations; source content/references and tax identities stay private.

    No RawOHLCVValidator/PIT provider/authority review has run in this stage. This
    report cannot be submitted in place of its inputs or an externally frozen root.
    """
    snapshot = _reconstruct(snapshot, FrozenDatasetInput, "snapshot")
    decision = contract_decision(snapshot, mode, reviewed_root_sha256=reviewed_root_sha256)
    request = snapshot.request
    sources = tuple(
        {
            "role": value.role.value,
            "source_id_sha256": hashlib.sha256(value.source_id.encode()).hexdigest(),
            "source_ref_sha256": hashlib.sha256(value.source_ref.encode()).hexdigest(),
            "provider_sha256": hashlib.sha256(value.provider.encode()).hexdigest(),
            "dataset_id_sha256": hashlib.sha256(value.dataset_id.encode()).hexdigest(),
            "dataset_revision_sha256": hashlib.sha256(value.dataset_revision.encode()).hexdigest(),
            "partition_id_sha256": hashlib.sha256(value.partition_id.encode()).hexdigest(),
            "event_ids_sha256": hashlib.sha256(_canonical(value.event_ids)).hexdigest(),
            "depends_on_sha256": tuple(
                hashlib.sha256(v.encode()).hexdigest() for v in value.depends_on
            ),
            "sha256": value.sha256,
            "size_bytes": len(value.content),
            "version": value.version,
            "window_start": value.window_start.isoformat(),
            "window_end": value.window_end.isoformat(),
            "clocks": {
                k: v.isoformat() if v is not None else None for k, v in asdict(value.clocks).items()
            },
        }
        for value in snapshot.sources
    )
    result = {
        "schema_version": PROVENANCE_SCHEMA,
        "stage": "5E-1_schema_and_binding_only",
        "mode": mode.value,
        "status": decision.status.value,
        "code": decision.code,
        "phase": "authority",
        "input_kind": request.input_kind,
        "input_root_sha256": snapshot.expected_root_sha256,
        "source_roles": sources,
        "declared_versions": asdict(snapshot.versions),
        "reporter_version": CONTRACT_VERSION,
        "security_id": request.security_id,
        "symbol": request.symbol,
        "listing_venue": request.venue,
        "currency": request.currency,
        "frequency": request.frequency,
        "window_start": request.window_start.isoformat(),
        "window_end": request.window_end.isoformat(),
        "asof": request.asof.isoformat(),
        "timezone": request.timezone,
        "session_filter": request.session_filter,
        "raw_product_id": request.raw_product_id,
        "signal_product_id": request.signal_product_id,
        "signal_basis": request.signal_basis.value,
        "signal_share_unit": request.signal_share_unit.value,
        "signal_volume_unit": request.signal_volume_unit.value,
        "adjustment_version": request.adjustment_version,
        "volume_policy": request.volume_policy.value,
        "execution_basis": request.execution_basis.value,
        "valuation_basis": request.valuation_basis.value,
        "risk_basis": request.risk_basis.value,
        "execution_share_unit": request.execution_share_unit.value,
        "execution_volume_unit": request.execution_volume_unit.value,
        "strategy_config_sha256": request.strategy_config_sha256,
        "feature_policy": {
            "macro": "enabled" if request.macro_enabled else "disabled",
            "dividend_tax": "enabled" if request.dividend_tax_enabled else "disabled",
            "archive": "declared_present" if request.archive_present else "absent",
        },
        "domain_validation_performed": False,
        "source_authority": "unverified",
        "license_verified": False,
        "strategy_config_approved": False,
        "coverage_status": "not_evaluated",
        "coverage_verified": False,
        "may_build_engine": False,
        "raw_verified": False,
        "PIT_verified": False,
        "archive_ready": False,
        "trusted_entry": False,
        "archive_status": "deferred_by_user / awaiting_paid_plan_and_license_review",
    }
    result["audit_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result
