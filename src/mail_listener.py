import imaplib
import email
from email.header import decode_header
import os
import json
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
from src.config import settings
from src.logger import logger


class StateManager:
    """Manages processed email IDs / attachment hashes to prevent duplicate processing."""

    def __init__(self, state_file_path: Path):
        self.state_file_path = state_file_path
        self.processed_ids: set = self._load_state()

    def _load_state(self) -> set:
        if self.state_file_path.exists():
            try:
                with open(self.state_file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("state_version") != 2:
                        logger.warning(
                            "Legacy processed-email state detected; rebuilding state for cumulative metrics."
                        )
                        return set()
                    return set(data.get("processed_ids", []))
            except Exception as e:
                logger.error(f"Failed to load state file ({self.state_file_path}): {e}")
                return set()
        return set()

    def is_processed(self, identifier: str) -> bool:
        return identifier in self.processed_ids

    def mark_processed(self, identifier: str, persist: bool = True) -> None:
        self.processed_ids.add(identifier)
        if persist:
            self._save_state()

    def flush(self) -> None:
        self._save_state()

    def _save_state(self) -> None:
        try:
            self.state_file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file_path, "w", encoding="utf-8") as f:
                json.dump(
                    {"state_version": 2, "processed_ids": sorted(self.processed_ids)},
                    f,
                    indent=2,
                )
        except Exception as e:
            logger.error(f"Failed to save state file ({self.state_file_path}): {e}")


class EmailIngestionService:
    """IMAP Email Ingestion listener downloading .xlsx/.csv attachments."""

    def __init__(self):
        settings.ensure_directories()
        self.state_manager = StateManager(settings.STATE_FILE_PATH)

    def _decode_str(self, header_val: Any) -> str:
        if not header_val:
            return ""
        decoded_list = decode_header(header_val)
        header_str = ""
        for bytes_or_str, encoding in decoded_list:
            if isinstance(bytes_or_str, bytes):
                header_str += bytes_or_str.decode(encoding or "utf-8", errors="ignore")
            else:
                header_str += str(bytes_or_str)
        return header_str

    @staticmethod
    def _matches_subject_filter(subject: str, filter_str: str) -> bool:
        """Case-insensitive and Turkish-character-tolerant subject match."""
        if not filter_str:
            return True
        tr_map = str.maketrans("ÇĞİÖŞÜçğıöşü", "CGIOSUcgiosu")
        norm_subject = subject.translate(tr_map).lower()
        norm_filter = filter_str.translate(tr_map).lower()
        if settings.EMAIL_SUBJECT_MATCH_MODE.lower() == "contains":
            return norm_filter in norm_subject
        return norm_filter == norm_subject

    def connect_imap(self) -> Optional[imaplib.IMAP4_SSL]:
        try:
            logger.info(f"Connecting to IMAP server: {settings.IMAP_SERVER}:{settings.IMAP_PORT}")
            mail = imaplib.IMAP4_SSL(settings.IMAP_SERVER, settings.IMAP_PORT)
            mail.login(settings.IMAP_USER, settings.IMAP_PASSWORD)
            logger.info(f"Successfully logged into IMAP as {settings.IMAP_USER}")
            return mail
        except Exception as e:
            logger.error(f"IMAP connection or authentication failed: {e}")
            return None

    def fetch_new_attachments(self) -> List[Dict[str, Any]]:
        """
        Polls IMAP mailbox for unread/matching emails and downloads valid excel/csv attachments.
        Returns a list of dicts containing downloaded file path and email metadata.
        """
        mail = self.connect_imap()
        downloaded_files: List[Dict[str, Any]] = []

        if not mail:
            if settings.ENABLE_LOCAL_DOWNLOAD_FALLBACK:
                logger.warning("IMAP unavailable; local download fallback is enabled.")
                return self.scan_local_downloads()
            logger.info("IMAP unavailable; local download fallback is disabled.")
            return []

        try:
            status, _ = mail.select(settings.IMAP_FOLDER)
            if status != "OK":
                logger.error(f"Failed to select IMAP folder: {settings.IMAP_FOLDER}")
                return []

            # Always search IMAP using plain ASCII 'ALL' to avoid IMAP ascii encoding errors for non-ASCII criteria
            logger.info("Searching IMAP inbox emails...")
            if settings.IMAP_SERVER.casefold() == "imap.gmail.com":
                # Gmail cannot reliably search Turkish UTF-8 subjects through imaplib,
                # so first request only messages with supported attachment names and
                # then apply the exact Unicode subject check in Python.
                gmail_query = f'"{settings.IMAP_GMAIL_ATTACHMENT_QUERY}"'
                status, data = mail.uid("search", None, "X-GM-RAW", gmail_query)
            else:
                status, data = mail.uid("search", None, "ALL")

            if status != "OK" or not data or not data[0]:
                logger.info("No emails found in inbox.")
                return []

            raw_ids = [uid for uid in data[0].split() if uid.isdigit()]
            email_ids = sorted(raw_ids, key=int, reverse=True)
            if settings.IMAP_SCAN_LIMIT > 0:
                email_ids = email_ids[:settings.IMAP_SCAN_LIMIT]
            logger.info(f"Scanning {len(email_ids)} mailbox email UID(s).")

            for email_id in email_ids:
                uid_text = email_id.decode("ascii")
                scanned_key = f"imap_scanned_uid:{uid_text}"
                if self.state_manager.is_processed(scanned_key):
                    continue

                res, msg_data = mail.uid("fetch", email_id, "(RFC822)")
                if res != "OK":
                    continue

                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])

                        msg_id = msg.get("Message-ID", uid_text)
                        subject = self._decode_str(msg.get("Subject"))
                        sender = self._decode_str(msg.get("From"))
                        date_str = self._decode_str(msg.get("Date"))

                        # Verify subject filter substring if defined (handled safely in Python)
                        if settings.EMAIL_SUBJECT_FILTER:
                            if not self._matches_subject_filter(subject, settings.EMAIL_SUBJECT_FILTER):
                                self.state_manager.mark_processed(scanned_key, persist=False)
                                continue

                        if settings.EMAIL_SENDER_FILTER:
                            if settings.EMAIL_SENDER_FILTER.casefold() not in sender.casefold():
                                self.state_manager.mark_processed(scanned_key, persist=False)
                                continue

                        logger.info(f"Examining matching email ID '{msg_id}' | Subject: '{subject}' | From: '{sender}'")

                        # Check multipart for attachments. Matching emails are revisited so that
                        # a crash between multiple attachments cannot lose an unprocessed file;
                        # attachment hashes make completed files idempotent.
                        valid_attachment_found = False
                        for part in msg.walk():
                            if part.get_content_maintype() == "multipart":
                                continue
                            if part.get("Content-Disposition") is None:
                                continue

                            filename = part.get_filename()
                            if filename:
                                filename = self._decode_str(filename)
                                ext = Path(filename).suffix.lower()

                                if ext in [".xlsx", ".xls", ".csv"]:
                                    valid_attachment_found = True
                                    file_bytes = part.get_payload(decode=True)
                                    file_hash = hashlib.sha256(file_bytes).hexdigest()

                                    # State check: Message ID + Attachment Hash
                                    state_key = f"{msg_id}_{file_hash}"
                                    if self.state_manager.is_processed(state_key):
                                        logger.info(f"Skipping already processed attachment: {filename}")
                                        continue

                                    # Save attachment locally
                                    safe_filename = f"{Path(filename).stem}_{file_hash[:8]}{ext}"
                                    save_path = settings.DOWNLOAD_DIR / safe_filename

                                    with open(save_path, "wb") as f:
                                        f.write(file_bytes)

                                    logger.success(f"Downloaded attachment to: {save_path}")

                                    downloaded_files.append({
                                        "file_path": save_path,
                                        "original_filename": filename,
                                        "file_hash": file_hash,
                                        "message_id": msg_id,
                                        "state_key": state_key,
                                        "subject": subject,
                                        "sender": sender,
                                        "email_date": date_str
                                    })

                        if not valid_attachment_found:
                            self.state_manager.mark_processed(scanned_key, persist=False)

            self.state_manager.flush()
            mail.logout()
            return downloaded_files

        except Exception as e:
            logger.error(f"Error fetching email attachments via IMAP: {e}", exc_info=True)
            return []

    def scan_local_downloads(self) -> List[Dict[str, Any]]:
        """Fallback method to scan local downloads folder when offline/testing."""
        downloaded_files: List[Dict[str, Any]] = []
        download_dir = settings.DOWNLOAD_DIR

        if not download_dir.exists():
            return []

        for file_path in download_dir.glob("*"):
            if file_path.suffix.lower() in [".xlsx", ".xls", ".csv"]:
                try:
                    with open(file_path, "rb") as f:
                        file_bytes = f.read()
                    file_hash = hashlib.sha256(file_bytes).hexdigest()
                    state_key = f"local_{file_path.name}_{file_hash}"

                    if not self.state_manager.is_processed(state_key):
                        downloaded_files.append({
                            "file_path": file_path,
                            "original_filename": file_path.name,
                            "file_hash": file_hash,
                            "message_id": f"local_{file_hash[:10]}",
                            "state_key": state_key,
                            "subject": "Local File Processing",
                            "sender": "Local User",
                            "email_date": ""
                        })
                except Exception as e:
                    logger.error(f"Error reading local file {file_path.name}: {e}")

        return downloaded_files
