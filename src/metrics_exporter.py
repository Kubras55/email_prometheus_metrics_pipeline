import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional

import polars as pl

from src.config import settings
from src.logger import logger


class PrometheusMetricsExporter:
    """Accumulate every successfully processed matching email report."""

    DIMENSIONS = (
        "start_date",
        "request_ana_basligi",
        "request_alt_basligi1",
        "request_alt_basligi2",
        "il",
        "ilce",
        "altyapi",
        "altyapi_yeni",
        "airpone",
        "sw_hostname",
        "modem_model",
        "modem_marka",
        "paket",
        "hiz",
        "saha",
    )
    MAX_VALUES_PER_DIMENSION = 1000
    DIMENSION_VALUE_LIMITS = {
        # AirPON identifiers are expected to be more numerous than ordinary
        # categorical report fields and are intentionally available for topk()
        # analysis in Grafana.
        "airpone": 25000,
    }
    INFO_LABELS = (
        "start_date", "il", "ilce", "request_ana_basligi",
        "request_alt_basligi1", "request_alt_basligi2",
        "altyapi", "altyapi_yeni",
    )
    MAX_NETWORK_SERVICE_SERIES = 100000

    def __init__(
        self,
        output_prom_path: Path = settings.PROMETHEUS_METRICS_PATH,
        store_path: Optional[Path] = settings.METRICS_STATE_PATH,
    ):
        self.output_prom_path = Path(output_prom_path)
        self.store_path = Path(store_path) if store_path else None
        state_exists = bool(self.store_path and self.store_path.exists())
        self.state = self._load_state()
        self.metadata: Dict[str, Any] = {}
        self.current_rows = 0
        self.current_counts: Dict[str, Counter] = {}
        self.current_network_service_ids = set()
        self.current_network_service_records: Dict[str, Dict[str, str]] = {}
        if not state_exists:
            self._save_state()
        if not self.output_prom_path.exists() or not state_exists:
            self.write_prom_file()

    def _empty_state(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "reports_processed": 0,
            "rows_processed": 0,
            "last_success_unixtime": 0,
            "dimensions": {name: {} for name in self.DIMENSIONS},
            "network_service_ids": [],
            "network_service_records": {},
        }

    def _load_state(self) -> Dict[str, Any]:
        if not self.store_path or not self.store_path.exists():
            return self._empty_state()
        try:
            loaded = json.loads(self.store_path.read_text(encoding="utf-8"))
            if loaded.get("version") != 1:
                raise ValueError("Unsupported cumulative metric state version")
            for name in self.DIMENSIONS:
                loaded.setdefault("dimensions", {}).setdefault(name, {})
            loaded.setdefault("network_service_ids", [])
            loaded.setdefault("network_service_records", {})
            return loaded
        except Exception as exc:
            raise RuntimeError(f"Cumulative metric state could not be loaded: {self.store_path}") from exc

    @staticmethod
    def _escape_label_value(value: Any) -> str:
        return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')

    @staticmethod
    def _month_value(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text[:7] if len(text) >= 7 else text

    @staticmethod
    def _date_value(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text[:10] if len(text) >= 10 else text

    def begin_report(self, metadata: Dict[str, Any]) -> None:
        self.metadata = dict(metadata)
        self.current_rows = 0
        self.current_counts = {name: Counter() for name in self.DIMENSIONS}
        self.current_network_service_ids = set()
        self.current_network_service_records = {}

    def add_dataframe(self, df: pl.DataFrame) -> int:
        if df.is_empty():
            return 0

        if "network_service_id" in df.columns:
            info_columns = [
                name for name in ("network_service_id", *self.INFO_LABELS)
                if name in df.columns
            ]
            # Select only report fields. Technical metadata such as ingested_at
            # can contain timezone-aware datetimes and is not part of this metric.
            for row in df.select(info_columns).iter_rows(named=True):
                raw_service_id = row.get("network_service_id")
                service_id = "" if raw_service_id is None else str(raw_service_id).strip()
                if not service_id:
                    continue
                self.current_network_service_ids.add(service_id)
                labels: Dict[str, str] = {"network_service_id": service_id}
                for label_name in self.INFO_LABELS:
                    value = row.get(label_name)
                    if label_name == "start_date":
                        value = self._date_value(value)
                    if value is None:
                        continue
                    clean_value = str(value).strip()
                    if clean_value:
                        labels[label_name] = clean_value
                record_key = json.dumps(labels, ensure_ascii=False, sort_keys=True)
                self.current_network_service_records.setdefault(record_key, labels)
        self.current_rows += df.height
        for dimension in self.DIMENSIONS:
            if dimension not in df.columns:
                continue
            values = df[dimension].to_list()
            if dimension == "start_date":
                values = [self._month_value(value) for value in values]
            for value in values:
                if value is None:
                    continue
                clean_value = str(value).strip()
                if clean_value:
                    self.current_counts[dimension][clean_value] += 1
        return df.height

    def finish_report(self) -> None:
        self.state["reports_processed"] += 1
        self.state["rows_processed"] += self.current_rows
        self.state["last_success_unixtime"] = int(time.time())

        for dimension, new_counts in self.current_counts.items():
            cumulative = Counter(self.state["dimensions"].get(dimension, {}))
            cumulative.update(new_counts)
            self.state["dimensions"][dimension] = dict(cumulative)

        network_service_ids = set(self.state.setdefault("network_service_ids", []))
        network_service_ids.update(self.current_network_service_ids)
        self.state["network_service_ids"] = sorted(network_service_ids)

        network_service_records = self.state.setdefault("network_service_records", {})
        for record_key, labels in self.current_network_service_records.items():
            network_service_records.setdefault(record_key, labels)

        self._save_state()
        self.write_prom_file()
        logger.success(
            f"Added '{self.metadata.get('original_filename', 'unknown')}' to cumulative metrics: "
            f"{self.current_rows} new rows, {self.state['rows_processed']} total rows."
        )

    def export_dataframe(self, df: pl.DataFrame, metadata: Dict[str, Any]) -> int:
        self.begin_report(metadata)
        rows = self.add_dataframe(df)
        self.finish_report()
        return rows

    def _save_state(self) -> None:
        if not self.store_path:
            return
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.store_path.with_suffix(".json.tmp")
        with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(self.state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, self.store_path)

    def write_prom_file(self) -> None:
        lines = [
            "# HELP email_reports_processed_total Successfully processed matching email reports",
            "# TYPE email_reports_processed_total counter",
            f"email_reports_processed_total {self.state['reports_processed']}",
            "# HELP email_rows_processed_total Rows accumulated from all matching email reports",
            "# TYPE email_rows_processed_total counter",
            f"email_rows_processed_total {self.state['rows_processed']}",
            "# HELP email_report_last_success_unixtime Unix time of the latest successful report",
            "# TYPE email_report_last_success_unixtime gauge",
            f"email_report_last_success_unixtime {self.state['last_success_unixtime']}",
            "# HELP email_unique_network_service_ids_total Unique Network Service IDs processed from matching emails",
            "# TYPE email_unique_network_service_ids_total gauge",
            f"email_unique_network_service_ids_total {len(self.state.get('network_service_ids', []))}",
        ]

        network_records = self.state.get("network_service_records", {})
        if network_records and len(network_records) <= self.MAX_NETWORK_SERVICE_SERIES:
            lines.extend([
                "# HELP complaint_network_service_info Network Service ID and complaint attributes from matching emails",
                "# TYPE complaint_network_service_info gauge",
            ])
            for _, labels in sorted(network_records.items()):
                label_text = ",".join(
                    f'{name}="{self._escape_label_value(value)}"'
                    for name, value in labels.items()
                )
                lines.append(f"complaint_network_service_info{{{label_text}}} 1")
        elif len(network_records) > self.MAX_NETWORK_SERVICE_SERIES:
            logger.warning(
                f"Skipping complaint_network_service_info: {len(network_records)} records exceeds "
                f"the safety limit of {self.MAX_NETWORK_SERVICE_SERIES}."
            )

        for dimension in self.DIMENSIONS:
            counter = Counter(self.state["dimensions"].get(dimension, {}))
            if not counter:
                continue
            value_limit = self.DIMENSION_VALUE_LIMITS.get(
                dimension, self.MAX_VALUES_PER_DIMENSION
            )
            if len(counter) > value_limit:
                logger.warning(
                    f"Skipping dimension '{dimension}': {len(counter)} unique values exceeds "
                    f"the safety limit of {value_limit}."
                )
                continue
            metric_name = re.sub(r"[^a-zA-Z0-9_:]", "_", dimension) + "_total"
            lines.extend([
                f"# HELP {metric_name} Cumulative rows from all matching emails grouped by {dimension}",
                f"# TYPE {metric_name} counter",
            ])
            for value, count in sorted(counter.items()):
                escaped = self._escape_label_value(value)
                lines.append(f'{metric_name}{{{dimension}="{escaped}"}} {count}')

        lines.append("")
        temp_path = self.output_prom_path.with_suffix(".prom.tmp")
        self.output_prom_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("\n".join(lines))
            os.replace(temp_path, self.output_prom_path)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
