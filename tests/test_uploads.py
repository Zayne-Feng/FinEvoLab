"""Verify upload provenance, text extraction, and metadata isolation."""

import io
from pathlib import Path
import tempfile
import unittest

from reportlab.pdfgen.canvas import Canvas
from finevolab.uploads import ingest_upload


class UploadTests(unittest.TestCase):
    def test_pdf_pages_have_original_locations(self):
        buffer = io.BytesIO()
        canvas = Canvas(buffer)
        canvas.drawString(50, 750, "Revenue source on page one")
        canvas.showPage()
        canvas.drawString(50, 750, "Revenue source on page two")
        canvas.save()
        with tempfile.TemporaryDirectory() as directory:
            result = ingest_upload(buffer.getvalue(), ".pdf", "EXAMPLE", 2024, Path(directory))
        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["status"], "text_extracted")
        self.assertTrue(any(item["locator"].startswith("page=2") for item in result["chunks"]))

    def test_html_scripts_are_excluded_and_duplicates_preserve_metadata(self):
        raw = b"<html><body><script>private script text</script><p>Annual revenue source</p></body></html>"
        with tempfile.TemporaryDirectory() as directory:
            result = ingest_upload(raw, ".html", "EXAMPLE", 2024, Path(directory))
            self.assertNotIn("private script", result["chunks"][0]["text"])
            with self.assertRaisesRegex(ValueError, "different metadata"):
                ingest_upload(raw, ".html", "OTHER", 2024, Path(directory))


if __name__ == "__main__":
    unittest.main()
