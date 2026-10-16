# Isolated parent-order fractional-fee research

This appendix corrects the isolated 0.1-share preview, not the sealed production,
integer-share, PIT, or universal IBKR tariff. Those original sources and original
tests are unchanged. New tests must not replace or relax them.

The account owner authorized read-only IBKR execution-history inspection. Ordinary
USD stock executions support treating a whole fill and its fractional remainder as
components of one parent order. The remainder must not independently incur another
minimum or an automatic one-percent commission. Standalone fractional orders can
still pay a minimum. Fractional shares are **not commission free**. DRIP and FX
records are excluded; account identifiers, prices and execution rows are not stored
in this repository or report.

IBKR's sample-statement notes 5–6 also distinguish principal fractional fills from
original-order commission aggregation:
https://ibkrguides.com/reportingreference/sample_model_statement.html . The published
fractional-trade percentage fallback remains available as the explicitly separate
legacy policy, not as a fee added to each residual of a mixed empirical parent order.

`IBKRParentOrderFractionalResearch` is an explicit research subclass. It applies the
existing tiered parent-order minimum/cap across all shares and keeps regulatory and
clearing fees. Only the whole component receives the unknown-external-venue proxy;
the fractional component is assumed internal. This is an account-observed scenario,
not a guarantee of every venue, historical rate, account plan or actual bill. The
existing 2026 fee snapshot remains a labelled historical backcast assumption.

The old preview source `aabaf88f527a586113edd8e1d993e198eb3d8a3f` and bundle
`a14b82c309bba6d57fcd964c49ecca584d9bef4e644ceccf6b013e417013c5bc` remain immutable.
Before running corrected rotation and funded-SPY cases, the operator authenticates
that bundle and exactly reproduces the complete old integer and old percentage-fee
engine reports. The only permitted differences are two explicit policy labels;
no economic value, precision, tolerance or ordering is waived. Input bytes and
fingerprints must match. Fee changes can affect later order sizing, so results are
fully replayed, not calculated by subtracting fees after the event.

Corrected private engine results include each simulated parent order's fill count,
minimum/cap, broker/venue/clearing/regulatory/pass-through totals, and execution
attribution. Components must reconcile to all simulated fill commissions within
USD 1E-8. This audit is not mislabeled as actual-account bill reconciliation.

The corrected appendix uses a new private report ref ending
`tenths-order-fees-v1-engine-report` and a separate workflow
`.github/workflows/rotation-stress-tenths-fees.yml`. A fresh hosted runner with a
different hash seed must rebuild all report bytes identically. Existing reports are
never overwritten. No new provider requests or broker orders are authorized.

Research-only limitations remain: assumed 2024 distribution classification,
historical fee snapshot, unknown venue and fractional eligibility, unverified PIT,
and unresolved account-NAV reconciliation. Performance admission remains false and
the number of fully admitted real scenarios remains zero. Passing this appendix
cannot merge or certify the strategy source or unlock live trading.
