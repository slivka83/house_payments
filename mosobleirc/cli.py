from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .client import MosOblEIRCClient, MosOblEIRCError
from .stats import (
    VALUE_FIELDS,
    ChargeCache,
    anchor_date,
    collect_charges,
    collect_receipt_charges,
    collect_turnover,
    format_amount,
    has_category_history,
    pdf_support_available,
    month_add,
    month_range,
    normalize_charges,
    previous_month,
    render_csv,
    render_json,
    render_table,
    render_totals_csv,
    render_totals_json,
    render_totals_table,
)
from .tokens import load_token, save_token

DEFAULT_CACHE_DIR = Path("data/raw")
DEFAULT_TOKEN_FILE = Path.home() / ".cache" / "mosobleirc" / "token.json"


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_list(name: str) -> list[str]:
    value = os.environ.get(name)
    if not value:
        return []
    items = [item.strip() for item in value.replace(";", ",").split(",")]
    return [item for item in items if item]


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
        description="Статистика начислений МосОблЕИРЦ по месяцам и категориям (неофициальный клиент ЛКК). "
        "Все параметры можно задавать переменными окружения (MOSOBLEIRC_*) или файлом .env",
    )
    parser.add_argument("--phone", default=os.environ.get("MOSOBLEIRC_PHONE"), help="телефон ЛКК")
    parser.add_argument("--password", default=os.environ.get("MOSOBLEIRC_PASSWORD"), help="пароль ЛКК")
    parser.add_argument(
        "--token",
        default=os.environ.get("MOSOBLEIRC_TOKEN"),
        help="готовый X-Auth-Tenant-Token (например, из браузера)",
    )
    parser.add_argument(
        "--token-file",
        default=os.environ.get("MOSOBLEIRC_TOKEN_FILE", str(DEFAULT_TOKEN_FILE)),
        help="файл кэша токена",
    )
    parser.add_argument("--no-token-cache", action="store_true", help="не читать и не писать кэш токена")
    parser.add_argument("--debug", action="store_true", help="показывать трейсбеки ошибок")

    subparsers = parser.add_subparsers(dest="command", required=True)

    accounts = subparsers.add_parser("accounts", help="список лицевых счетов")
    accounts.set_defaults(func=cmd_accounts)

    web = subparsers.add_parser("web", help="локальная веб-страница с графиком начислений")
    add_web_options(web)
    web.set_defaults(func=cmd_web)

    turnover = subparsers.add_parser("turnover", help="начислено/оплачено по месяцам (оборотная ведомость)")
    add_months_options(turnover)
    turnover.add_argument("--format", choices=("table", "csv", "json"), default="table", help="формат вывода")
    turnover.add_argument("--out", help="файл для записи результата (по умолчанию stdout)")
    turnover.set_defaults(func=cmd_turnover)

    months = subparsers.add_parser("months", help="таблица начислений по месяцам и категориям")
    add_months_options(months)
    add_report_options(months)
    months.set_defaults(func=cmd_months)

    collect = subparsers.add_parser("collect", help="загрузить и закэшировать данные без вывода таблицы")
    add_months_options(collect)
    collect.set_defaults(func=cmd_collect)

    probe = subparsers.add_parser("probe", help="диагностика: какие периоды отдаёт API для разных дат")
    add_common_data_options(probe)
    probe.add_argument("--months", type=int, default=env_int("MOSOBLEIRC_MONTHS", 4), help="сколько месяцев проверять (по умолчанию 4)")
    probe.add_argument("--anchor-days", default="1,15,28", help="дни месяца для проверки (по умолчанию 1,15,28)")
    probe.add_argument("--shift", default="0,1", help="сдвиги месяца запроса (по умолчанию 0,1)")
    probe.set_defaults(func=cmd_probe)

    return parser


def add_common_data_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--account",
        action="append",
        default=env_list("MOSOBLEIRC_ACCOUNT") or None,
        help="id или часть названия ЛС (можно несколько раз)",
    )
    parser.add_argument(
        "--cache-dir",
        default=os.environ.get("MOSOBLEIRC_CACHE_DIR", str(DEFAULT_CACHE_DIR)),
        help="каталог кэша ответов",
    )
    parser.add_argument(
        "--receipts-dir",
        default=os.environ.get("MOSOBLEIRC_RECEIPTS_DIR", "data/receipts"),
        help="каталог кэша квитанций ЕПД (PDF)",
    )
    parser.add_argument("--no-cache", action="store_true", help="не использовать кэш ответов")
    parser.add_argument("--force", action="store_true", help="перезапросить даже закэшированные месяцы")


def add_months_options(parser: argparse.ArgumentParser) -> None:
    add_common_data_options(parser)
    parser.add_argument("--months", type=int, default=env_int("MOSOBLEIRC_MONTHS", 12), help="сколько месяцев (по умолчанию 12)")
    parser.add_argument(
        "--end-month",
        default=os.environ.get("MOSOBLEIRC_END_MONTH"),
        help="последний месяц в формате YYYY-MM (по умолчанию предыдущий месяц)",
    )
    parser.add_argument(
        "--anchor-day",
        type=int,
        default=env_int("MOSOBLEIRC_ANCHOR_DAY", 15),
        help="день месяца для запроса (по умолчанию 15)",
    )
    parser.add_argument(
        "--shift",
        type=int,
        default=env_int("MOSOBLEIRC_SHIFT", 1),
        help="сдвиг месяца запроса относительно месяца начисления (по умолчанию 1)",
    )


def add_report_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--value",
        choices=sorted(VALUE_FIELDS),
        default=os.environ.get("MOSOBLEIRC_VALUE", "charged"),
        help="показатель для таблицы: charged — начислено, total — итого (по умолчанию charged)",
    )
    parser.add_argument("--format", choices=("table", "csv", "json"), default="table", help="формат вывода")
    parser.add_argument("--out", help="файл для записи результата (по умолчанию stdout)")


def add_web_options(parser: argparse.ArgumentParser) -> None:
    add_months_options(parser)
    parser.add_argument(
        "--value",
        choices=sorted(VALUE_FIELDS),
        default=os.environ.get("MOSOBLEIRC_VALUE", "charged"),
        help="показатель по умолчанию (по умолчанию charged)",
    )
    parser.add_argument("--host", default=os.environ.get("MOSOBLEIRC_HOST", "0.0.0.0"), help="адрес веб-сервера")
    parser.add_argument("--port", type=int, default=env_int("MOSOBLEIRC_PORT", 8765), help="порт веб-сервера")


def prompt_code(factor: str) -> str:
    if not sys.stdin.isatty():
        raise MosOblEIRCError(f"Требуется код второго фактора ({factor}), но ввод недоступен")
    return input(f"Код подтверждения ({factor}): ").strip()


def make_client(args) -> MosOblEIRCClient:
    token = args.token
    token_path = Path(args.token_file).expanduser() if args.token_file else None

    if not token and token_path and not args.no_token_cache:
        token = load_token(token_path, args.phone)

    client = MosOblEIRCClient(
        phone=args.phone,
        password=args.password,
        token=token,
        prompt_code=prompt_code,
    )

    if not token and args.phone and args.password:
        token = client.login()
        if token_path and not args.no_token_cache:
            save_token(token_path, args.phone, token)

    return client


def resolve_end_month(args) -> str:
    if args.end_month:
        return args.end_month
    return previous_month()


def select_accounts(client: MosOblEIRCClient, filters: list[str] | None) -> list[dict]:
    accounts = client.accounts()
    if not accounts:
        raise MosOblEIRCError("В ЛКК не найдено ни одного лицевого счёта")

    if not filters:
        return accounts

    selected = []
    for account in accounts:
        haystack = f"{account['id']} {account['personal_account_id']} {account['name']}".lower()
        if any(needle.lower() in haystack for needle in filters):
            selected.append(account)

    if not selected:
        raise MosOblEIRCError("По фильтру --account ничего не найдено")
    return selected


def make_logger(quiet: bool):
    def log(account, month, requested_date, count, source):
        if quiet:
            return
        print(
            f"  {account['name']}: {month} (запрос {requested_date}, {source}, услуг: {count})",
            file=sys.stderr,
        )

    return log


def collect_for_args(args, client: MosOblEIRCClient, accounts: list[dict]):
    end_month = resolve_end_month(args)
    months = month_range(end_month, args.months)
    receipts_dir = None if args.no_cache else args.receipts_dir
    cache_dir = None if args.no_cache else args.cache_dir

    if not getattr(args, "quiet", False):
        print(
            f"Собираю начисления за {months[0]}..{months[-1]} по {len(accounts)} ЛС...",
            file=sys.stderr,
        )

    if not pdf_support_available():
        print(
            "Квитанции ЕПД не разобраны: установите зависимости — pip install -r requirements.txt (нужен pypdf)",
            file=sys.stderr,
        )
        rows = []
    else:
        rows = collect_receipt_charges(
            client,
            accounts,
            months,
            cache_dir=receipts_dir,
            force=args.force,
            log=lambda account, month, note: print(f"  {account['name']}: {month} {note}", file=sys.stderr),
        )
    if rows:
        return rows

    print("Квитанций ЕПД не нашлось — беру текущий расчёт из charge-details", file=sys.stderr)
    return collect_charges(
        client,
        accounts,
        months,
        anchor_day=args.anchor_day,
        shift=args.shift,
        cache_dir=cache_dir,
        force=args.force,
        log=make_logger(getattr(args, "quiet", False)),
    )


def write_output(text: str, path: str | None) -> None:
    if path:
        Path(path).write_text(text, encoding="utf-8-sig" if path.endswith(".csv") else "utf-8")
        print(f"Записано в {path}", file=sys.stderr)
    else:
        print(text)


def cmd_accounts(args) -> int:
    client = make_client(args)
    accounts = client.accounts()
    for account in accounts:
        print(
            f"id={account['id']}  ЛС={account['personal_account_id']}  {account['name']}"
        )
    return 0


def cmd_web(args) -> int:
    from .webapp import WebConfig, run_server

    config = WebConfig(
        phone=args.phone,
        password=args.password,
        token=args.token,
        token_file=None if args.no_token_cache else args.token_file,
        account_filter=list(args.account or []),
        months=args.months,
        shift=args.shift,
        anchor_day=args.anchor_day,
        value=args.value,
        end_month=args.end_month,
        cache_dir=None if args.no_cache else args.cache_dir,
        receipts_dir=None if args.no_cache else args.receipts_dir,
        host=args.host,
        port=args.port,
    )
    run_server(config)
    return 0


def cmd_collect(args) -> int:
    client = make_client(args)
    accounts = select_accounts(client, args.account)
    rows = collect_for_args(args, client, accounts)
    print(f"Загружено записей: {len(rows)}", file=sys.stderr)
    return 0


def cmd_turnover(args) -> int:
    client = make_client(args)
    accounts = select_accounts(client, args.account)
    end_month = resolve_end_month(args)
    months = month_range(end_month, args.months)

    def log(account, *info):
        print(f"  {account['name']}:", *info, file=sys.stderr)

    rows = collect_turnover(client, accounts, months, log=lambda a, y, p, c: log(a, y, f"стр. {p}", f"записей: {c}"))

    if not rows:
        print("Оборотной ведомости нет — ЛКК не отдаёт её для этого счёта.", file=sys.stderr)

    if args.format == "table":
        output = render_totals_table(rows)
    elif args.format == "csv":
        output = render_totals_csv(rows)
    else:
        output = render_totals_json(rows)

    write_output(output, args.out)
    return 0


def cmd_months(args) -> int:
    client = make_client(args)
    accounts = select_accounts(client, args.account)
    rows = collect_for_args(args, client, accounts)

    if not has_category_history(rows):
        print(
            "ЛКК отдаёт разбивку по услугам только за текущий период. "
            "Для истории по месяцам используйте: python -m mosobleirc turnover",
            file=sys.stderr,
        )

    if args.format == "table":
        output = render_table(rows, args.value)
    elif args.format == "csv":
        output = render_csv(rows)
    else:
        output = render_json(rows, args.value)

    write_output(output, args.out)
    return 0


def cmd_probe(args) -> int:
    client = make_client(args)
    accounts = select_accounts(client, args.account)
    cache_dir = None if args.no_cache else args.cache_dir

    end_month = previous_month()
    months = month_range(end_month, args.months)
    anchor_days = [int(day.strip()) for day in args.anchor_days.split(",") if day.strip()]
    shifts = [int(shift.strip()) for shift in args.shift.split(",") if shift.strip()]

    cache = ChargeCache(cache_dir)
    rows = []

    for account in accounts:
        personal_account_id = account["personal_account_id"]
        for month in months:
            for shift in shifts:
                request_month = month_add(month, shift)
                for day in anchor_days:
                    requested_date = anchor_date(request_month, day)
                    details = None if args.force else cache.load(personal_account_id, requested_date)
                    source = "cache"
                    if details is None:
                        details = client.charge_details(personal_account_id, requested_date)
                        cache.save(personal_account_id, requested_date, month, details)
                        source = "api"

                    charges = normalize_charges(
                        details,
                        month=month,
                        requested_date=requested_date,
                        account_id=account["id"],
                        account_name=account["name"],
                    )
                    periods = sorted({charge.month for charge in charges})
                    charged = sum(charge.charged for charge in charges)
                    total = sum(charge.total for charge in charges)
                    rows.append(
                        (
                            requested_date,
                            source,
                            len(charges),
                            format_amount(charged),
                            format_amount(total),
                            ",".join(periods) or "-",
                        )
                    )

    headers = ("Запрос", "Источник", "Услуг", "Начислено", "Итого", "Периоды в ответе")
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))

    def render(row):
        return "  ".join(str(cell).ljust(widths[index]) for index, cell in enumerate(row)).rstrip()

    print(render(headers))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for row in rows:
        print(render(row))
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MosOblEIRCError as error:
        if args.debug:
            raise
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Прервано", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
