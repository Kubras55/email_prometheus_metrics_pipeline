import asyncio
import signal
import sys
import os
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings
from src.logger import logger
from src.mail_listener import EmailIngestionService
from src.etl_processor import HighPerformanceETLEngine
from src.metrics_exporter import PrometheusMetricsExporter
from src.metrics_server import start_metrics_server

stop_event = asyncio.Event()


def handle_shutdown_signals(sig, frame):
    logger.warning(f"Received termination signal ({sig}). Initiating graceful shutdown...")
    stop_event.set()


async def process_single_file(
    file_info: dict,
    etl_engine: HighPerformanceETLEngine,
    exporter: PrometheusMetricsExporter,
    mail_service: EmailIngestionService
):
    file_path = Path(file_info["file_path"])
    state_key = file_info["state_key"]
    filename = file_info["original_filename"]

    if not file_path.exists():
        logger.warning(f"File '{filename}' ({file_path}) no longer exists. Skipping.")
        return

    logger.info(f"Processing downloaded attachment: {filename} ({file_path})")
    total_file_rows = 0

    try:
        exporter.begin_report(file_info)
        seen_row_hashes = set()
        for chunk_df in etl_engine.process_file_in_chunks(file_path, file_info):
            if "row_hash" in chunk_df.columns:
                chunk_df = chunk_df.filter(~chunk_df["row_hash"].is_in(seen_row_hashes))
                seen_row_hashes.update(chunk_df["row_hash"].to_list())
            rows_processed = exporter.add_dataframe(chunk_df)
            total_file_rows += rows_processed

        exporter.finish_report()

        # Mark processed in state manager
        mail_service.state_manager.mark_processed(state_key)
        logger.success(f"Completed metric export for '{filename}'. Total processed rows: {total_file_rows}")

        # Cleanup downloaded file to prevent disk exhaustion
        if file_path.exists():
            try:
                file_path.unlink()
                logger.info(f"Cleaned up temporary file: {file_path.name}")
            except Exception as unlink_err:
                logger.warning(f"Could not delete temporary file {file_path.name}: {unlink_err}")

    except Exception as e:
        logger.error(f"Failed to process file '{filename}': {e}", exc_info=True)


async def run_pipeline():
    logger.info("Initializing Email Excel/CSV ETL Pipeline Daemon (Prometheus `.prom` Metrics Mode)...")
    settings.ensure_directories()
    
    mail_service = EmailIngestionService()
    etl_engine = HighPerformanceETLEngine()
    exporter = PrometheusMetricsExporter()
    metrics_server = start_metrics_server()

    logger.info(f"Metrics output configured at: {settings.PROMETHEUS_METRICS_PATH.resolve()}")
    logger.info(f"Pipeline running. Polling interval: {settings.POLL_INTERVAL_SECONDS} seconds.")

    try:
        while not stop_event.is_set():
            logger.info("Polling IMAP server for new email attachments...")

            try:
                downloaded_files = mail_service.fetch_new_attachments()

                if downloaded_files:
                    logger.info(f"Retrieved {len(downloaded_files)} new file(s) to process.")
                    for file_info in downloaded_files:
                        if stop_event.is_set():
                            break
                        await process_single_file(file_info, etl_engine, exporter, mail_service)
                else:
                    logger.info("No new matching attachments found in this cycle.")

            except Exception as e:
                logger.error(f"Error during ingestion cycle: {e}", exc_info=True)

            logger.info(f"Sleeping for {settings.POLL_INTERVAL_SECONDS} seconds before next poll...")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=settings.POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    finally:
        metrics_server.shutdown()
        metrics_server.server_close()
        logger.info("ETL Pipeline Daemon stopped cleanly.")


def main():
    signal.signal(signal.SIGINT, handle_shutdown_signals)
    signal.signal(signal.SIGTERM, handle_shutdown_signals)

    try:
        asyncio.run(run_pipeline())
    except KeyboardInterrupt:
        logger.info("Pipeline terminated by user.")


if __name__ == "__main__":
    main()
