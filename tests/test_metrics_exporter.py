import tempfile
import unittest
from pathlib import Path

import polars as pl

from src.metrics_exporter import PrometheusMetricsExporter


class TestPrometheusMetricsExporter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.prom_path = Path(self.temp_dir.name) / "email_metrics.prom"
        self.store_path = Path(self.temp_dir.name) / "cumulative_metrics.json"
        self.exporter = PrometheusMetricsExporter(
            output_prom_path=self.prom_path,
            store_path=self.store_path,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def frame(city: str, count: int = 1) -> pl.DataFrame:
        return pl.DataFrame({
            "network_service_id": [f"NS-{index}" for index in range(count)],
            "modem_seri_no": [f"SERIAL-{index}" for index in range(count)],
            "start_date": ["2026-08-05 10:00:00"] * count,
            "request_ana_basligi": ["Teknik Şikayet"] * count,
            "request_alt_basligi1": ["Bağlantı"] * count,
            "il": [city] * count,
            "altyapi": ["Fiber"] * count,
            "modem_marka": ["ZTE"] * count,
            "hiz": ["100 Mbps"] * count,
        })

    def test_accumulates_all_reports(self):
        self.exporter.export_dataframe(self.frame("İstanbul", 2), {"original_filename": "ilk.xlsx"})
        first = self.prom_path.read_text(encoding="utf-8")
        self.assertIn("email_reports_processed_total 1", first)
        self.assertIn("email_rows_processed_total 2", first)
        self.assertIn('il_total{il="İstanbul"} 2', first)
        self.assertIn("email_unique_network_service_ids_total 2", first)
        self.assertNotIn("modem_seri_no", first)

        self.exporter.export_dataframe(self.frame("Ankara"), {"original_filename": "son.xlsx"})
        cumulative = self.prom_path.read_text(encoding="utf-8")
        self.assertIn("email_reports_processed_total 2", cumulative)
        self.assertIn("email_rows_processed_total 3", cumulative)
        self.assertIn('il_total{il="Ankara"} 1', cumulative)
        self.assertIn('il_total{il="İstanbul"} 2', cumulative)

        restarted = PrometheusMetricsExporter(
            output_prom_path=self.prom_path,
            store_path=self.store_path,
        )
        restarted.export_dataframe(self.frame("İzmir"), {"original_filename": "ucuncu.xlsx"})
        persisted = self.prom_path.read_text(encoding="utf-8")
        self.assertIn("email_reports_processed_total 3", persisted)
        self.assertIn("email_rows_processed_total 4", persisted)
        self.assertIn('il_total{il="İstanbul"} 2', persisted)

    def test_combines_chunks_as_one_report(self):
        self.exporter.begin_report({"original_filename": "parcali.xlsx"})
        self.exporter.add_dataframe(self.frame("İzmir", 2))
        self.exporter.add_dataframe(self.frame("İzmir", 3))
        self.exporter.finish_report()
        content = self.prom_path.read_text(encoding="utf-8")
        self.assertIn("email_reports_processed_total 1", content)
        self.assertIn("email_rows_processed_total 5", content)
        self.assertIn('il_total{il="İzmir"} 5', content)


    def test_exports_network_service_count_and_complaint_details(self):
        frame = pl.DataFrame({
            "network_service_id": ["NS-1", "NS-1", "NS-2"],
            "start_date": ["2026-08-05", "2026-08-06", "2026-08-06"],
            "il": ["Ankara", "Ankara", "Izmir"],
            "request_ana_basligi": ["Teknik", "Fatura", "Teknik"],
        })

        self.exporter.export_dataframe(frame, {"original_filename": "network.xlsx"})

        content = self.prom_path.read_text(encoding="utf-8")
        self.assertIn("email_unique_network_service_ids_total 2", content)
        self.assertEqual(content.count("complaint_network_service_info{"), 3)
        self.assertIn('network_service_id="NS-1",start_date="2026-08-05"', content)
        self.assertIn('network_service_id="NS-1",start_date="2026-08-06"', content)


if __name__ == "__main__":
    unittest.main()
