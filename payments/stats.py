from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import Store

MONTH_RE = re.compile(r"(\d{4})-(\d{1,2})")

# Увеличивать при изменениях парсеров: кэш в БД будет перечитан при следующем ⟳.
PARSER_VERSION = 4

VALUE_FIELDS = {
    "charged": "charged",
    "volume": "volume",
}

SUPPLIER_LABELS = {
    "mosobleirc": "МосОблЕИРЦ",
}

HOUSING_GROUP = "Жилищные услуги"
SHARED_GROUP = "Общедомовые нужды (КР на СОИ)"
UTILITY_GROUP = "Коммунальные услуги"
OTHER_GROUP = "Иные услуги"
FALLBACK_GROUP = "Услуги"

# Порядок разделов снизу вверх: жилищные → общедомовые → коммунальные → иные.
GROUP_ORDER = (HOUSING_GROUP, SHARED_GROUP, UTILITY_GROUP, OTHER_GROUP, FALLBACK_GROUP)


def month_add(month: str, delta: int) -> str:
    year, mon = (int(part) for part in month.split("-"))
    total = year * 12 + (mon - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def month_range(end_month: str, count: int) -> list[str]:
    return [month_add(end_month, offset) for offset in range(-count + 1, 1)]


def supplier_label(name: str | None) -> str:
    if not name:
        return ""
    return SUPPLIER_LABELS.get(name, name)


def month_from_filename(name: str) -> str | None:
    for match in MONTH_RE.finditer(Path(name).stem):
        month = int(match.group(2))
        if 1 <= month <= 12:
            return f"{int(match.group(1)):04d}-{month:02d}"
    return None


def iter_supplier_dirs(directory: Path | str | None) -> list[Path]:
    if not directory:
        return []
    root = Path(directory)
    if not root.is_dir():
        return []
    return sorted(
        child
        for child in root.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    )


def find_supplier_receipt(
    directory: Path | str | None, month: str, supplier: str | None = None
) -> Path | None:
    """Квитанция за месяц; при указании supplier ищем только в его папке."""
    for supplier_dir in iter_supplier_dirs(directory):
        if supplier and supplier_dir.name != supplier:
            continue
        for pdf_path in sorted(supplier_dir.rglob("*.pdf")):
            if month_from_filename(pdf_path.name) == month:
                return pdf_path
    return None


def scan_suppliers(directory: Path | str | None) -> list[dict]:
    return [_scan_supplier_dir(path) for path in iter_supplier_dirs(directory)]


def _scan_supplier_dir(path: Path) -> dict:
    pdf_files = [item for item in sorted(path.rglob("*.pdf")) if item.is_file()]
    months = sorted(
        month for month in (month_from_filename(item.name) for item in pdf_files) if month
    )
    return {
        "name": path.name,
        "label": supplier_label(path.name),
        "files": len(pdf_files),
        "months": months,
    }


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


@dataclass
class Charge:
    month: str
    service: str
    supplier: str = ""
    group: str = ""
    unit: str = ""
    volume: float | None = None
    tariff: float | None = None
    charged: float = 0.0
    total: float = 0.0
    reading_start: float | None = None
    reading_end: float | None = None
    raw: dict = field(default_factory=dict)


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
    ("жилищные", HOUSING_GROUP),
    ("коммунальные", UTILITY_GROUP),
    ("иные", OTHER_GROUP),
)

# Каноническая группа по названию услуги: одна и та же услуга должна попадать
# в один раздел у всех поставщиков, независимо от вёрстки платёжки.
GROUP_KEYWORDS = (
    (OTHER_GROUP, ("СТРАХОВАН", "АНТЕНН", "РАДИОТОЧК", "КАБЕЛЬН", "ТЕЛЕФОН")),
    (
        HOUSING_GROUP,
        (
            "СОДЕРЖАН",
            "КАПИТАЛЬН",
            "КАПРЕМОНТ",
            "КАП.РЕМОНТ",
            "КАП. РЕМОНТ",
            "ТЕКУЩ",
            "РЕМОНТ",
            "УПРАВЛЕН",
            "ОХРАН",
            "КОНСЬЕРЖ",
            "ДОМОФОН",
            "ВАХТ",
            "ДИСПЕТЧЕР",
            "ЛИФТ",
            "БЛАГОУСТР",
            "ПРИДОМОВ",
        ),
    ),
    (
        UTILITY_GROUP,
        (
            "ХОЛОДН",
            "ГОРЯЧ",
            "ВОДООТВЕД",
            "ВОДОСНАБЖ",
            "ВОДОСН",
            "ВОДА",
            "В/С",
            "ГВС",
            "ХВС",
            "КАНАЛИЗ",
            "ГАЗ",
            "ЭЛЕКТРО",
            "ЭНЕРГ",
            "ТЕПЛ",
            "ОТОПЛ",
            "ПОДОГРЕВ",
            "ТКО",
            "ОБРАЩЕН",
            "ОТХОД",
        ),
    ),
)

SHARED_TOKENS = {"ОДН", "СОИ", "КРСОИ", "КР"}


def _is_shared_resource(name: str) -> bool:
    """ОДН/КР на СОИ: проверяем по словам, чтобы «холодное» не ловилось на «ОДН»."""
    tokens = re.split(r"[^0-9A-ZА-ЯЁ]+", name.upper())
    return any(
        token in SHARED_TOKENS or token.startswith("ОБЩЕДОМ") for token in tokens
    )


def canonical_service_group(name: str, fallback: str = "") -> str:
    """Определяет раздел услуги по её названию (ЖК РФ, ст. 154).

    ОДН/КР на СОИ — ресурсы на содержание общего имущества; в квитанциях идут
    отдельной строкой, поэтому показываем их отдельным разделом.
    """
    if _is_shared_resource(name):
        return SHARED_GROUP
    upper = name.upper()
    for group, keywords in GROUP_KEYWORDS:
        if any(keyword in upper for keyword in keywords):
            return group
    return fallback or UTILITY_GROUP


MES_HEADER_RE = re.compile(r"СЧЁТ\s+ЗА\s+ЭЛЕКТРОЭНЕРГИЮ\s*/\s*([А-ЯЁа-яё]+)\s+(\d{4})", re.I)
MES_ZONE_RE = re.compile(r"^\(Т(\d+)\)\s*(.+)$", re.I)
MES_ZONE_LABELS = {"1": "ДЕНЬ", "2": "НОЧЬ"}

UK_UNITS = ("Гк", "Гкал", "м3", "м³", "кВт∙ч", "кВт·ч", "м2", "м²")
SUMMARY_SERVICE = "ЖКУ (ИТОГ ПО КВИТАНЦИИ)"
UK_TOTAL_RE = re.compile(
    r"(?:К оплате за|Начислено за)\s+([А-ЯЁа-яё]+)\s+(\d{4})[^\d]{0,40}?([\d\s\u00a0]+[.,]\d{2})",
    re.I,
)


def receipt_columns(text: str) -> dict:
    anchor = text.find("Виды услуг")
    window = (text[max(0, anchor - 2000) : anchor + 2000] if anchor != -1 else text).lower()
    return {
        "recalc": "перерасчет" in window,
        "debt": "задолженность" in window,
        "paid": "оплачено" in window,
    }


def month_from_word(word: str, year: str) -> str | None:
    word = word.lower()
    for root, number in MONTH_ROOTS:
        if word.startswith(root):
            return f"{int(year):04d}-{number:02d}"
    return None


def period_from_receipt(text: str) -> str | None:
    match = re.search(r"за\s+([А-ЯЁа-яё]+)\s+(\d{4})", text, re.I)
    if not match:
        return None
    return month_from_word(match.group(1), match.group(2))


def is_mes_receipt(text: str) -> bool:
    return bool(MES_HEADER_RE.search(text) or MES_ZONE_RE.search(text))


def detect_receipt_format(text: str) -> str:
    """Формат платёжки: mes (Мосэнергосбыт), epd (ЕПД), uk (квитанция УК) или пустая строка."""
    if is_mes_receipt(text):
        return "mes"
    lower = text.lower()
    if "единый платежный документ" in lower or "мособлеирц" in lower:
        return "epd"
    if (
        "расчет размера платы за жилое помещение" in lower
        or "жилищно-коммунальные и иные услуги" in lower
        or "расшифровка счета" in lower
    ):
        return "uk"
    if "виды услуг" in lower or "расчет размера платы" in lower or "расчёт размера платы" in lower:
        return "epd"
    return ""


def period_from_mes_receipt(text: str) -> str | None:
    match = MES_HEADER_RE.search(text)
    if not match:
        return None
    return month_from_word(match.group(1), match.group(2))


def parse_mes_receipt_text(
    text: str,
    *,
    requested_month: str | None = None,
    supplier: str = "",
) -> list[Charge]:
    """Счёт АО «Мосэнергосбыт»: строки (Т1) день / (Т2) ночь с показаниями, расходом, тарифом и суммой."""
    period = period_from_mes_receipt(text) or requested_month
    rows: list[Charge] = []

    for line in text.splitlines():
        stripped = line.strip()
        match = MES_ZONE_RE.match(stripped)
        if not match:
            continue
        fields = [field.strip() for field in re.split(r"\s{2,}", stripped) if field.strip()]
        if len(fields) < 5:
            continue
        if not (MONEY_RE.fullmatch(fields[-1]) and MONEY_RE.fullmatch(fields[-2])):
            continue

        charged = to_number(fields[-1])
        tariff = to_number(fields[-2])
        middle = fields[1:-2]
        volume = to_optional_number(middle[-1]) if len(middle) >= 3 else None
        reading_start = to_optional_number(middle[0]) if len(middle) >= 2 else None
        reading_end = to_optional_number(middle[1]) if len(middle) >= 2 else None
        if charged <= 0 and volume is None:
            continue

        zone = match.group(1)
        label = MES_ZONE_LABELS.get(zone) or match.group(2).strip().upper()
        rows.append(
            Charge(
                month=period or "",
                service=f"ЭЛЕКТРОЭНЕРГИЯ (Т{zone}) {label}",
                supplier=supplier,
                group=canonical_service_group(f"ЭЛЕКТРОЭНЕРГИЯ (Т{zone}) {label}"),
                unit="кВт∙ч",
                volume=volume,
                tariff=tariff,
                charged=charged,
                total=charged,
                reading_start=reading_start,
                reading_end=reading_end,
                raw={"line": stripped},
            )
        )

    return rows


def service_group(name: str) -> str:
    return canonical_service_group(name)


def parse_uk_receipt_text(
    text: str,
    *,
    requested_month: str | None = None,
    supplier: str = "",
) -> list[Charge]:
    """Квитанция УК (ООО «КП»): таблица «Расчёт размера платы» с колонкой «Начислено».

    Если таблица пустая (сводная квитанция без расшифровки) — возвращается одна строка
    с итоговой суммой (SUMMARY_SERVICE).
    """
    period = period_from_receipt(text) or requested_month
    rows: list[Charge] = []
    in_table = False

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "РАСЧЕТ РАЗМЕРА ПЛАТЫ" in stripped:
            in_table = True
            continue
        if not in_table:
            continue
        if stripped.startswith("ИТОГО") or stripped.startswith("Личный кабинет") or "СПРАВОЧНАЯ" in stripped:
            break

        fields = [field.strip() for field in re.split(r"\s{2,}", stripped) if field.strip()]
        if len(fields) < 6:
            continue
        unit_index = next((index for index, field in enumerate(fields) if field in UK_UNITS), None)
        if unit_index is None or unit_index == 0 or unit_index + 2 >= len(fields):
            continue
        volume_text = fields[unit_index - 1]
        if not re.fullmatch(r"[\d\s\u00a0]+[.,]?\d*", volume_text):
            continue
        if not (MONEY_RE.fullmatch(fields[unit_index + 1]) and MONEY_RE.fullmatch(fields[unit_index + 2])):
            continue

        charged = to_number(fields[unit_index + 2])
        if charged <= 0:
            continue
        name = fields[0]
        if name.upper().startswith(RECEIPT_SKIP_PREFIXES):
            continue
        rows.append(
            Charge(
                month=period or "",
                service=name,
                supplier=supplier,
                group=service_group(name),
                unit=fields[unit_index],
                volume=to_number(volume_text),
                tariff=to_number(fields[unit_index + 1]),
                charged=charged,
                total=charged,
                raw={"line": stripped},
            )
        )

    if rows:
        return rows

    match = UK_TOTAL_RE.search(text)
    if match:
        total = to_number(match.group(3))
        if total > 0:
            return [
                Charge(
                    month=month_from_word(match.group(1), match.group(2)) or period or "",
                    service=SUMMARY_SERVICE,
                    supplier=supplier,
                    group=UTILITY_GROUP,
                    charged=total,
                    total=total,
                    raw={"summary": True},
                )
            ]
    return []


def is_summary_row(row: Charge) -> bool:
    return row.service == SUMMARY_SERVICE


def drop_duplicate_summaries(rows: list[Charge], *, log=None) -> tuple[list[Charge], int]:
    """Убирает сводные квитанции-дубли: если за месяц есть детализация с той же суммой."""
    detailed: dict[str, float] = {}
    for row in rows:
        if not is_summary_row(row):
            detailed[row.month] = detailed.get(row.month, 0.0) + row.charged

    kept: list[Charge] = []
    dropped = 0
    for row in rows:
        if is_summary_row(row) and abs(detailed.get(row.month, 0.0) - row.charged) < 0.01:
            dropped += 1
            if log:
                log(row.supplier, row.month, f"(сводная квитанция {row.charged:.2f} ₽ — дубль детализации, пропущена)")
            continue
        kept.append(row)
    return kept, dropped


def parse_receipt_text(
    text: str,
    *,
    requested_month: str | None = None,
    supplier: str = "",
) -> list[Charge]:
    receipt_format = detect_receipt_format(text)
    if receipt_format == "mes":
        return parse_mes_receipt_text(text, requested_month=requested_month, supplier=supplier)
    if receipt_format == "uk":
        return parse_uk_receipt_text(text, requested_month=requested_month, supplier=supplier)
    if receipt_format == "epd":
        return parse_epd_receipt_text(text, requested_month=requested_month, supplier=supplier)
    return []


def parse_receipt_document(data: bytes, **kwargs) -> tuple[list[Charge], str]:
    """Разбирает PDF и возвращает (строки, формат): формат пустой, если платёжка не распознана."""
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    text = "\n".join(page.extract_text(extraction_mode="layout") or "" for page in reader.pages)
    receipt_format = detect_receipt_format(text)
    if receipt_format == "mes":
        rows = parse_mes_receipt_text(text, **kwargs)
    elif receipt_format == "uk":
        rows = parse_uk_receipt_text(text, **kwargs)
    elif receipt_format == "epd":
        rows = parse_epd_receipt_text(text, **kwargs)
    else:
        rows = []
    return rows, receipt_format


def parse_epd_receipt_text(
    text: str,
    *,
    requested_month: str | None = None,
    supplier: str = "",
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

        group = canonical_service_group(name, current_group)

        rows.append(
            Charge(
                month=period or "",
                service=name,
                supplier=supplier,
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
    rows, _ = parse_receipt_document(data, **kwargs)
    return rows


def charge_to_dict(row: Charge) -> dict:
    return {
        "month": row.month,
        "service": row.service,
        "supplier": row.supplier,
        "group": row.group,
        "unit": row.unit,
        "volume": row.volume,
        "tariff": row.tariff,
        "charged": row.charged,
        "readingStart": row.reading_start,
        "readingEnd": row.reading_end,
    }


def charge_from_dict(data: dict, *, supplier: str = "") -> Charge:
    charged = to_number(data.get("charged"))
    return Charge(
        month=str(data.get("month") or ""),
        service=str(data.get("service") or "Без названия"),
        supplier=str(data.get("supplier") or supplier),
        group=str(data.get("group") or ""),
        unit=str(data.get("unit") or ""),
        volume=to_optional_number(data.get("volume")),
        tariff=to_optional_number(data.get("tariff")),
        charged=charged,
        total=charged,
        reading_start=to_optional_number(data.get("readingStart")),
        reading_end=to_optional_number(data.get("readingEnd")),
        raw={"parsed": True},
    )


def _supplier_note(
    report: dict,
    log,
    store: Store | None,
    supplier: str,
    pdf_path: Path,
    message: str,
) -> None:
    if store is not None:
        store.mark_receipt_skipped(
            pdf_path,
            supplier=supplier,
            note=message,
            parser_version=PARSER_VERSION,
        )
    report.setdefault("warnings", []).append(
        f"{supplier_label(supplier)}: {pdf_path.name} — {message}"
    )
    if log:
        log(supplier, pdf_path.name, f"(пропущено: {message})")


def collect_receipt_charges(
    directory: Path | str | None,
    months: list[str] | None,
    *,
    store: Store | None = None,
    force: bool = False,
    log=None,
    report: dict | None = None,
) -> list[Charge]:
    """Читает PDF из папок поставщиков ({каталог}/{поставщик}/**/*.pdf) и разбирает их
    парсером по формату (ЕПД, счёт Мосэнергосбыта, квитанция УК).

    months=None — вернуть все месяцы. Сводные квитанции без расшифровки пропускаются,
    если за тот же месяц есть детализация с той же суммой.
    """
    report = report if report is not None else {}
    wanted = set(months) if months else None
    rows: list[Charge] = []
    if not directory:
        return rows

    for supplier_dir in iter_supplier_dirs(directory):
        supplier = supplier_dir.name
        files_per_month: dict[str, int] = {}

        for pdf_path in sorted(supplier_dir.rglob("*.pdf")):
            report.setdefault("files", []).append(str(pdf_path))
            cached = (
                None
                if force or store is None
                else store.load_receipt_charges(pdf_path, parser_version=PARSER_VERSION)
            )
            if cached is not None:
                parsed = [
                    charge_from_dict(item, supplier=supplier)
                    for item in cached
                    if isinstance(item, dict)
                ]
                source = "кэш разбора"
                report["cached"] = report.get("cached", 0) + 1
            else:
                try:
                    data = pdf_path.read_bytes()
                except OSError as error:
                    _supplier_note(report, log, store, supplier, pdf_path, f"не прочитать: {error}")
                    continue
                if not data.startswith(b"%PDF"):
                    _supplier_note(report, log, store, supplier, pdf_path, "не PDF")
                    continue
                fallback_month = month_from_filename(pdf_path.name)
                try:
                    parsed, receipt_format = parse_receipt_document(
                        data,
                        requested_month=fallback_month,
                        supplier=supplier,
                    )
                except Exception as error:
                    _supplier_note(report, log, store, supplier, pdf_path, f"PDF не распознан: {error}")
                    continue
                if not receipt_format:
                    _supplier_note(report, log, store, supplier, pdf_path, "формат не распознан")
                    continue
                if not parsed and receipt_format != "mes":
                    _supplier_note(
                        report, log, store, supplier, pdf_path, "в PDF не найдено строк начислений"
                    )
                    continue
                if parsed and not all(row.month for row in parsed):
                    _supplier_note(
                        report,
                        log,
                        store,
                        supplier,
                        pdf_path,
                        "не удалось определить месяц — добавьте ГГГГ-ММ в имя файла",
                    )
                    continue
                summary = bool(parsed) and all(is_summary_row(row) for row in parsed)
                if store is not None:
                    if summary:
                        # сводные квитанции не кэшируем: их учитывают только при отсутствии детализации
                        store.delete_receipt_charges(pdf_path)
                    else:
                        store.save_receipt_charges(
                            pdf_path,
                            supplier=supplier,
                            account_id="",
                            month=parsed[0].month if parsed else (fallback_month or ""),
                            charges=[charge_to_dict(row) for row in parsed],
                            parser_version=PARSER_VERSION,
                        )
                report["parsed"] = report.get("parsed", 0) + 1
                source = "сводная квитанция" if summary else ("разбор PDF" if parsed else "разбор PDF (начислений нет)")

            if not parsed:
                continue

            selected = [row for row in parsed if wanted is None or row.month in wanted]
            if not selected:
                if log:
                    found = ", ".join(sorted({row.month for row in parsed if row.month})) or "?"
                    log(supplier, pdf_path.name, f"(вне выбранного периода: {found})")
                continue

            for row in selected:
                row.supplier = supplier
            for month in {row.month for row in selected}:
                files_per_month[month] = files_per_month.get(month, 0) + 1
            if log:
                found = ", ".join(sorted({row.month for row in selected}))
                log(supplier, found, f"({source}, услуг: {len(selected)})")
            rows.extend(selected)

        for month, count in sorted(files_per_month.items()):
            if count > 1:
                report.setdefault("warnings", []).append(
                    f"{supplier_label(supplier)}: за {month} найдено файлов: {count} — суммы сложены"
                )

    rows, dropped = drop_duplicate_summaries(rows, log=log)
    if dropped:
        report["duplicates"] = report.get("duplicates", 0) + dropped
    return rows


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


def render_csv(rows: list[Charge]) -> str:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(
        ["month", "supplier", "service", "unit", "volume", "tariff", "charged", "reading_start", "reading_end"]
    )
    for row in sorted(rows, key=lambda item: (item.month, item.supplier, item.service)):
        writer.writerow(
            [
                row.month,
                supplier_label(row.supplier),
                row.service,
                row.unit,
                "" if row.volume is None else row.volume,
                "" if row.tariff is None else row.tariff,
                row.charged,
                "" if row.reading_start is None else row.reading_start,
                "" if row.reading_end is None else row.reading_end,
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
                "supplier": supplier_label(row.supplier),
                "service": row.service,
                "unit": row.unit,
                "volume": row.volume,
                "tariff": row.tariff,
                "charged": row.charged,
                "readingStart": row.reading_start,
                "readingEnd": row.reading_end,
            }
            for row in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
