"""Fake archives and invented document bodies; never a live SEC request."""

import pytest

from tests.test_private_archive_migration import FakeGitHub
from tools import bear_2022_documents as d

SOURCE = "b" * 40
BODY = b"<p>SPDR S&amp;P 500 ETF Trust: tax character. Ordinary income: 123.</p>"


def test_capture_and_fresh_read_without_requests_or_writes():
    archive = FakeGitHub(d.c.m.DESTINATION)
    body, receipt, made = d.capture(archive, "spy-fy2023", SOURCE, get=lambda _: (200, BODY))
    assert body == BODY and made == 1
    before = dict(archive.refs)
    archive.events.clear()
    other, other_receipt, made = d.capture(
        archive,
        "spy-fy2023",
        SOURCE,
        verify_only=True,
        get=lambda _: pytest.fail("network forbidden"),
    )
    assert (body, receipt) == (other, other_receipt) and made == 0
    assert archive.refs == before and all(method == "GET" for method, _ in archive.events)
    assert all(len(value) <= d.c.m.FILE_LIMIT for value in archive.blobs.values())


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_failure_is_retained_and_never_retried(status):
    archive, calls = FakeGitHub(d.c.m.DESTINATION), []

    def denied(product):
        calls.append(product)
        return status, b"PRIVATE_ERROR_BODY"

    for _ in range(2):
        with pytest.raises(ValueError, match="DOCUMENT_HTTP_" + str(status) + "_RETAINED_NO_RETRY"):
            d.capture(archive, "spy-fy2023", SOURCE, get=denied)
    assert len(calls) == 1 and archive.ref(d.ref("spy-fy2023")) is not None


def test_unknown_outcome_is_retained_and_never_retried():
    archive, calls = FakeGitHub(d.c.m.DESTINATION), []

    def unknown(product):
        calls.append(product)
        raise TimeoutError()

    for _ in range(2):
        with pytest.raises(ValueError, match="DOCUMENT_UNKNOWN_OUTCOME_NO_RETRY"):
            d.capture(archive, "spy-fy2023", SOURCE, get=unknown)
    assert len(calls) == 1 and archive.ref(d.ref("spy-fy2023")) is not None


def test_large_document_uses_existing_blob_limit_not_larger_blobs():
    archive = FakeGitHub(d.c.m.DESTINATION)
    body = b"A" * (d.c.m.FILE_LIMIT + 1)
    saved, receipt, made = d.capture(archive, "spy-fy2023", SOURCE, get=lambda _: (200, body))
    assert saved == body and receipt["part_count"] == 2 and made == 1
    assert all(len(value) <= d.c.m.FILE_LIMIT for value in archive.blobs.values())
    assert len(archive.events) < d.c.m.CALL_LIMIT


def test_missing_read_only_and_nonowner_context_fail_closed(monkeypatch):
    archive = FakeGitHub(d.c.m.DESTINATION)
    with pytest.raises(ValueError, match="DOCUMENT_RAW_MISSING"):
        d.capture(archive, "spy-fy2023", SOURCE, verify_only=True)
    assert not archive.refs
    monkeypatch.setenv("GITHUB_ACTOR", "untrusted")
    with pytest.raises(ValueError, match="DOCUMENT_RUNNER_CONTEXT"):
        d.guarded(SOURCE)
