from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .stats import (
    VALUE_FIELDS,
    Charge,
    collect_receipt_charges,
    month_range,
    pdf_support_available,
    render_csv,
    render_grid,
    render_json,
    render_table,
    scan_suppliers,
)
from .store import Store

DEFAULT_DB_PATH = Path("data/mosobleirc.sqlite")
DEFAULT_RECEIPTS_DIR = Path("data/receipts")


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def read_text_any_encoding(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
        if "\x00" not in text:
            return text
    return data.decode("utf-8", errors="replace")


def parse_dotenv_value(raw: str) -> str:
    value = raw.strip()
    if value[:1] in ('"', "'"):
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            return value[1:end]
        return value[1:]
    for marker in (" #", "\t#"):
        if marker in value:
            value = value.split(marker, 1)[0]
    return value.strip()


def load_dotenv(path: str | Path = ".env") -> None:
    dotenv = Path(path)
    if not dotenv.exists():
        return
    try:
        lines = read_text_any_encoding(dotenv).splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = parse_dotenv_value(value)
        if key and key not in os.environ:
            os.environ[key] = value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mosobleirc",
        description="Начисления по месяцам и категориям из платёжек в папках поставщиков. "
        "Все параметры можно задавать переменными окружения (MOSOBLEIRC_*) или файлом .env",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    web = subparsers.add_parser("web", help="локальная веб-страница с графиком начислений")
    add_period_options(web)
    add_value_option(web)
    web.add_argument("--host", default=os.environ.get("MOSOBLEIRC_HOST", "0.0.0.0"), help="адрес веб-сервера")
    web.add_argument("--port", type=int, default=env_int("MOSOBLEIRC_PORT", 8765), help="порт веб-сервера")
    web.set_defaults(func=cmd_web)

    months = subparsers.add_parser("months", help="таблица начислений по месяцам и категориям")
    add_period_options(months)
    add_value_option(months)
    add_output_options(months)
    months.set_defaults(func=cmd_months)

    suppliers = subparsers.add_parser("suppliers", help="папки поставщиков в каталоге платёжек")
    suppliers.add_argument(
        "--receipts-dir",
        default=os.environ.get("MOSOBLEIRC_RECEIPTS_DIR", str(DEFAULT_RECEIPTS_DIR)),
        help="корневой каталог с папками поставщиков (по умолчанию data/receipts)",
    )
    suppliers.set_defaults(func=cmd_suppliers)

    return parser


def add_data_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=os.environ.get("MOSOBLEIRC_DB", str(DEFAULT_DB_PATH)),
        help="файл БД SQLite с кэшем разбора (по умолчанию data/mosobleirc.sqlite)",
    )
    parser.add_argument(
        "--receipts-dir",
        default=os.environ.get("MOSOBLEIRC_RECEIPTS_DIR", str(DEFAULT_RECEIPTS_DIR)),
        help="корневой каталог с папками поставщиков: {каталог}/{поставщик}/**/*.pdf",
    )
    parser.add_argument("--no-cache", action="store_true", help="не читать и не писать кэш")
    parser.add_argument("--force", action="store_true", help="разобрать PDF заново, игнорируя кэш")


def add_period_options(parser: argparse.ArgumentParser) -> None:
    add_data_options(parser)
    parser.add_argument("--months", type=int, default=env_int("MOSOBLEIRC_MONTHS", 12), help="сколько месяцев (по умолчанию 12)")
    parser.add_argument(
        "--end-month",
        default=os.environ.get("MOSOBLEIRC_END_MONTH"),
        help="последний месяц в формате YYYY-MM (по умолчанию последний месяц в папках)",
    )


def add_value_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--value",
        choices=sorted(VALUE_FIELDS),
        default=os.environ.get("MOSOBLEIRC_VALUE", "charged"),
        help="показатель: charged — начислено, volume — объём (по умолчанию charged)",
    )


def add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("table", "csv", "json"), default="table", help="формат вывода")
    parser.add_argument("--out", help="файл для записи результата (по умолчанию stdout)")


def collect_rows(args) -> list[Charge]:
    receipts_dir = None if args.no_cache else args.receipts_dir
    store = Store(None if args.no_cache else args.db)

    if not pdf_support_available():
        print(
            "PDF не разбираются: установите зависимости — pip install -r requirements.txt (нужен pypdf)",
            file=sys.stderr,
        )
        store.close()
        return []

    def log(supplier, month, note):
        print(f"  {supplier}: {month} {note}", file=sys.stderr)

    if args.end_month:
        month_list = month_range(args.end_month, args.months)
        print(f"Собираю платёжки за {month_list[0]}..{month_list[-1]}...", file=sys.stderr)
        rows = collect_receipt_charges(
            receipts_dir, month_list, store=store, force=args.force, log=log
        )
    else:
        print("Собираю платёжки из папок...", file=sys.stderr)
        rows = collect_receipt_charges(
            receipts_dir, None, store=store, force=args.force, log=log
        )
        available = sorted({row.month for row in rows if row.month})
        selected = set(available[-args.months :])
        rows = [row for row in rows if row.month in selected]

    store.close()
    return rows


def write_output(text: str, path: str | None) -> None:
    if path:
        Path(path).write_text(text, encoding="utf-8-sig" if path.endswith(".csv") else "utf-8")
        print(f"Записано в {path}", file=sys.stderr)
    else:
        print(text)


def cmd_web(args) -> int:
    from .webapp import WebConfig, run_server

    config = WebConfig(
        months=args.months,
        value=args.value,
        end_month=args.end_month,
        db_path=None if args.no_cache else args.db,
        receipts_dir=None if args.no_cache else args.receipts_dir,
        host=args.host,
        port=args.port,
    )
    run_server(config)
    return 0


def cmd_months(args) -> int:
    rows = collect_rows(args)

    if args.format == "table":
        output = render_table(rows, args.value)
    elif args.format == "csv":
        output = render_csv(rows)
    else:
        output = render_json(rows, args.value)

    write_output(output, args.out)
    return 0


def cmd_suppliers(args) -> int:
    suppliers = scan_suppliers(args.receipts_dir)
    if not suppliers:
        print(
            f"В {args.receipts_dir} нет папок поставщиков. Создайте папку с именем поставщика "
            "и положите в неё PDF, например: data/receipts/mosenergosbyt/2026-08.pdf",
            file=sys.stderr,
        )
        return 0

    table: list[list[str]] = []
    for supplier in suppliers:
        months = supplier["months"]
        if len(months) > 1:
            span = f"{months[0]}..{months[-1]}"
        else:
            span = months[0] if months else "—"
        table.append([supplier["label"], str(supplier["files"]), span])

    print(render_grid(["Поставщик", "PDF", "Месяцы"], table))
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("Прервано", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
