import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mosobleirc import cli  # noqa: E402
from mosobleirc.stats import (  # noqa: E402
    HOUSING_GROUP,
    OTHER_GROUP,
    PARSER_VERSION,
    SHARED_GROUP,
    SUMMARY_SERVICE,
    UTILITY_GROUP,
    Charge,
    canonical_service_group,
    collect_receipt_charges,
    detect_receipt_format,
    drop_duplicate_summaries,
    find_supplier_receipt,
    month_from_filename,
    parse_receipt_pdf,
    parse_receipt_text,
    scan_suppliers,
    supplier_label,
)
from mosobleirc.store import Store  # noqa: E402
from mosobleirc.webapp import State, WebConfig  # noqa: E402

REAL_PDF = ROOT / "data/receipts/mosobleirc/2026-08.pdf"
REAL_PDF_JULY = ROOT / "data/receipts/mosobleirc/2026-07.pdf"
REAL_MES_PDF = ROOT / "data/receipts/mosenergosbyt/2025-06.pdf"
REAL_UK_PDF = ROOT / "data/receipts/upr/2026-03.pdf"
EXPECTED_SUM = 5752.16
EXPECTED_JULY_SUM = 5748.29
EXPECTED_MES_SUM = 627.50
EXPECTED_UK_SUM = 2721.68
UPR_EXPECTED = {
    "2025-08": 311.08,
    "2025-10": 2039.15,
    "2025-11": 3005.93,
    "2025-12": 3096.20,
    "2026-02": 6388.64,
    "2026-03": 2721.68,
    "2026-04": 2047.44,
    "2026-05": 1379.63,
    "2026-06": 649.83,
    "2026-07": 934.92,
    "2026-08": 872.76,
}


class TestHelpers(unittest.TestCase):
    def test_month_from_filename(self):
        self.assertEqual(month_from_filename("2026-08.pdf"), "2026-08")
        self.assertEqual(month_from_filename("invoice 2026-8 (2).pdf"), "2026-08")
        self.assertEqual(month_from_filename("2026-13.pdf"), None)
        self.assertEqual(month_from_filename("scan.pdf"), None)

    def test_supplier_label(self):
        self.assertEqual(supplier_label("mosobleirc"), "МосОблЕИРЦ")
        self.assertEqual(supplier_label("mosenergosbyt"), "mosenergosbyt")
        self.assertEqual(supplier_label(""), "")
        self.assertEqual(supplier_label(None), "")


class TestServiceGroups(unittest.TestCase):
    def test_canonical_service_group(self):
        cases = {
            "СОДЕРЖАНИЕ ЖИЛОГО ПОМЕЩЕНИЯ": HOUSING_GROUP,
            "ВЗНОС НА КАПИТАЛЬНЫЙ РЕМОНТ": HOUSING_GROUP,
            "ОХРАНА": HOUSING_GROUP,
            "УСЛУГИ КОНСЬЕРЖА": HOUSING_GROUP,
            "ДОМОФОН": HOUSING_GROUP,
            "ХОЛОДНОЕ В/С ОДН": SHARED_GROUP,
            "ВОДООТВЕДЕНИЕ ОДН": SHARED_GROUP,
            "ЭЛЕКТРОСНАБЖЕНИЕ ДЕНЬ ОДН": SHARED_GROUP,
            "КР на СОИ по воде": SHARED_GROUP,
            "ХОЛОДНОЕ В/С": UTILITY_GROUP,
            "ГОРЯЧЕЕ В/С (НОСИТЕЛЬ)": UTILITY_GROUP,
            "Подогрев воды для ГВС": UTILITY_GROUP,
            "ОБРАЩЕНИЕ С ТКО": UTILITY_GROUP,
            "Отопление": UTILITY_GROUP,
            "ДОБРОВОЛЬНОЕ СТРАХОВАНИЕ": OTHER_GROUP,
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(canonical_service_group(name), expected)

    def test_epd_rows_get_canonical_groups(self):
        pdf = ROOT / "data/receipts/mosobleirc/2025-12.pdf"
        rows = parse_receipt_pdf(pdf.read_bytes(), supplier="mosobleirc")
        by_service = {row.service: row for row in rows}
        self.assertEqual(by_service["ХОЛОДНОЕ В/С ОДН"].group, SHARED_GROUP)
        self.assertEqual(by_service["ВОДООТВЕДЕНИЕ ОДН"].group, SHARED_GROUP)
        self.assertEqual(by_service["ОХРАНА"].group, HOUSING_GROUP)
        self.assertEqual(by_service["УСЛУГИ КОНСЬЕРЖА"].group, HOUSING_GROUP)
        self.assertEqual(by_service["ДОБРОВОЛЬНОЕ СТРАХОВАНИЕ"].group, OTHER_GROUP)
        self.assertEqual(by_service["ХОЛОДНОЕ В/С"].group, UTILITY_GROUP)
        self.assertEqual(by_service["ВЗНОС НА КАПИТАЛЬНЫЙ РЕМОНТ"].group, HOUSING_GROUP)

    def test_web_payload_groups_use_new_sections(self):
        tmp = Path(tempfile.mkdtemp(prefix="groups-web-"))
        try:
            supplier = tmp / "mosobleirc"
            supplier.mkdir()
            shutil.copy(ROOT / "data/receipts/mosobleirc/2025-12.pdf", supplier / "2025-12.pdf")
            state = State(WebConfig(db_path=str(tmp / "db.sqlite"), receipts_dir=str(tmp), months=12))
            try:
                payload = state.data(months=12, value="charged", force=True)
                groups = payload["categories"]["groups"]
                self.assertIn(SHARED_GROUP, groups)
                self.assertIn("ХОЛОДНОЕ В/С ОДН", groups[SHARED_GROUP])
                self.assertIn("ОХРАНА", groups[HOUSING_GROUP])
                self.assertNotIn("ОХРАНА", groups.get(OTHER_GROUP, []))
                self.assertIn("ДОБРОВОЛЬНОЕ СТРАХОВАНИЕ", groups[OTHER_GROUP])
                order = payload["categories"]["order"]
                self.assertLess(order.index("СОДЕРЖАНИЕ ЖИЛОГО ПОМЕЩЕНИЯ"), order.index("ХОЛОДНОЕ В/С ОДН"))
                self.assertLess(order.index("ХОЛОДНОЕ В/С ОДН"), order.index("ХОЛОДНОЕ В/С"))
            finally:
                state.store.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="store-"))
        self.store = Store(self.tmp / "cache.sqlite")

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_receipt_charges_roundtrip_and_invalidation(self):
        pdf = self.tmp / "r.pdf"
        shutil.copy(REAL_PDF, pdf)
        charges = [{"month": "2026-08", "service": "X", "group": "G", "unit": "м3",
                    "volume": 1.5, "tariff": 2.0, "charged": 3.0,
                    "readingStart": 100.0, "readingEnd": 150.0}]
        self.store.save_receipt_charges(
            pdf, supplier="s", account_id="", month="2026-08", charges=charges
        )
        self.assertEqual(self.store.load_receipt_charges(pdf), charges)
        self.assertEqual(self.store.latest_month(), "2026-08")
        os.utime(pdf, (1, 1))
        self.assertIsNone(self.store.load_receipt_charges(pdf))

    def test_disabled_store(self):
        store = Store(None)
        self.assertFalse(store.enabled)
        self.assertIsNone(store.load_receipt_charges(self.tmp / "none.pdf"))
        self.assertIsNone(store.latest_month())
        store.save_receipt_charges(self.tmp / "none.pdf", supplier="s", account_id="",
                                   month="2026-01", charges=[])
        store.mark_receipt_skipped(self.tmp / "none.pdf", supplier="s", note="x")
        self.assertEqual(store.load_all_receipt_charges(), [])
        self.assertEqual(store.load_warnings(), [])
        self.assertEqual(store.counts(), {})


class TestReceiptPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pipeline-"))
        self.receipts = self.tmp / "receipts"
        self.supplier_dir = self.receipts / "mosenergosbyt"
        self.supplier_dir.mkdir(parents=True)
        shutil.copy(REAL_PDF, self.supplier_dir / "2026-08.pdf")
        self.store = Store(self.tmp / "cache.sqlite")

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_and_cache(self):
        report = {}
        first = collect_receipt_charges(self.receipts, ["2026-08"], store=self.store, report=report)
        self.assertEqual(len(first), 11)
        self.assertAlmostEqual(sum(row.charged for row in first), EXPECTED_SUM, places=2)
        self.assertTrue(all(row.supplier == "mosenergosbyt" for row in first))
        self.assertEqual(report.get("parsed"), 1)
        self.assertFalse(list(self.receipts.rglob("*.json")))

        report = {}
        second = collect_receipt_charges(self.receipts, ["2026-08"], store=self.store, report=report)
        self.assertEqual(len(second), 11)
        self.assertEqual(report.get("cached"), 1)
        self.assertNotIn("parsed", report)

    def test_force_reparses(self):
        collect_receipt_charges(self.receipts, ["2026-08"], store=self.store)
        report = {}
        collect_receipt_charges(self.receipts, ["2026-08"], store=self.store, force=True, report=report)
        self.assertEqual(report.get("parsed"), 1)

    def test_month_filter(self):
        rows = collect_receipt_charges(self.receipts, ["2026-07"], store=self.store)
        self.assertEqual(rows, [])

    def test_works_without_store(self):
        rows = collect_receipt_charges(self.receipts, ["2026-08"], store=None)
        self.assertEqual(len(rows), 11)

    def test_non_pdf_and_unparseable_pdf_are_warned(self):
        (self.receipts / "bad").mkdir()
        (self.receipts / "bad" / "2026-07.pdf").write_bytes(b"not a pdf at all")
        (self.receipts / "broken").mkdir()
        (self.receipts / "broken" / "2026-06.pdf").write_bytes(b"%PDF-1.4\nbroken content")
        report = {}
        rows = collect_receipt_charges(self.receipts, ["2026-06", "2026-07", "2026-08"],
                                       store=self.store, report=report)
        self.assertEqual(len(rows), 11)
        warnings = report.get("warnings") or []
        self.assertEqual(len(warnings), 2)
        self.assertTrue(any("не PDF" in warning for warning in warnings))
        self.assertTrue(any("2026-06.pdf" in warning for warning in warnings))

    def test_two_files_same_month_are_summed_with_warning(self):
        shutil.copy(REAL_PDF, self.supplier_dir / "2026-08-extra.pdf")
        report = {}
        rows = collect_receipt_charges(self.receipts, ["2026-08"], store=self.store, report=report)
        self.assertEqual(len(rows), 22)
        warnings = report.get("warnings") or []
        self.assertTrue(any("найдено файлов: 2" in warning for warning in warnings))

    def test_find_supplier_receipt(self):
        found = find_supplier_receipt(self.receipts, "2026-08")
        self.assertEqual(found.name, "2026-08.pdf")
        self.assertIsNone(find_supplier_receipt(self.receipts, "2026-01"))

    def test_find_supplier_receipt_by_supplier(self):
        found = find_supplier_receipt(self.receipts, "2026-08", supplier="mosenergosbyt")
        self.assertEqual(found.name, "2026-08.pdf")
        self.assertIsNone(
            find_supplier_receipt(self.receipts, "2026-08", supplier="mosobleirc")
        )

    def test_scan_suppliers(self):
        scanned = scan_suppliers(self.receipts)
        self.assertEqual([item["name"] for item in scanned], ["mosenergosbyt"])
        self.assertEqual(scanned[0]["files"], 1)
        self.assertEqual(scanned[0]["months"], ["2026-08"])
        self.assertEqual(scanned[0]["label"], "mosenergosbyt")


class TestMesParser(unittest.TestCase):
    def test_parse_mes_pdf(self):
        rows = parse_receipt_pdf(
            REAL_MES_PDF.read_bytes(), requested_month=None, supplier="mosenergosbyt"
        )
        self.assertEqual(
            [row.service for row in rows],
            ["ЭЛЕКТРОЭНЕРГИЯ (Т1) ДЕНЬ", "ЭЛЕКТРОЭНЕРГИЯ (Т2) НОЧЬ"],
        )
        self.assertTrue(all(row.month == "2025-06" for row in rows))
        self.assertAlmostEqual(sum(row.charged for row in rows), EXPECTED_MES_SUM, places=2)
        self.assertAlmostEqual(sum(row.volume for row in rows), 110.0, places=2)

        day = rows[0]
        self.assertEqual(day.reading_start, 2520)
        self.assertEqual(day.reading_end, 2600)
        self.assertEqual(day.tariff, 6.79)
        self.assertEqual(day.unit, "кВт∙ч")
        self.assertEqual(day.group, "Коммунальные услуги")
        self.assertEqual(day.supplier, "mosenergosbyt")

    def test_dispatch_by_zone_lines(self):
        text = "СЧЁТ ЗА ЭЛЕКТРОЭНЕРГИЮ / август 2026 г.\n(Т1) день Д1   4006   4006   8,24   0,00\n"
        self.assertEqual(parse_receipt_text(text, supplier="mosenergosbyt"), [])

        text = "(Т1) день Д1   3895   4006   111   8,24   914,64"
        rows = parse_receipt_text(text, requested_month="2026-05", supplier="mosenergosbyt")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].charged, 914.64)
        self.assertEqual(rows[0].volume, 111)
        self.assertEqual(rows[0].month, "2026-05")

    def test_zero_mes_month_has_no_rows(self):
        rows = parse_receipt_pdf(REAL_MES_PDF.read_bytes(), supplier="mosenergosbyt")
        self.assertTrue(rows)

        zero = parse_receipt_pdf(
            (ROOT / "data/receipts/mosenergosbyt/2026-08.pdf").read_bytes(),
            supplier="mosenergosbyt",
        )
        self.assertEqual(zero, [])

    def test_uk_receipts_parse_to_stated_totals(self):
        for name, expected in UPR_EXPECTED.items():
            pdf = ROOT / f"data/receipts/upr/{name}.pdf"
            with self.subTest(name=name):
                rows = parse_receipt_pdf(pdf.read_bytes(), supplier="upr")
                self.assertTrue(rows, f"{name}: нет строк")
                self.assertAlmostEqual(sum(row.charged for row in rows), expected, places=2)
                self.assertTrue(all(row.month == name for row in rows))
                self.assertTrue(all(row.volume is not None for row in rows))
                self.assertTrue(all(row.service != "Водомер ГВ" for row in rows))

    def test_uk_receipt_rows(self):
        rows = parse_receipt_pdf(REAL_UK_PDF.read_bytes(), supplier="upr")
        by_service = {row.service: row for row in rows}
        self.assertAlmostEqual(by_service["Отопление"].charged, 2157.94, places=2)
        self.assertAlmostEqual(by_service["Отопление"].volume, 0.63742, places=5)
        self.assertAlmostEqual(by_service["Отопление"].tariff, 3385.43, places=2)
        self.assertEqual(by_service["Отопление"].unit, "Гк")
        self.assertEqual(by_service["Отопление"].group, "Коммунальные услуги")

    def test_uk_zero_volume_row_skipped(self):
        pdf = ROOT / "data/receipts/upr/2026-06.pdf"
        rows = parse_receipt_pdf(pdf.read_bytes(), supplier="upr")
        self.assertEqual([row.service for row in rows], ["Подогрев воды для ГВС"])
        self.assertAlmostEqual(rows[0].charged, 649.83, places=2)

    def test_uk_summary_fallback(self):
        text = (
            "ЖИЛИЩНО-КОММУНАЛЬНЫЕ И ИНЫЕ УСЛУГИ / расшифровка счета\n"
            "К оплате за Март 2026                              2 721.68\n"
        )
        self.assertEqual(detect_receipt_format(text), "uk")
        rows = parse_receipt_text(text, supplier="teplo")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].month, "2026-03")
        self.assertEqual(rows[0].service, SUMMARY_SERVICE)
        self.assertAlmostEqual(rows[0].charged, 2721.68)

    def test_drop_duplicate_summaries(self):
        detailed = Charge(month="2026-03", service="Отопление", charged=2157.94)
        detailed2 = Charge(month="2026-03", service="Подогрев воды для ГВС", charged=563.74)
        summary = Charge(month="2026-03", service=SUMMARY_SERVICE, charged=2721.68)
        other = Charge(month="2026-04", service=SUMMARY_SERVICE, charged=2047.44)
        kept, dropped = drop_duplicate_summaries([detailed, detailed2, summary, other])
        self.assertEqual(dropped, 1)
        self.assertNotIn(summary, kept)
        self.assertIn(other, kept)

    def test_detect_unknown_format(self):
        self.assertEqual(detect_receipt_format("случайный текст без таблиц"), "")

    def test_zero_mes_month_cached_without_warning(self):
        tmp = Path(tempfile.mkdtemp(prefix="mes-zero-"))
        try:
            supplier = tmp / "mosenergosbyt"
            supplier.mkdir()
            zero_pdf = supplier / "2026-08.pdf"
            shutil.copy(ROOT / "data/receipts/mosenergosbyt/2026-08.pdf", zero_pdf)
            store = Store(tmp / "db.sqlite")
            try:
                report = {}
                rows = collect_receipt_charges(tmp, None, store=store, report=report)
                self.assertEqual(rows, [])
                self.assertNotIn("warnings", report)
                self.assertEqual(
                    store.load_receipt_charges(zero_pdf, parser_version=PARSER_VERSION), []
                )
                self.assertEqual(store.load_warnings(), [])

                report = {}
                collect_receipt_charges(tmp, None, store=store, report=report)
                self.assertEqual(report.get("cached"), 1)
                self.assertNotIn("parsed", report)
            finally:
                store.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_parser_version_change_reparses(self):
        tmp = Path(tempfile.mkdtemp(prefix="parser-version-"))
        try:
            supplier = tmp / "mosenergosbyt"
            supplier.mkdir()
            pdf = supplier / "2025-06.pdf"
            shutil.copy(REAL_MES_PDF, pdf)
            store = Store(tmp / "db.sqlite")
            try:
                store.mark_receipt_skipped(pdf, supplier="mosenergosbyt", note="old", parser_version=1)
                self.assertIsNone(store.load_receipt_charges(pdf, parser_version=2))
                self.assertEqual(store.load_receipt_charges(pdf, parser_version=1), [])
            finally:
                store.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestWebFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="receipts-web-"))
        self.receipts = self.tmp / "receipts"
        self.receipts.mkdir()
        for name in ("mosobleirc", "mosenergosbyt"):
            supplier = self.receipts / name
            supplier.mkdir()
            shutil.copy(REAL_PDF, supplier / "2026-08.pdf")
        shutil.copy(REAL_PDF_JULY, self.receipts / "mosobleirc" / "2026-07.pdf")
        self.db = self.tmp / "web.sqlite"
        self.state = State(WebConfig(db_path=str(self.db), receipts_dir=str(self.receipts), months=1))

    def tearDown(self):
        self.state.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_payload_from_folders(self):
        payload = self.state.data(months=1, value="charged", force=True)
        categories = payload["categories"]
        self.assertEqual(categories["source"], "receipts")
        self.assertIsNone(categories["error"])
        self.assertIsNone(categories["warning"])
        self.assertEqual(categories["months"], ["2026-08"])
        self.assertAlmostEqual(categories["grandTotal"], EXPECTED_SUM * 2, places=2)
        self.assertEqual(categories["suppliersList"], ["mosenergosbyt", "МосОблЕИРЦ"])
        self.assertEqual(
            sorted(categories["suppliers"]["ОБРАЩЕНИЕ С ТКО"]), ["mosenergosbyt", "МосОблЕИРЦ"]
        )
        self.assertEqual(
            categories["supplierKeys"]["ОБРАЩЕНИЕ С ТКО"], ["mosenergosbyt", "mosobleirc"]
        )

    def test_month_window_ends_at_latest_receipt(self):
        payload = self.state.data(months=2, value="charged", force=True)
        self.assertEqual(payload["categories"]["months"], ["2026-07", "2026-08"])

    def test_explicit_end_month(self):
        state = State(WebConfig(
            db_path=str(self.db), receipts_dir=str(self.receipts), months=1, end_month="2026-07"
        ))
        try:
            payload = state.data(months=1, value="charged", force=True)
            self.assertEqual(payload["categories"]["months"], ["2026-07"])
            self.assertAlmostEqual(payload["categories"]["grandTotal"], EXPECTED_JULY_SUM, places=2)
        finally:
            state.store.close()

    def test_second_call_uses_server_cache(self):
        self.state.data(months=1, value="charged", force=True)
        payload = self.state.data(months=1, value="charged")
        self.assertTrue(payload["fetch"]["cacheHit"])

    def test_page_load_reads_db_only(self):
        empty = self.state.data(months=6, value="charged")
        self.assertEqual(empty["categories"]["months"], [])
        parsed = self.state.data(months=6, value="charged", force=True)
        self.assertEqual(parsed["categories"]["months"], ["2026-07", "2026-08"])
        self.assertEqual(parsed["fetch"]["source"], "folders")

        fresh = State(WebConfig(db_path=str(self.db), receipts_dir=str(self.receipts), months=6))
        try:
            loaded = fresh.data(months=6, value="charged")
            self.assertEqual(loaded["fetch"]["source"], "db")
            self.assertEqual(loaded["categories"]["months"], ["2026-07", "2026-08"])
        finally:
            fresh.store.close()

    def test_new_pdf_requires_refresh(self):
        self.state.data(months=6, value="charged", force=True)
        extra = self.receipts / "extra"
        extra.mkdir()
        shutil.copy(REAL_PDF, extra / "2026-08.pdf")

        fresh = State(WebConfig(db_path=str(self.db), receipts_dir=str(self.receipts), months=6))
        try:
            without = fresh.data(months=6, value="charged")
            self.assertNotIn("extra", without["categories"]["suppliersList"])
            with_refresh = fresh.data(months=6, value="charged", force=True)
            self.assertIn("extra", with_refresh["categories"]["suppliersList"])
        finally:
            fresh.store.close()

        again = State(WebConfig(db_path=str(self.db), receipts_dir=str(self.receipts), months=6))
        try:
            self.assertIn("extra", again.data(months=6, value="charged")["categories"]["suppliersList"])
        finally:
            again.store.close()

    def test_refresh_prunes_deleted_pdf(self):
        self.state.data(months=6, value="charged", force=True)
        (self.receipts / "mosenergosbyt" / "2026-08.pdf").unlink()
        payload = self.state.data(months=6, value="charged", force=True)
        self.assertEqual(payload["categories"]["suppliersList"], ["МосОблЕИРЦ"])

    def test_mes_readings_in_payload(self):
        tmp = Path(tempfile.mkdtemp(prefix="mes-web-"))
        try:
            supplier = tmp / "mosenergosbyt"
            supplier.mkdir()
            shutil.copy(REAL_MES_PDF, supplier / "2025-06.pdf")
            state = State(WebConfig(db_path=str(tmp / "db.sqlite"), receipts_dir=str(tmp), months=6))
            try:
                payload = state.data(months=6, value="volume", force=True)
                categories = payload["categories"]
                self.assertEqual(categories["months"], ["2025-06"])
                self.assertEqual(
                    categories["readings"]["ЭЛЕКТРОЭНЕРГИЯ (Т1) ДЕНЬ"]["2025-06"],
                    {"start": 2520.0, "end": 2600.0},
                )
                self.assertAlmostEqual(categories["grandTotal"], 110.0, places=2)
                self.assertAlmostEqual(
                    categories["charged"]["ЭЛЕКТРОЭНЕРГИЯ (Т2) НОЧЬ"]["2025-06"], 84.30, places=2
                )
            finally:
                state.store.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_mes_readings_survive_db_roundtrip(self):
        tmp = Path(tempfile.mkdtemp(prefix="mes-db-"))
        try:
            supplier = tmp / "mosenergosbyt"
            supplier.mkdir()
            shutil.copy(REAL_MES_PDF, supplier / "2025-06.pdf")
            db = tmp / "db.sqlite"
            state = State(WebConfig(db_path=str(db), receipts_dir=str(tmp), months=6))
            try:
                state.data(months=6, value="volume", force=True)
            finally:
                state.store.close()
            fresh = State(WebConfig(db_path=str(db), receipts_dir=str(tmp), months=6))
            try:
                categories = fresh.data(months=6, value="volume")["categories"]
                self.assertEqual(
                    categories["readings"]["ЭЛЕКТРОЭНЕРГИЯ (Т2) НОЧЬ"]["2025-06"],
                    {"start": 1038.0, "end": 1068.0},
                )
            finally:
                fresh.store.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_warning_persists_without_refresh(self):
        (self.receipts / "bad").mkdir()
        broken = self.receipts / "bad" / "2026-09.pdf"
        broken.write_bytes(b"not a pdf")
        self.state.data(months=6, value="charged", force=True)

        fresh = State(WebConfig(db_path=str(self.db), receipts_dir=str(self.receipts), months=6))
        try:
            payload = fresh.data(months=6, value="charged")
            self.assertIn("bad", payload["categories"]["warning"])
            self.assertEqual(payload["fetch"]["source"], "db")
        finally:
            fresh.store.close()

        broken.unlink()
        payload = self.state.data(months=6, value="charged", force=True)
        self.assertIsNone(payload["categories"]["warning"])

    def test_broken_pdf_drops_stale_db_rows(self):
        self.state.data(months=6, value="charged", force=True)
        target = self.receipts / "mosenergosbyt" / "2026-08.pdf"
        target.write_bytes(b"broken")
        payload = self.state.data(months=6, value="charged", force=True)
        self.assertEqual(payload["categories"]["suppliersList"], ["МосОблЕИРЦ"])

    def test_unparsed_pdf_shows_warning(self):
        (self.receipts / "bad").mkdir()
        (self.receipts / "bad" / "2026-09.pdf").write_bytes(b"not a pdf")
        payload = self.state.data(months=2, value="charged", force=True)
        warning = payload["categories"]["warning"]
        self.assertIsNotNone(warning)
        self.assertIn("bad", warning)

    def test_receipt_pdf_from_folders(self):
        data = self.state.receipt_pdf("2026-08")
        self.assertTrue(data.startswith(b"%PDF"))

    def test_receipt_pdf_by_supplier(self):
        upr_dir = self.receipts / "upr"
        upr_dir.mkdir()
        upr_pdf = upr_dir / "2026-08.pdf"
        shutil.copy(REAL_UK_PDF, upr_pdf)
        self.assertEqual(self.state.receipt_pdf("2026-08", supplier="upr"), upr_pdf.read_bytes())
        self.assertNotEqual(self.state.receipt_pdf("2026-08", supplier="upr"),
                            self.state.receipt_pdf("2026-08", supplier="mosobleirc"))

    def test_receipt_pdf_falls_back_to_any_supplier(self):
        data = self.state.receipt_pdf("2026-08", supplier="unknown")
        self.assertTrue(data.startswith(b"%PDF"))

    def test_receipt_pdf_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            self.state.receipt_pdf("2026-01")

    def test_state_creates_receipts_dir_only(self):
        self.assertTrue(self.receipts.is_dir())
        self.assertFalse(self.db.exists())

    def test_startup_does_not_parse_or_touch_folders(self):
        sidecar = self.receipts / "mosenergosbyt" / "2026-08.json"
        sidecar.write_text(json.dumps({"charges": []}), encoding="utf-8")
        fresh_db = self.tmp / "fresh.sqlite"
        state = State(WebConfig(db_path=str(fresh_db), receipts_dir=str(self.receipts), months=6))
        try:
            self.assertFalse(fresh_db.exists())
            payload = state.data(months=6, value="charged")
            self.assertEqual(payload["categories"]["months"], [])
            self.assertEqual(state.store.counts()["receipts"], 0)
            self.assertEqual(state.store.load_all_receipt_charges(), [])
            self.assertTrue(sidecar.exists())
        finally:
            state.store.close()


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="receipts-cli-"))
        self.receipts = self.tmp / "receipts"
        supplier = self.receipts / "mosenergosbyt"
        supplier.mkdir(parents=True)
        shutil.copy(REAL_PDF, supplier / "2026-08.pdf")
        self.db = self.tmp / "cache.sqlite"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_suppliers_command(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["suppliers", "--receipts-dir", str(self.receipts)])
        self.assertEqual(code, 0)
        self.assertIn("mosenergosbyt", out.getvalue())
        self.assertIn("2026-08", out.getvalue())

    def test_months_json(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([
                "months", "--receipts-dir", str(self.receipts), "--db", str(self.db),
                "--months", "1", "--value", "charged", "--format", "json",
            ])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["months"], ["2026-08"])
        self.assertAlmostEqual(payload["grandTotal"], EXPECTED_SUM, places=2)
        self.assertEqual(payload["charges"][0]["supplier"], "mosenergosbyt")

    def test_months_csv(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([
                "months", "--receipts-dir", str(self.receipts), "--db", str(self.db),
                "--months", "1", "--format", "csv",
            ])
        self.assertEqual(code, 0)
        first_line = out.getvalue().splitlines()[0]
        self.assertEqual(
            first_line,
            "month;supplier;service;unit;volume;tariff;charged;reading_start;reading_end",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
