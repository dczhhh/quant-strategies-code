# Read-only IBKR activity statement arithmetic

`tools/ibkr_activity_audit.py` accepts a user-supplied, Chinese sectioned XLSX activity export with
explicit opening/ending cash, opening quantities, order-level trades, closing positions, dividends,
withholding and accrual changes. It reads literal XML numbers as `Decimal`, using the bounded XLSX
reader; formulas, unknown monetary fields, missing required headers and ambiguous credits fail closed.
The original workbook is never modified. No connector, broker credentials or market-data API is used.

```bash
python -I tools/ibkr_activity_audit.py /private/statement.xlsx \
  --private-report /private/new-audit.json
```

Private report output is supported on POSIX hosts (including the existing Ubuntu runners), where it
is created with mode `0600`. Windows does not enforce POSIX mode bits: the CLI rejects output there
with `PRIVATE_REPORT_PERMISSION_PLATFORM_UNSUPPORTED` before reading source data or creating a file.
Cross-platform arithmetic fixtures still run; no permission assertion is silently skipped to admit
a Windows private export. A verified Windows ACL implementation would be needed to add that support.

The report is created exclusively so an earlier report cannot be overwritten. It contains private
account amounts, source rows and the workbook fingerprint: keep it and the original source out of
public repositories, CI, issue comments and artifacts. Standard output contains only allowlisted
aggregate statuses; unexpected exceptions print a generic reason, never their account-bearing text.
Public regression fixtures contain invented data only.
Exit code `0` means the selected arithmetic checks reconcile, `2` preserves an arithmetic
discrepancy report, and `1` means parsing or source requirements blocked the audit. Even exit code
`0` never grants PIT, execution, tax or strategy performance admission.

Cash reconstruction starts from the reported opening cash in every currency and adds actual detail
movements once. It excludes both `Total` rows and totals labelled `Data`; stock proceeds and fees,
both FX currency legs, USD-denominated FX commissions, deposits, withdrawals, trade fees, other fees,
dividends and withholding have separate source mappings. Receivable postings/removals never post cash.
The stock `commission/tax` column contains combined charges: each separate transaction tax is matched
to one order by asset, currency, timestamp, symbol, quantity and price, then subtracted from that
combined column before posting commission. The matched tax detail is posted once. Ambiguous or
unmatched tax rows block the audit; the fee split never uses closing cash to fit a difference.
Independent detail reconstruction is reported separately from summing the broker's cash subtotals.
Every raw discrepancy remains visible with the fixed `1E-8` tolerance in quote-currency units.
Quantity comparisons require exact equality. Small source NAV bridge differences remain failures;
the audit does not silently increase tolerance to a cent.

Position quantities start from the broker's earlier quantity and add observed orders. NVDA ordinary
US dividend cases reconstruct eligibility strictly before the ex-date boundary in `America/New_York`,
then match actual cash credit, withholding and receivable removal. Gross quantity-times-rate and
explicit USD cent rounding are separate comparisons: cent rounding does not relax cash tolerance.
Missing withholding is accepted as a source zero only when the explicit accrual tax is zero and the
other lifecycle records exist. ADR cash credits remain part of the observed account cash; this tool
does not admit ADR or Canadian new purchases or certify their tax treatment.

NAV checks reconcile the reported bridge and ending cash plus reported position values using the
statement's closing FX rates. They do not independently validate closing prices, every intraday fill,
daily settlement transitions or strategy performance. The source is a retrospective user export,
not authenticated API evidence or historical PIT. Corporate-action coverage, source completeness,
tax qualification, native Engine execution and performance admission remain unverified. Historical
orders outside regular trading hours are observations; strategy RTH constraints are not changed.
