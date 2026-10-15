# Append-only recovery of the interrupted ETF group

The first capture run, `38509087584`, archived 41 responses: 40 from the six
completed groups and XLB raw bars from the interrupted `etf-1` group. Its next
XLB split-adjusted request has an immutable reservation but no response capture.
That request's outcome is unknown, not a reusable request allowance.

`tools/real_action_recovery.py` permits one explicitly reviewed additional
attempt in a separate `real-actions-recovery-v1-*` namespace. It acquires only
the eight missing XLB/XLC/XLE products from the original fixed URL plan. It reuses
the original XLB raw capture and requires the original capture and both original
reservations to match pinned commits before and after acquisition. No old ref is
written, deleted, reset or renamed. The previous unknown outcome remains unknown.

Every new product gets a create-only reservation before one GET. The fixed
45-second timeout is not an automatic retry. An interrupted new attempt remains
reserved and cannot be silently attempted again. Only explicitly reviewed main
and owner workflow dispatch may use the existing protected archive environment;
the new enable and source-SHA variables do not grant new credential permissions.

Original response bytes stay exclusively on the runner and private data repo.
No public artifact, market-data dump or exception body is emitted. Readback is
performed with the provider credential absent, then repeated on a fresh runner
using a read-only GitHub client. Transport/structure acceptance stays
`UNVERIFIED`, with all raw/PIT/archive/engine/admission flags false.

This is not official distribution classification, historical PIT, GE 2021 data
entitlement, a cash-in-lieu calculation, actual broker credit evidence, strategy
performance certification or permission to merge strategy PR #6. All added
transport tests use invented responses and count as zero real economic cases.
