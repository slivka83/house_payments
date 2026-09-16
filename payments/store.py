from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS receipt_files (
    path TEXT PRIMARY KEY,
    pdf_mtime_ns INTEGER NOT NULL,
    pdf_size INTEGER NOT NULL,
    supplier TEXT NOT NULL DEFAULT '',
    account_id TEXT NOT NULL DEFAULT '',
    month TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ok',
    note TEXT NOT NULL DEFAULT '',
    parser_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS receipt_charges (
    path TEXT NOT NULL,
    position INTEGER NOT NULL,
    month TEXT NOT NULL DEFAULT '',
    service TEXT NOT NULL DEFAULT '',
    service_group TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT '',
    volume REAL,
    tariff REAL,
    charged REAL NOT NULL DEFAULT 0,
    reading_start REAL,
    reading_end REAL,
    PRIMARY KEY (path, position)
);
CREATE INDEX IF NOT EXISTS idx_receipt_charges_month ON receipt_charges(month);
CREATE INDEX IF NOT EXISTS idx_receipt_charges_service ON receipt_charges(service);
"""


class Store:
    """SQLite-БД с результатами разбора платёжек."""

    def __init__(self, path: Path | str | None):
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def _connection(self) -> sqlite3.Connection | None:
        if self.path is None:
            return None
        with self._lock:
            if self._conn is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.executescript(SCHEMA)
                self._ensure_columns(conn)
                self._conn = conn
            return self._conn

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection) -> None:
        file_columns = {row[1] for row in conn.execute("PRAGMA table_info(receipt_files)")}
        if "status" not in file_columns:
            conn.execute("ALTER TABLE receipt_files ADD COLUMN status TEXT NOT NULL DEFAULT 'ok'")
        if "note" not in file_columns:
            conn.execute("ALTER TABLE receipt_files ADD COLUMN note TEXT NOT NULL DEFAULT ''")
        if "parser_version" not in file_columns:
            conn.execute(
                "ALTER TABLE receipt_files ADD COLUMN parser_version INTEGER NOT NULL DEFAULT 0"
            )
        charge_columns = {row[1] for row in conn.execute("PRAGMA table_info(receipt_charges)")}
        if "reading_start" not in charge_columns:
            conn.execute("ALTER TABLE receipt_charges ADD COLUMN reading_start REAL")
        if "reading_end" not in charge_columns:
            conn.execute("ALTER TABLE receipt_charges ADD COLUMN reading_end REAL")

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def latest_month(self) -> str | None:
        conn = self._connection()
        if conn is None:
            return None
        with self._lock:
            row = conn.execute("SELECT MAX(month) FROM receipt_files").fetchone()
        return row[0] if row and row[0] else None

    def load_receipt_charges(
        self, pdf_path: Path | str, *, parser_version: int = 0
    ) -> list[dict] | None:
        """Строки разбора, если PDF не менялся и разобран текущей версией парсера.

        [] — файл уже пробовали и признали неразобранным; None — нужно разобрать.
        """
        conn = self._connection()
        if conn is None:
            return None
        pdf_path = Path(pdf_path)
        try:
            stat = pdf_path.stat()
        except OSError:
            return None
        key = str(pdf_path)
        with self._lock:
            head = conn.execute(
                "SELECT pdf_mtime_ns, pdf_size, status, parser_version "
                "FROM receipt_files WHERE path = ?",
                (key,),
            ).fetchone()
            if not head or head[0] != stat.st_mtime_ns or head[1] != stat.st_size:
                return None
            if head[3] != parser_version:
                return None
            if head[2] != "ok":
                return []
            rows = conn.execute(
                "SELECT month, service, service_group, unit, volume, tariff, charged, "
                "reading_start, reading_end "
                "FROM receipt_charges WHERE path = ? ORDER BY position",
                (key,),
            ).fetchall()
        return [
            {
                "month": row[0],
                "service": row[1],
                "group": row[2],
                "unit": row[3],
                "volume": row[4],
                "tariff": row[5],
                "charged": row[6],
                "readingStart": row[7],
                "readingEnd": row[8],
            }
            for row in rows
        ]

    def save_receipt_charges(
        self,
        pdf_path: Path | str,
        *,
        supplier: str,
        account_id: str,
        month: str,
        charges: list[dict],
        parser_version: int = 0,
    ) -> None:
        conn = self._connection()
        if conn is None:
            return
        pdf_path = Path(pdf_path)
        try:
            stat = pdf_path.stat()
        except OSError:
            return
        key = str(pdf_path)
        default_month = month or (str(charges[0].get("month") or "") if charges else "")
        with self._lock, conn:
            conn.execute("DELETE FROM receipt_charges WHERE path = ?", (key,))
            conn.execute("DELETE FROM receipt_files WHERE path = ?", (key,))
            conn.execute(
                "INSERT INTO receipt_files"
                "(path, pdf_mtime_ns, pdf_size, supplier, account_id, month, status, note, "
                "parser_version) VALUES(?, ?, ?, ?, ?, ?, 'ok', '', ?)",
                (
                    key,
                    stat.st_mtime_ns,
                    stat.st_size,
                    supplier or "",
                    str(account_id or ""),
                    default_month,
                    parser_version,
                ),
            )
            conn.executemany(
                "INSERT INTO receipt_charges"
                "(path, position, month, service, service_group, unit, volume, tariff, charged, "
                "reading_start, reading_end) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        key,
                        index,
                        str(item.get("month") or default_month),
                        str(item.get("service") or ""),
                        str(item.get("group") or ""),
                        str(item.get("unit") or ""),
                        item.get("volume"),
                        item.get("tariff"),
                        float(item.get("charged") or 0.0),
                        item.get("readingStart"),
                        item.get("readingEnd"),
                    )
                    for index, item in enumerate(charges)
                ],
            )

    def load_all_receipt_charges(self) -> list[dict]:
        """Все разобранные строки из БД (без обращения к PDF)."""
        conn = self._connection()
        if conn is None:
            return []
        with self._lock:
            rows = conn.execute(
                "SELECT f.supplier, c.month, c.service, c.service_group, c.unit, "
                "c.volume, c.tariff, c.charged, c.reading_start, c.reading_end "
                "FROM receipt_charges c JOIN receipt_files f ON f.path = c.path "
                "ORDER BY c.path, c.position"
            ).fetchall()
        return [
            {
                "supplier": row[0],
                "month": row[1],
                "service": row[2],
                "group": row[3],
                "unit": row[4],
                "volume": row[5],
                "tariff": row[6],
                "charged": row[7],
                "readingStart": row[8],
                "readingEnd": row[9],
            }
            for row in rows
        ]

    def delete_receipt_charges(self, pdf_path: Path | str) -> None:
        conn = self._connection()
        if conn is None:
            return
        key = str(pdf_path)
        with self._lock, conn:
            conn.execute("DELETE FROM receipt_charges WHERE path = ?", (key,))
            conn.execute("DELETE FROM receipt_files WHERE path = ?", (key,))

    def mark_receipt_skipped(
        self, pdf_path: Path | str, *, supplier: str, note: str, parser_version: int = 0
    ) -> None:
        """Запоминает, что PDF не разобран, чтобы предупреждение было видно и после перезагрузки."""
        conn = self._connection()
        if conn is None:
            return
        pdf_path = Path(pdf_path)
        try:
            stat = pdf_path.stat()
        except OSError:
            return
        key = str(pdf_path)
        with self._lock, conn:
            conn.execute("DELETE FROM receipt_charges WHERE path = ?", (key,))
            conn.execute("DELETE FROM receipt_files WHERE path = ?", (key,))
            conn.execute(
                "INSERT INTO receipt_files"
                "(path, pdf_mtime_ns, pdf_size, supplier, account_id, month, status, note, "
                "parser_version) VALUES(?, ?, ?, ?, '', '', 'skipped', ?, ?)",
                (key, stat.st_mtime_ns, stat.st_size, supplier or "", note, parser_version),
            )

    def load_warnings(self) -> list[dict]:
        conn = self._connection()
        if conn is None:
            return []
        with self._lock:
            rows = conn.execute(
                "SELECT supplier, path, note FROM receipt_files "
                "WHERE status = 'skipped' ORDER BY path"
            ).fetchall()
        return [{"supplier": row[0], "path": row[1], "note": row[2]} for row in rows]

    def prune_receipts(self, keep_paths: set[str]) -> int:
        """Удаляет из БД разборы PDF, которых больше нет на диске."""
        conn = self._connection()
        if conn is None:
            return 0
        with self._lock:
            existing = [row[0] for row in conn.execute("SELECT path FROM receipt_files")]
            stale = [path for path in existing if path not in keep_paths]
            with conn:
                for path in stale:
                    conn.execute("DELETE FROM receipt_charges WHERE path = ?", (path,))
                    conn.execute("DELETE FROM receipt_files WHERE path = ?", (path,))
        return len(stale)

    def counts(self) -> dict:
        conn = self._connection()
        if conn is None:
            return {}
        with self._lock:
            receipts = conn.execute("SELECT COUNT(*) FROM receipt_files").fetchone()[0]
            charges = conn.execute("SELECT COUNT(*) FROM receipt_charges").fetchone()[0]
        return {"receipts": receipts, "charges": charges}
