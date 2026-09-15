from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .client import MosOblEIRCClient, MosOblEIRCError

MONTH_RE = re.compile(r"(\d{4})-(\d{1,2})")
PERIOD_KEYS = (
    "dt_period",
    "period",
    "dt_charge",
    "chargePeriod",
    "dt",
    "date",
    "month",
    "periodDate",
)

VALUE_FIELDS = {
    "charged": "charged",
    "volume": "volume",
}


def month_add(month: str, delta: int) -> str:
    year, mon = (int(part) for part in month.split("-"))
    total = year * 12 + (mon - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def month_range(end_month: str, count: int) -> list[str]:
    return [month_add(end_month, offset) for offset in range(-count + 1, 1)]


def anchor_date(month: str, day: int) -> str:
    year, mon = (int(part) for part in month.split("-"))
    last = calendar.monthrange(year, mon)[1]
    return f"{year:04d}-{mon:02d}-{min(day, last):02d}"


def previous_month(today: date | None = None) -> str:
    today = today or date.today()
    return month_add(f"{today.year:04d}-{today.month:02d}", -1)


def to_number(value) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace("\u00a0", "").replace(" ", "").replace(",", ".")
    cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def to_optional_number(value) -> float | None:
    if value is None or value == "":
        return None
    return to_number(value)


def period_from_item(item: dict) -> str | None:
    for key in PERIOD_KEYS:
        value = item.get(key)
        if isinstance(value, str):
            match = MONTH_RE.search(value)
            if match:
                return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"
    return None


@dataclass
class MonthTotal:
    month: str
    account_id: str
    account_name: str
    accrued: float = 0.0
    paid: float = 0.0
    balance_start: float = 0.0
    balance_end: float = 0.0
    raw: dict = field(default_factory=dict)


def month_from_item(item: dict) -> str | None:
    period = period_from_item(item)
    if period:
        return period
    year = item.get("year")
    month = item.get("month")
    if year is None or month is None:
        return None
    try:
        return f"{int(year):04d}-{int(month):02d}"
    except (TypeError, ValueError):
        return None


@dataclass
class Charge:
    month: str
    requested_date: str
    account_id: str
    account_name: str
    service: str
    group: str = ""
    unit: str = ""
    volume: float | None = None
    tariff: float | None = None
    charged: float = 0.0
    benefits: float = 0.0
    recalculations: float = 0.0
    total: float = 0.0
    raw: dict = field(default_factory=dict)


def normalize_charges(
    details: list[dict],
    *,
    month: str,
    requested_date: str,
    account_id: str,
    account_name: str,
) -> list[Charge]:
    rows = []
    for item in details:
        if not isinstance(item, dict):
            continue
        rows.append(
            Charge(
                month=month_from_item(item) or month,
                requested_date=requested_date,
                account_id=account_id,
                account_name=account_name,
                service=str(item.get("nm_service") or "Без названия"),
                unit=str(item.get("nm_measure_unit") or ""),
                volume=to_optional_number(item.get("vl_charged_volume")),
                tariff=to_optional_number(item.get("vl_tariff")),
                charged=to_number(item.get("sm_charged")),
                benefits=to_number(item.get("sm_benefits")),
                recalculations=to_number(item.get("sm_recalculations")),
                total=to_number(item.get("sm_total")),
                raw=item,
            )
        )
    return rows


class ChargeCache:
    def __init__(self, directory: Path | str | None):
        self.directory = Path(directory) if directory else None

    def path(self, personal_account_id: str, requested_date: str) -> Path | None:
        if not self.directory:
            return None
        return self.directory / str(personal_account_id) / f"{requested_date}.json"

    def load(self, personal_account_id: str, requested_date: str) -> list[dict] | None:
        path = self.path(personal_account_id, requested_date)
        if not path or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        details = payload.get("chargeDetails") if isinstance(payload, dict) else payload
        return details if isinstance(details, list) else None

    def save(self, personal_account_id: str, requested_date: str, month: str, details: list[dict]) -> None:
        path = self.path(personal_account_id, requested_date)
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"requestedDate": requested_date, "month": month, "chargeDetails": details}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_charges(
    client: MosOblEIRCClient,
    accounts: list[dict],
    months: list[str],
    *,
    anchor_day: int = 15,
    shift: int = 1,
    cache_dir: Path | str | None = None,
    force: bool = False,
    log=None,
) -> list[Charge]:
    cache = ChargeCache(cache_dir)
    rows: list[Charge] = []

    for account in accounts:
        personal_account_id = account["personal_account_id"]
        for month in months:
            requested_date = anchor_date(month_add(month, shift), anchor_day)
            details = None if force else cache.load(personal_account_id, requested_date)
            source = "cache"

            if details is None:
                details = client.charge_details(personal_account_id, requested_date)
                cache.save(personal_account_id, requested_date, month, details)
                source = "api"

            if log:
                log(account, month, requested_date, len(details), source)

            rows.extend(
                normalize_charges(
                    details,
                    month=month,
                    requested_date=requested_date,
                    account_id=str(account.get("id") or personal_account_id),
                    account_name=str(account.get("name") or personal_account_id),
                )
            )

    return rows


MONTH_ROOTS = (
    ("январ", 1),
    ("феврал", 2),
    ("март", 3),
    ("апрел", 4),
    ("май", 5),
    ("июн", 6),
    ("июл", 7),
    ("август", 8),
    ("сентябр", 9),
    ("октябр", 10),
    ("ноябр", 11),
    ("декабр", 12),
)

MONEY_RE = re.compile(r"-?\d[\d\s\u00a0]*[.,]\d{2}")

RECEIPT_SKIP_PREFIXES = (
    "НАЧИСЛЕНИЯ",
    "ВСЕГО",
    "ИТОГО",
    "ВИДЫ",
    "РАСЧЕТ",
    "ЕДИНЫЙ",
    "ПОЛУЧАТЕЛЬ",
    "ЛИЦЕВОЙ",
    "ВАША",
)

RECEIPT_GROUPS = (
    ("жилищные", "Жилищные услуги"),
    ("коммунальные", "Коммунальные услуги"),
    ("иные", "Иные услуги"),
)


def receipt_columns(text: str) -> dict:
    anchor = text.find("Виды услуг")
    window = (text[max(0, anchor - 2000) : anchor + 2000] if anchor != -1 else text).lower()
    return {
        "recalc": "перерасчет" in window,
        "debt": "задолженность" in window,
        "paid": "оплачено" in window,
    }


def receipt_has_recalc_column(text: str) -> bool:
    return receipt_columns(text)["recalc"]


def period_from_receipt(text: str) -> str | None:
    match = re.search(r"за\s+([А-ЯЁа-яё]+)\s+(\d{4})", text, re.I)
    if not match:
        return None
    word = match.group(1).lower()
    for root, number in MONTH_ROOTS:
        if word.startswith(root):
            return f"{int(match.group(2)):04d}-{number:02d}"
    return None


def parse_receipt_text(
    text: str,
    *,
    requested_month: str | None = None,
    requested_date: str | None = None,
    account_id: str = "",
    account_name: str = "",
) -> list[Charge]:
    period = period_from_receipt(text) or requested_month
    columns = receipt_columns(text)
    charged_index = -(2 + int(columns["recalc"]) + int(columns["debt"]) + int(columns["paid"]))
    rows: list[Charge] = []
    current_group = "Прочие услуги"

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        lower = stripped.lower()
        if lower.startswith("начисления за"):
            for keyword, group in RECEIPT_GROUPS:
                if keyword in lower:
                    current_group = group
                    break
            continue

        fields = [field.strip() for field in re.split(r"\s{2,}", stripped) if field.strip()]
        if len(fields) < 6:
            continue
        if not all(MONEY_RE.fullmatch(field) for field in fields[charged_index:]):
            continue

        name = fields[0]
        if name.upper().startswith(RECEIPT_SKIP_PREFIXES):
            continue

        if len(fields) >= 8:
            volume, unit, tariff = fields[1], fields[2], fields[3]
        else:
            volume, unit, tariff = None, fields[1], fields[2]

        charged = to_number(fields[charged_index])
        if charged <= 0:
            continue

        group = current_group
        if name.upper().startswith("ДОБРОВОЛЬНОЕ СТРАХОВАНИЕ"):
            group = "Иные услуги"

        rows.append(
            Charge(
                month=period or requested_month or "",
                requested_date=requested_date or "",
                account_id=account_id,
                account_name=account_name,
                service=name,
                group=group,
                unit=unit or "",
                volume=to_optional_number(volume),
                tariff=to_optional_number(tariff),
                charged=charged,
                total=charged,
                raw={"line": stripped},
            )
        )

    return rows


def pdf_support_available() -> bool:
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return False
    return True


def parse_receipt_pdf(data: bytes, **kwargs) -> list[Charge]:
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    text = "\n".join(page.extract_text(extraction_mode="layout") or "" for page in reader.pages)
    return parse_receipt_text(text, **kwargs)


class ReceiptCache:
    def __init__(self, directory: Path | str | None):
        self.directory = Path(directory) if directory else None

    def path(self, personal_account_id: str, month: str) -> Path | None:
        if not self.directory:
            return None
        return self.directory / str(personal_account_id) / f"{month}.pdf"

    def parsed_path(self, personal_account_id: str, month: str) -> Path | None:
        path = self.path(personal_account_id, month)
        return path.with_suffix(".json") if path else None

    def load(self, personal_account_id: str, month: str) -> bytes | None:
        path = self.path(personal_account_id, month)
        if not path or not path.exists():
            return None
        try:
            data = path.read_bytes()
        except OSError:
            return None
        return data if data.startswith(b"%PDF") else None

    def save(self, personal_account_id: str, month: str, data: bytes) -> None:
        path = self.path(personal_account_id, month)
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError:
            pass

    def load_parsed(self, personal_account_id: str, month: str) -> list[dict] | None:
        pdf_path = self.path(personal_account_id, month)
        parsed_path = self.parsed_path(personal_account_id, month)
        if not pdf_path or not parsed_path:
            return None
        if not pdf_path.exists() or not parsed_path.exists():
            return None
        try:
            if parsed_path.stat().st_mtime_ns < pdf_path.stat().st_mtime_ns:
                return None
            payload = json.loads(parsed_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        charges = payload.get("charges") if isinstance(payload, dict) else None
        return charges if isinstance(charges, list) else None

    def save_parsed(self, personal_account_id: str, month: str, charges: list[dict]) -> None:
        path = self.parsed_path(personal_account_id, month)
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"charges": charges}, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except OSError:
            pass


def charge_to_dict(row: Charge) -> dict:
    return {
        "month": row.month,
        "service": row.service,
        "group": row.group,
        "unit": row.unit,
        "volume": row.volume,
        "tariff": row.tariff,
        "charged": row.charged,
    }


def charge_from_dict(
    data: dict,
    *,
    requested_date: str,
    account_id: str,
    account_name: str,
) -> Charge:
    charged = to_number(data.get("charged"))
    return Charge(
        month=str(data.get("month") or ""),
        requested_date=requested_date,
        account_id=account_id,
        account_name=account_name,
        service=str(data.get("service") or "Без названия"),
        group=str(data.get("group") or ""),
        unit=str(data.get("unit") or ""),
        volume=to_optional_number(data.get("volume")),
        tariff=to_optional_number(data.get("tariff")),
        charged=charged,
        total=charged,
        raw={"parsed": True},
    )


def collect_receipt_charges(
    client: MosOblEIRCClient,
    accounts: list[dict],
    months: list[str],
    *,
    cache_dir: Path | str | None = None,
    force: bool = False,
    log=None,
    report: dict | None = None,
) -> list[Charge]:
    cache = ReceiptCache(cache_dir)
    rows: list[Charge] = []
    report = report if report is not None else {}

    for account in accounts:
        personal_account_id = account["personal_account_id"]
        account_id = str(account.get("id") or personal_account_id)
        account_name = str(account.get("name") or personal_account_id)

        for month in months:
            requested_date = f"{month}-01"

            if not force:
                cached = cache.load_parsed(personal_account_id, month)
                if cached is not None:
                    parsed = [
                        charge_from_dict(
                            item,
                            requested_date=requested_date,
                            account_id=account_id,
                            account_name=account_name,
                        )
                        for item in cached
                        if isinstance(item, dict)
                    ]
                    report["cached"] = report.get("cached", 0) + 1
                    if log:
                        log(account, month, f"(кэш разбора, услуг: {len(parsed)})")
                    rows.extend(parsed)
                    continue

            data = None if force else cache.load(personal_account_id, month)
            downloaded = False
            if data is None:
                try:
                    data = client.receipt_pdf(personal_account_id, requested_date)
                except MosOblEIRCError as error:
                    if log:
                        log(account, month, f"квитанции нет: {error}")
                    continue
                cache.save(personal_account_id, month, data)
                downloaded = True

            try:
                parsed = parse_receipt_pdf(
                    data,
                    requested_month=month,
                    requested_date=requested_date,
                    account_id=account_id,
                    account_name=account_name,
                )
            except Exception as error:
                if log:
                    log(account, month, f"PDF не распознан: {error}")
                continue

            cache.save_parsed(personal_account_id, month, [charge_to_dict(row) for row in parsed])

            if downloaded:
                report["downloaded"] = report.get("downloaded", 0) + 1
            else:
                report["parsed"] = report.get("parsed", 0) + 1
            if log:
                source = "скачано" if downloaded else "разбор PDF"
                log(account, month, f"({source}, услуг: {len(parsed)})")
            rows.extend(parsed)

    return rows


def collect_turnover(
    client: MosOblEIRCClient,
    accounts: list[dict],
    months: list[str],
    *,
    log=None,
) -> list[MonthTotal]:
    wanted = set(months)
    years = sorted({int(month[:4]) for month in months})
    rows: list[MonthTotal] = []

    for account in accounts:
        personal_account_id = account["personal_account_id"]
        for year in years:
            page = 0
            while page < 20:
                payload = client.turnover_statements(personal_account_id, year=year, page=page, size=100)
                results = payload.get("results") or []
                for item in results:
                    if not isinstance(item, dict):
                        continue
                    month = month_from_item(item)
                    if not month or month not in wanted:
                        continue
                    rows.append(
                        MonthTotal(
                            month=month,
                            account_id=str(account.get("id") or personal_account_id),
                            account_name=str(account.get("name") or personal_account_id),
                            accrued=to_number(item.get("accrualsAmount")),
                            paid=to_number(item.get("paidAmount")),
                            balance_start=to_number(item.get("balanceStart")),
                            balance_end=to_number(item.get("balanceEnd")),
                            raw=item,
                        )
                    )
                if log:
                    log(account, year, page, len(results))
                if not payload.get("hasMore") or not results:
                    break
                page += 1

    return rows


def aggregate_totals(rows: list[MonthTotal]):
    months: set[str] = set()
    accrued: dict[str, float] = {}
    paid: dict[str, float] = {}
    balance_end: dict[str, float] = {}

    for row in rows:
        months.add(row.month)
        accrued[row.month] = accrued.get(row.month, 0.0) + row.accrued
        paid[row.month] = paid.get(row.month, 0.0) + row.paid
        balance_end[row.month] = balance_end.get(row.month, 0.0) + row.balance_end

    sorted_months = sorted(months)
    return {
        "months": sorted_months,
        "accrued": accrued,
        "paid": paid,
        "balanceEnd": balance_end,
        "grandAccrued": sum(accrued.values()),
        "grandPaid": sum(paid.values()),
        "loaded": bool(rows),
        "source": "turnover",
    }


def month_signatures(rows: list[Charge]) -> dict[str, tuple]:
    by_month: dict[str, list] = {}
    for row in rows:
        by_month.setdefault(row.month, []).append((row.service, round(row.charged, 2)))
    return {month: tuple(sorted(items)) for month, items in by_month.items()}


def has_category_history(rows: list[Charge]) -> bool:
    signatures = set(month_signatures(rows).values())
    return len(signatures) > 1


def aggregate(rows: list[Charge], value: str = "charged"):
    field = VALUE_FIELDS.get(value)
    if not field:
        raise ValueError(f"Неизвестный показатель: {value}")

    months: set[str] = set()
    services: dict[str, dict[str, float]] = {}
    totals: dict[str, float] = {}
    grand_total = 0.0

    for row in rows:
        amount = getattr(row, field) or 0.0
        months.add(row.month)
        services.setdefault(row.service, {})
        services[row.service][row.month] = services[row.service].get(row.month, 0.0) + amount
        totals[row.month] = totals.get(row.month, 0.0) + amount
        grand_total += amount

    sorted_months = sorted(months)
    ordered = dict(
        sorted(services.items(), key=lambda pair: sum(pair[1].values()), reverse=True)
    )
    return sorted_months, ordered, totals, grand_total


def format_amount(value: float | None) -> str:
    return f"{value or 0.0:,.2f}".replace(",", " ")


def format_quantity(value: float | None) -> str:
    text = f"{value or 0.0:,.4f}".replace(",", " ").replace("\u00a0", " ")
    return text.rstrip("0").rstrip(".") if "." in text else text


def render_grid(headers: list[str], table: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for line in table:
        for index, cell in enumerate(line):
            widths[index] = max(widths[index], len(cell))

    def render_line(line: list[str]) -> str:
        cells = [line[0].ljust(widths[0])]
        cells.extend(cell.rjust(widths[index]) for index, cell in enumerate(line[1:], start=1))
        return "  ".join(cells).rstrip()

    separator = "-" * (sum(widths) + 2 * (len(widths) - 1))
    lines = [render_line(headers), separator]
    lines.extend(render_line(line) for line in table)
    return "\n".join(lines)


def render_table(rows: list[Charge], value: str = "charged") -> str:
    months, services, totals, grand_total = aggregate(rows, value)
    if not months:
        return "Нет данных"

    formatter = format_quantity if value == "volume" else format_amount
    units = {row.service: row.unit for row in rows if row.unit}
    headers = ["Категория", *months, "Итого"]
    table: list[list[str]] = []

    for service, by_month in services.items():
        service_total = sum(by_month.values())
        label = f"{service} ({units[service]})" if value == "volume" and service in units else service
        table.append(
            [label, *[formatter(by_month.get(month)) for month in months], formatter(service_total)]
        )

    if value != "volume":
        table.append(["ИТОГО", *[formatter(totals.get(month)) for month in months], formatter(grand_total)])

    return render_grid(headers, table)


def render_totals_csv(rows: list[MonthTotal]) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(["month", "account", "accrued", "paid", "balance_start", "balance_end"])
    for row in sorted(rows, key=lambda item: (item.month, item.account_name)):
        writer.writerow(
            [
                row.month,
                row.account_name,
                row.accrued,
                row.paid,
                row.balance_start,
                row.balance_end,
            ]
        )
    return buffer.getvalue()


def render_totals_json(rows: list[MonthTotal]) -> str:
    data = aggregate_totals(rows)
    payload = {
        **{key: value for key, value in data.items() if key != "months"},
        "months": data["months"],
        "rows": [
            {
                "month": row.month,
                "account": row.account_name,
                "accrued": row.accrued,
                "paid": row.paid,
                "balanceStart": row.balance_start,
                "balanceEnd": row.balance_end,
            }
            for row in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_totals_table(rows: list[MonthTotal]) -> str:
    data = aggregate_totals(rows)
    months = data["months"]
    if not months:
        return "Нет данных"

    has_accrued = any(data["accrued"].values())
    has_balance = any(data["balanceEnd"].values())

    headers = ["Месяц"]
    if has_accrued:
        headers.append("Начислено")
    headers.append("Оплачено")
    if has_balance:
        headers.append("Баланс на конец")

    table: list[list[str]] = []
    for month in months:
        line = [month]
        if has_accrued:
            line.append(format_amount(data["accrued"].get(month)))
        line.append(format_amount(data["paid"].get(month)))
        if has_balance:
            line.append(format_amount(data["balanceEnd"].get(month)))
        table.append(line)

    total_line = ["ИТОГО"]
    if has_accrued:
        total_line.append(format_amount(data["grandAccrued"]))
    total_line.append(format_amount(data["grandPaid"]))
    if has_balance:
        total_line.append("")
    table.append(total_line)

    return render_grid(headers, table)


def render_csv(rows: list[Charge]) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(["month", "account", "service", "unit", "volume", "tariff", "charged"])
    for row in sorted(rows, key=lambda item: (item.month, item.account_name, item.service)):
        writer.writerow(
            [
                row.month,
                row.account_name,
                row.service,
                row.unit,
                "" if row.volume is None else row.volume,
                "" if row.tariff is None else row.tariff,
                row.charged,
            ]
        )
    return buffer.getvalue()


def render_json(rows: list[Charge], value: str = "charged") -> str:
    months, services, totals, grand_total = aggregate(rows, value)
    payload = {
        "value": value,
        "months": months,
        "services": services,
        "totals": totals,
        "grandTotal": grand_total,
        "charges": [
            {
                "month": row.month,
                "account": row.account_name,
                "service": row.service,
                "unit": row.unit,
                "volume": row.volume,
                "tariff": row.tariff,
                "charged": row.charged,
                "requestedDate": row.requested_date,
            }
            for row in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
