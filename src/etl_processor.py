import polars as pl
from pathlib import Path
from typing import Iterator, Dict, Any, List
import re
import openpyxl
from datetime import datetime, timezone
from src.config import settings
from src.logger import logger


class HighPerformanceETLEngine:
    """Polars-based high performance ETL Engine for large Excel and CSV files."""

    def __init__(self, chunk_size: int = settings.CHUNK_SIZE):
        self.chunk_size = chunk_size
        pl.Config.set_verbose(False)

    @staticmethod
    def _clean_column_name(col: str) -> str:
        """Converts column names to clean snake_case SQL-friendly identifiers with canonical Turkish aliases."""
        col = str(col).strip().lower()
        tr_map = str.maketrans("çğıöşü", "cgiosu")
        cleaned = col.translate(tr_map)
        cleaned = re.sub(r"[^\w\s]", "", cleaned)
        cleaned = re.sub(r"\s+", "_", cleaned).strip("_")

        # Canonical report column mappings
        alias_map = {
            "bilet_id": "bilet_id",
            "bilet_no": "bilet_id",
            "bilet_numarasi": "bilet_id",
            "network_service_id": "network_service_id",
            "start_date": "start_date",
            "request_ana_basligi": "request_ana_basligi",
            "request_alt_basligi_1": "request_alt_basligi1",
            "request_alt_basligi1": "request_alt_basligi1",
            "request_alt_basligi_2": "request_alt_basligi2",
            "request_alt_basligi2": "request_alt_basligi2",
            "request_alt_basligi_2": "request_alt_basligi2",
            "il": "il",
            "ilce": "ilce",
            "altyapi": "altyapi",
            "altyapi_saglayici": "altyapi",
            "altyapi_yeni": "altyapi_yeni",
            "altyapi_tipi": "altyapi_yeni",
            "airpone": "airpone",
            "airpone_ip_sayi": "airpone",
            "airpone_ipsayi": "airpone",
            "sw_hostname": "sw_hostname",
            "modem_model": "modem_model",
            "modem_marka": "modem_marka",
            "modem_seri_no": "modem_seri_no",
            "paket": "paket",
            "hiz": "hiz",
            "saha": "saha",
            "saha_dolabi": "saha",
            "saha_dolabi_santral": "saha"
        }
        return alias_map.get(cleaned, cleaned)

    def process_file_in_chunks(
        self, file_path: Path, metadata: Dict[str, Any]
    ) -> Iterator[pl.DataFrame]:
        """Reads `.xlsx` or `.csv` using Polars and yields cleaned DataFrames in chunks."""
        file_path = Path(file_path)
        ext = file_path.suffix.lower()

        logger.info(f"Starting ETL transformation for file: {file_path.name} (Type: {ext})")

        try:
            if ext == ".csv":
                yield from self._process_csv(file_path, metadata)
            elif ext in [".xlsx", ".xls"]:
                yield from self._process_excel(file_path, metadata)
            else:
                raise ValueError(f"Unsupported file format: {ext}")
        except Exception as e:
            logger.error(f"ETL pipeline error processing {file_path.name}: {e}", exc_info=True)
            raise

    def _process_csv(
        self, file_path: Path, metadata: Dict[str, Any]
    ) -> Iterator[pl.DataFrame]:
        """Streams CSV in chunks using Polars scan_csv or eager processing."""
        try:
            lazy_df = pl.scan_csv(file_path, infer_schema_length=10000, ignore_errors=True)
            schema = lazy_df.collect_schema()
            clean_cols = {col: self._clean_column_name(col) for col in schema.names()}

            total_rows = lazy_df.select(pl.len()).collect().item()
            logger.info(f"CSV file scanned successfully. Total rows to process: {total_rows}")

            num_chunks = (total_rows + self.chunk_size - 1) // self.chunk_size
            for i in range(num_chunks):
                offset = i * self.chunk_size
                chunk = (
                    lazy_df.slice(offset, self.chunk_size)
                    .rename(clean_cols)
                    .collect()
                )
                chunk = self._enrich_and_clean(chunk, metadata)
                yield chunk

        except Exception as e:
            logger.warning(f"Lazy CSV scan failed, falling back to eager batched processing: {e}")
            df = pl.read_csv(file_path, infer_schema_length=10000, ignore_errors=True)
            clean_cols = {col: self._clean_column_name(col) for col in df.columns}
            df = df.rename(clean_cols)
            df = self._enrich_and_clean(df, metadata)

            for chunk in df.iter_slices(n_rows=self.chunk_size):
                yield chunk

    def _process_excel(
        self, file_path: Path, metadata: Dict[str, Any]
    ) -> Iterator[pl.DataFrame]:
        """Reads Excel file safely converting dirty cells and yielding chunked DataFrames."""
        df = None
        try:
            df = pl.read_excel(file_path, engine="calamine")
        except Exception:
            logger.warning("Calamine engine unavailable or failed, reading Excel via openpyxl string parser...")
            wb = openpyxl.load_workbook(file_path, read_only=True)
            try:
                sheet = wb.active
                rows = sheet.iter_rows(values_only=True)
                header_row = next(rows)
                headers = [self._clean_column_name(h) for h in header_row]

                clean_rows = []
                for r in rows:
                    clean_rows.append([str(v) if v is not None else None for v in r])

                df = pl.DataFrame(clean_rows, schema=headers, orient="row")
            finally:
                wb.close()

        clean_cols = {col: self._clean_column_name(col) for col in df.columns}
        df = df.rename(clean_cols)
        df = self._enrich_and_clean(df, metadata)

        logger.info(f"Excel loaded into memory. Total rows: {df.height}. Slicing into chunks of {self.chunk_size}.")

        for chunk in df.iter_slices(n_rows=self.chunk_size):
            yield chunk

    def _enrich_and_clean(self, df: pl.DataFrame, metadata: Dict[str, Any]) -> pl.DataFrame:
        """Applies robust type casting, null handling, datetime parsing, and metadata enrichment."""
        expressions = []

        # Datetime & Numeric column casting
        for col, dtype in df.schema.items():
            # Datetime column detection & parsing
            if any(kw in col for kw in ["date", "time", "tarih", "saat", "created", "timestamp", "damgası", "damgasi"]):
                if dtype == pl.String:
                    expressions.append(pl.col(col).str.to_datetime(strict=False).alias(col))

            # Numeric column detection & safe cast
            elif any(kw in col for kw in ["gecikme", "tutar", "adet", "satis", "price", "amount"]):
                expressions.append(pl.col(col).cast(pl.Float64, strict=False).alias(col))

        if expressions:
            df = df.with_columns(expressions)

        # Compute deterministic row_hash based on data payload columns
        data_cols = [c for c in df.columns if c not in ["id", "ingested_at", "source_file", "email_message_id", "row_hash"]]
        if data_cols:
            df = df.with_columns(
                pl.concat_str([pl.col(c).cast(pl.String).fill_null("") for c in data_cols], separator="||")
                .hash()
                .cast(pl.String)
                .alias("row_hash")
            )

        # Deduplicate incoming DataFrame batch (Bilet ID, Islem ID, Composite (ID+Date+Request), or Row Hash)
        if "bilet_id" in df.columns:
            df = df.unique(subset=["bilet_id"], keep="first")
        elif "islem_id" in df.columns:
            df = df.unique(subset=["islem_id"], keep="first")
        elif "network_service_id" in df.columns and "start_date" in df.columns and "request_ana_basligi" in df.columns:
            df = df.unique(subset=["network_service_id", "start_date", "request_ana_basligi"], keep="first")
        elif "row_hash" in df.columns:
            df = df.unique(subset=["row_hash"], keep="first")

        # Append tracking metadata columns
        df = df.with_columns([
            pl.lit(datetime.now(timezone.utc)).alias("ingested_at"),
            pl.lit(metadata.get("original_filename", "")).alias("source_file"),
            pl.lit(metadata.get("message_id", "")).alias("email_message_id")
        ])

        return df
