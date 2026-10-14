"""Read-only, retrospective statement arithmetic. Never certifies PIT or execution."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import runpy
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

p = SimpleNamespace(**runpy.run_path(str(Path(__file__).with_name("real_etf_probe.py"))))
NY = ZoneInfo("America/New_York")
TOLERANCE = Decimal("1E-8")  # Quote-currency units; never loosened to hide discrepancies.
HEADERS = {
    "现金报告": [("货币总结", "货币", "总数", "证券", "期货")],
    "按市值计算的表现总结": [
        (
            "资产分类",
            "代码",
            "先前 数量",
            "当前 数量",
            "先前 价格",
            "当前 价格",
            "按市值计盈亏 持仓",
            "按市值计盈亏 交易",
            "按市值计盈亏 佣金",
            "按市值计盈亏 其它",
            "按市值计盈亏 总数",
            "代码",
        )
    ],
    "未平仓持仓": [
        (
            "DataDiscriminator",
            "资产分类",
            "货币",
            "代码",
            "数量",
            "合约乘数",
            "成本价格",
            "成本基础",
            "收盘价格",
            "价值",
            "未实现的损益",
            "代码",
        )
    ],
    "交易": [
        (
            "DataDiscriminator",
            "资产分类",
            "货币",
            "代码",
            "日期/时间",
            "数量",
            "交易价格",
            "收盘价格",
            "收益",
            "佣金/税",
            "基础",
            "已实现的损益",
            "按市值计算的损益",
            "代码",
        ),
        (
            "DataDiscriminator",
            "资产分类",
            "货币",
            "代码",
            "日期/时间",
            "数量",
            "交易价格",
            "",
            "收益",
            "佣金 USD",
            "",
            "",
            "以市值计（MTM） USD",
            "代码",
        ),
    ],
    "交易费用": [
        ("资产分类", "货币", "日期/时间", "代码", "描述", "数量", "交易价格", "总额", "代码")
    ],
    "存款和取款": [("货币", "结算日期", "描述", "金额")],
    "费用": [("Subtitle", "货币", "日期", "描述", "金额")],
    "股息": [("货币", "日期", "描述", "金额")],
    "代扣税": [("货币", "日期", "描述", "金额", "代码")],
    "应计股息的变化": [
        (
            "资产分类",
            "货币",
            "代码",
            "日期",
            "除息日",
            "支付日期",
            "数量",
            "税",
            "费用",
            "总股息率",
            "总额",
            "净额",
            "代码",
        )
    ],
    "净资产值变更": [("域名称", "域值")],
}


def number(value):
    return p.number(value, nonnegative=False)


def day(value):
    p.need(type(value) is str, "STATEMENT_DATE_INVALID")
    p.need(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}(?: 00:00:00|, \d{2}:\d{2}:\d{2})?", value),
        "STATEMENT_DATE_INVALID",
    )
    return date.fromisoformat(value[:10])


def currency(value):
    p.need(type(value) is str and re.fullmatch(r"[A-Z]{3}", value), "CURRENCY_INVALID")
    return value


def comparison(actual, expected, tolerance=TOLERANCE):
    difference = actual - expected
    return {
        "computed": str(actual),
        "reported": str(expected),
        "difference": str(difference),
        "exact_match": difference == 0,
        "within_tolerance": abs(difference) <= tolerance,
        "tolerance_units": str(tolerance),
    }


def records(body):
    sheets = p.workbook(body)
    p.need(len(sheets) == 1, "STATEMENT_SHEET_AMBIGUOUS")
    rows = next(iter(sheets.values()))
    result = defaultdict(list)
    seen = set()
    active_headers = {}
    for ordinal, row in enumerate(rows, 1):
        p.need(all(type(v) is str for v in row.values()), "STATEMENT_NON_LITERAL_CELL")
        section, kind = row.get("A", ""), row.get("B", "")
        values = [row.get(chr(67 + i), "") for i in range(15)]
        if kind == "Header" and section in HEADERS:
            p.need(
                any(tuple(values[: len(h)]) == h for h in HEADERS[section]),
                "STATEMENT_HEADER_INVALID",
            )
            seen.add(section)
            active_headers[section] = tuple(values[: len(HEADERS[section][0])])
        elif kind == "Data" and section in HEADERS:
            p.need(section in seen, "STATEMENT_HEADER_MISSING")
            if section == "交易":
                index = 0 if values[1] == "股票" else 1
                p.need(
                    active_headers[section] == HEADERS[section][index], "TRADE_HEADER_KIND_MISMATCH"
                )
            result[section].append((ordinal, values))
    p.need(set(HEADERS) <= seen, "STATEMENT_SECTION_MISSING")
    return rows, result


def analyze(body):
    with localcontext() as ctx:
        ctx.prec = 50
        return _analyze(body)


def _analyze(body):
    rows, sections = records(body)
    base = [
        r.get("D")
        for r in rows
        if r.get("A") in {"Account Information", "账户信息"} and r.get("C") == "基础货币"
    ]
    p.need(len(base) == 1, "BASE_CURRENCY_MISSING")
    base_currency = currency(base[0])
    p.need(base_currency == "USD", "BASE_CURRENCY_UNSUPPORTED")
    periods = [
        r.get("D", "")
        for r in rows
        if r.get("A") == "Statement" and r.get("C") in {"期间", "Period"}
    ]
    p.need(len(periods) == 1, "STATEMENT_PERIOD_MISSING")
    months = {
        v + "月": i
        for i, v in enumerate(
            ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二"), 1
        )
    }
    match = re.fullmatch(r"(\S+) (\d{1,2}), (\d{4}) - (\S+) (\d{1,2}), (\d{4})", periods[0])
    if match is None:
        raise p.Blocked("STATEMENT_PERIOD_INVALID")
    a, b, c, d, e, f = match.groups()
    p.need(a in months and d in months, "STATEMENT_PERIOD_INVALID")
    start, end = date(int(c), months[a], int(b)), date(int(f), months[d], int(e))
    p.need(start <= end, "STATEMENT_PERIOD_INVALID")
    p.need(
        any("交易执行时间以东部时间显示" in str(r.get("D", "")) for r in rows),
        "TRADE_TIMEZONE_UNCONFIRMED",
    )
    cash = defaultdict(dict)
    for ordinal, v in sections["现金报告"]:
        p.need(v[0] not in cash[v[1]], "CASH_REPORT_DUPLICATE")
        cash[v[1]][v[0]] = (number(v[2]), ordinal)
    currencies = {v for v in cash if v != "基础货币总结"}
    for cur in currencies:
        currency(cur)
        p.need(
            {"期初现金", "期末现金", "期末已结算现金"} <= set(cash[cur]), "CASH_BOUNDARY_MISSING"
        )
    details = defaultdict(lambda: defaultdict(Decimal))
    trades = []
    movement_counts = Counter()
    trade_taxes = {}
    consumed_trade_taxes = set()
    for _, v in sections["交易费用"]:
        if v[0].startswith("总数"):
            continue
        p.need(v[0] == "股票", "TRADE_FEE_KIND_UNSUPPORTED")
        key = (v[0], v[1], v[2], v[3], number(v[5]), number(v[6]))
        p.need(key not in trade_taxes, "TRADE_FEE_AMBIGUOUS")
        trade_taxes[key] = number(v[7])

    def move(cur, category, amount):
        p.need(cur in currencies, "CASH_CURRENCY_UNREPORTED")
        details[cur][category] += amount

    def proceeds(cur, amount):
        if amount:
            move(cur, "交易（卖出）" if amount > 0 else "交易（买入）", amount)

    for ordinal, v in sections["交易"]:
        p.need(v[0] == "Order" and v[1] in {"股票", "外汇"}, "TRADE_KIND_UNSUPPORTED")
        cur, symbol = currency(v[2]), v[3]
        p.need(re.fullmatch(r"\d{4}-\d{2}-\d{2}, \d{2}:\d{2}:\d{2}", v[4]), "TRADE_TIME_INVALID")
        timestamp = datetime.strptime(v[4], "%Y-%m-%d, %H:%M:%S").replace(tzinfo=NY)
        p.need(start <= timestamp.date() <= end, "TRADE_OUTSIDE_PERIOD")
        q, gross, fee = number(v[5]), number(v[8]), number(v[9])
        proceeds(cur, gross)
        if v[1] == "股票":
            key = (v[1], cur, v[4], symbol, q, number(v[6]))
            tax = trade_taxes.get(key, Decimal(0))
            if key in trade_taxes:
                p.need(key not in consumed_trade_taxes, "TRADE_FEE_AMBIGUOUS")
                p.need(fee <= tax <= 0, "TRADE_FEE_SIGN_CONFLICT")
                consumed_trade_taxes.add(key)
            # The stock header is commission/TAX combined; the detailed tax is posted below once.
            move(cur, "佣金", fee - tax)
            trades.append(
                {
                    "symbol": symbol,
                    "currency": cur,
                    "quantity": q,
                    "time": timestamp,
                    "row": ordinal,
                }
            )
        else:
            pair = symbol.split(".")
            p.need(len(pair) == 2 and pair[1] == cur and pair[0] != cur, "FX_PAIR_INVALID")
            proceeds(currency(pair[0]), q)
            move("USD", "佣金", fee)  # Source FX header explicitly denominates commissions in USD.
        movement_counts[v[1]] += 1
    p.need(set(trade_taxes) == consumed_trade_taxes, "TRADE_FEE_ORDER_MISSING")
    for section, cur_index, date_index, amount_index, category in (
        ("交易费用", 1, 2, 7, "交易费用"),
        ("存款和取款", 0, 1, 3, None),
        ("费用", 1, 2, 4, "其它费用"),
        ("股息", 0, 1, 3, "股息"),
        ("代扣税", 0, 1, 3, "代扣税款"),
    ):
        for _, v in sections[section]:
            if v[0].startswith("总数"):
                p.need(not v[date_index], "TOTAL_WITH_MOVEMENT_DATE")
                continue
            cur, posted, amount = (
                currency(v[cur_index]),
                day(v[date_index]),
                number(v[amount_index]),
            )
            p.need(start <= posted <= end, "MOVEMENT_OUTSIDE_PERIOD")
            move(cur, category or ("存款" if amount >= 0 else "取款"), amount)
            movement_counts[section] += 1
    cash_checks = []
    for cur in sorted(currencies):
        report = cash[cur]
        p.need(all(k in report for k, v in details[cur].items() if v), "CASH_CATEGORY_UNREPORTED")
        categories = {
            k: comparison(details[cur].get(k, Decimal(0)), value[0])
            for k, value in report.items()
            if k not in {"期初现金", "期末现金", "期末已结算现金"}
        }
        expected = report["期末现金"][0]
        independent = report["期初现金"][0] + sum(details[cur].values(), Decimal(0))
        cash_checks.append(
            {
                "currency": cur,
                "categories": categories,
                "independent_detail_rollforward": comparison(independent, expected),
                "reported_subtotal_rollforward": comparison(
                    report["期初现金"][0]
                    + sum(
                        (
                            v[0]
                            for k, v in report.items()
                            if k not in {"期初现金", "期末现金", "期末已结算现金"}
                        ),
                        Decimal(0),
                    ),
                    expected,
                ),
                "reported_settled_equals_total": comparison(report["期末已结算现金"][0], expected),
            }
        )
    opening, closing, rates = {}, {}, {}
    for _, v in sections["按市值计算的表现总结"]:
        if v[0] == "股票":
            p.need(v[1] not in opening, "POSITION_BOUNDARY_DUPLICATE")
            opening[v[1]], closing[v[1]] = number(v[2]), number(v[3])
        elif v[0] == "外汇":
            p.need(v[1] not in rates, "FX_RATE_DUPLICATE")
            rates[currency(v[1])] = p.number(v[5], positive=True)
    p.need(rates.get("USD") == 1, "USD_RATE_INVALID")
    ending_positions = {}
    ending_stock_value = Decimal(0)
    for _, v in sections["未平仓持仓"]:
        p.need(v[0] == "Summary" and v[1] == "股票", "POSITION_KIND_UNSUPPORTED")
        p.need(v[3] not in ending_positions and v[2] in rates, "POSITION_VALUE_AMBIGUOUS")
        ending_positions[v[3]] = number(v[4])
        ending_stock_value += number(v[9]) * rates[v[2]]
    p.need(all(t["symbol"] in opening for t in trades), "TRADE_OPENING_BOUNDARY_MISSING")
    position_checks = []
    for symbol, initial in sorted(opening.items()):
        reconstructed = initial + sum(
            (t["quantity"] for t in trades if t["symbol"] == symbol), Decimal(0)
        )
        position_checks.append(
            {
                "symbol": symbol,
                "opening": str(initial),
                "reported_change": comparison(reconstructed, closing[symbol], Decimal(0)),
                "reported_open_position": comparison(
                    closing[symbol], ending_positions.get(symbol, Decimal(0)), Decimal(0)
                ),
            }
        )
    p.need(set(ending_positions) <= set(opening), "ENDING_POSITION_BOUNDARY_MISSING")
    ending_cash_value = sum(
        (cash[cur]["期末现金"][0] * rates[cur] for cur in currencies), Decimal(0)
    )
    bridge = {}
    for _, v in sections["净资产值变更"]:
        p.need(v[0] not in bridge, "NAV_BRIDGE_DUPLICATE")
        bridge[v[0]] = number(v[1])
    p.need({"开始价值", "结束价值"} <= set(bridge), "NAV_BOUNDARY_MISSING")
    nav = {
        "reported_bridge": comparison(
            sum((v for k, v in bridge.items() if k != "结束价值"), Decimal(0)), bridge["结束价值"]
        ),
        "ending_cash_and_reported_position_values": comparison(
            ending_cash_value + ending_stock_value, bridge["结束价值"]
        ),
        "closing_FX_rates_are_retrospective": True,
        "trade_price_times_quantity_independently_valued": False,
    }
    dividend_checks = []
    for ordinal, v in sections["应计股息的变化"]:
        if v[0] != "股票" or v[12] != "Po" or v[2] != "NVDA":
            continue  # ADR corrections are retained in cash details, not ordinary-US admission.
        cur, symbol, ex, pay = currency(v[1]), v[2], day(v[4]), day(v[5])
        p.need(symbol in opening and start <= ex <= pay <= end, "DIVIDEND_BOUNDARY_UNSUPPORTED")
        boundary = datetime.combine(ex, time(), NY)
        eligible = opening[symbol] + sum(
            (t["quantity"] for t in trades if t["symbol"] == symbol and t["time"] < boundary),
            Decimal(0),
        )
        q, tax, fee, rate, gross, net = (number(v[i]) for i in (6, 7, 8, 9, 10, 11))
        p.need(q > 0 and rate > 0 and min(tax, fee, gross, net) >= 0, "DIVIDEND_SIGN_INVALID")
        credits = [
            number(w[3])
            for _, w in sections["股息"]
            if w[0] == cur and w[1] and day(w[1]) == pay and w[2].startswith(symbol + "(")
        ]
        withheld = [
            number(w[3])
            for _, w in sections["代扣税"]
            if w[0] == cur and w[1] and day(w[1]) == pay and w[2].startswith(symbol + "(")
        ]
        reversals = [
            w
            for _, w in sections["应计股息的变化"]
            if w[0] == "股票"
            and w[1] == cur
            and w[2] == symbol
            and day(w[4]) == ex
            and day(w[5]) == pay
            and w[12] == "Re"
        ]
        p.need(
            len(credits) == 1 and len(withheld) <= 1 and len(reversals) == 1,
            "DIVIDEND_LIFECYCLE_AMBIGUOUS",
        )
        p.need(bool(withheld) or tax == 0, "DIVIDEND_WITHHOLDING_MISSING")
        rev = reversals[0]
        p.need(day(rev[3]) == pay, "DIVIDEND_REMOVAL_DATE_INVALID")
        dividend_checks.append(
            {
                "parsed_row_ordinal": ordinal,
                "symbol": symbol,
                "ex_date": ex.isoformat(),
                "pay_date": pay.isoformat(),
                "eligible_quantity": comparison(eligible, q, Decimal(0)),
                "removal_quantity": comparison(number(rev[6]), q, Decimal(0)),
                "removal_rate": comparison(number(rev[9]), rate, Decimal(0)),
                "gross_unrounded": comparison(q * rate, gross),
                "gross_USD_cent_rounding": comparison(
                    (q * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), gross
                ),
                "gross_minus_withholding_and_fees": comparison(gross - tax - fee, net),
                "actual_cash_credit": comparison(credits[0], gross),
                "actual_withholding": comparison(-sum(withheld, Decimal(0)), tax),
                "receivable_cleared": {
                    k: comparison(number(rev[i]) + number(v[i]), Decimal(0))
                    for k, i in (("tax", 7), ("fee", 8), ("gross", 10), ("net", 11))
                },
                "missing_withholding_row_has_explicit_source_zero": not withheld and tax == 0,
                "tax_qualification_verified": False,
            }
        )
    return {
        "schema": "ibkr_activity_arithmetic_v1",
        "source_kind": "user_supplied_export",
        "observed_at": datetime.now(UTC).isoformat(),
        "source_sha256_private": hashlib.sha256(body).hexdigest(),
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "base_currency": base_currency,
        "trade_timezone": "America/New_York",
        "cash": cash_checks,
        "positions": position_checks,
        "NAV": nav,
        "ordinary_US_dividend_lifecycles": dividend_checks,
        "movement_counts": dict(movement_counts),
        "historical_PIT_verified": False,
        "source_authentication_verified": False,
        "native_engine_verified": False,
        "intraday_execution_verified": False,
        "settlement_lifecycle_verified": False,
        "corporate_action_coverage_verified": False,
        "performance_admission": False,
        "certification": "UNVERIFIED",
    }


def public_summary(report):
    """Allowlisted aggregate statuses: no account IDs, amounts, timestamps or hashes."""
    cash_ok = all(
        c["independent_detail_rollforward"]["within_tolerance"]
        and all(v["within_tolerance"] for v in c["categories"].values())
        for c in report["cash"]
    )
    positions_ok = all(
        v["reported_change"]["within_tolerance"] and v["reported_open_position"]["within_tolerance"]
        for v in report["positions"]
    )
    dividends_ok = bool(report["ordinary_US_dividend_lifecycles"]) and all(
        all(
            d[k]["within_tolerance"]
            for k in (
                "eligible_quantity",
                "removal_quantity",
                "removal_rate",
                "gross_USD_cent_rounding",
                "gross_minus_withholding_and_fees",
                "actual_cash_credit",
                "actual_withholding",
            )
        )
        and all(v["within_tolerance"] for v in d["receivable_cleared"].values())
        for d in report["ordinary_US_dividend_lifecycles"]
    )
    return {
        "schema": report["schema"],
        "source_kind": report["source_kind"],
        "cash_detail_reconciles": cash_ok,
        "position_quantity_reconciles": positions_ok,
        "ordinary_US_dividend_lifecycles_reconcile": dividends_ok,
        "reported_NAV_bridge_reconciles": report["NAV"]["reported_bridge"]["within_tolerance"],
        "ending_reported_value_NAV_reconciles": report["NAV"][
            "ending_cash_and_reported_position_values"
        ]["within_tolerance"],
        **dict.fromkeys(
            (
                "historical_PIT_verified",
                "source_authentication_verified",
                "native_engine_verified",
                "intraday_execution_verified",
                "settlement_lifecycle_verified",
                "corporate_action_coverage_verified",
                "performance_admission",
            ),
            False,
        ),
        "certification": "UNVERIFIED",
    }


def private_report_supported():
    return os.name == "posix"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--private-report", type=Path, required=True)
    args = parser.parse_args()
    try:
        # Windows ignores POSIX mode bits. Do not write account data with an unverified ACL.
        p.need(private_report_supported(), "PRIVATE_REPORT_PERMISSION_PLATFORM_UNSUPPORTED")
        p.need(args.input.resolve() != args.private_report.resolve(), "SOURCE_OVERWRITE_FORBIDDEN")
        p.need(args.input.stat().st_size <= p.DOC_LIMIT, "DOCUMENT_TOO_LARGE")
        report = analyze(args.input.read_bytes())
        summary = public_summary(report)
        # Exclusive creation protects earlier evidence; the source is never saved or modified.
        descriptor = os.open(args.private_report, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as out:
            json.dump(report, out, ensure_ascii=False, indent=2)
            out.write("\n")
        print(json.dumps(summary, sort_keys=True))
    except Exception as error:
        code = str(error) if isinstance(error, p.Blocked) else "STATEMENT_AUDIT_FAILED"
        print(json.dumps({"status": "BLOCKED", "reason_code": code, "certification": "UNVERIFIED"}))
        return 1
    return (
        0
        if all(
            summary[k]
            for k in (
                "cash_detail_reconciles",
                "position_quantity_reconciles",
                "ordinary_US_dividend_lifecycles_reconcile",
                "reported_NAV_bridge_reconciles",
                "ending_reported_value_NAV_reconciles",
            )
        )
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
