import unittest
from unittest.mock import patch

from src.mail_listener import EmailIngestionService


class TestMailSubjectFilter(unittest.TestCase):
    def test_exact_turkish_subject_match(self):
        with patch("src.mail_listener.settings.EMAIL_SUBJECT_MATCH_MODE", "exact"):
            self.assertTrue(EmailIngestionService._matches_subject_filter(
                "Şikayet Oranı Dağılımı", "şikayet oranı dağılımı"
            ))
            self.assertFalse(EmailIngestionService._matches_subject_filter(
                "Aylık Şikayet Oranı Dağılımı", "şikayet oranı dağılımı"
            ))

    def test_contains_mode(self):
        with patch("src.mail_listener.settings.EMAIL_SUBJECT_MATCH_MODE", "contains"):
            self.assertTrue(EmailIngestionService._matches_subject_filter(
                "Aylık Şikayet Oranı Dağılımı", "şikayet oranı dağılımı"
            ))


if __name__ == "__main__":
    unittest.main()
