from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts.rebuild_operating_systems_from_pdf import ensure_fulltext


class CoursePreparationTests(unittest.TestCase):
    def test_extracts_pages_without_bytecode_or_subprocess(self):
        pages = [Mock(), Mock(), Mock()]
        for page, text in zip(pages, ["Chapter 1", None, "Chapter 2"]):
            page.extract_text.return_value = text
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "pypdf.PdfReader", return_value=SimpleNamespace(pages=pages)
            ) as reader:
                result = ensure_fulltext(root / "book.pdf", root / "text")
            reader.assert_called_once_with(str(root / "book.pdf"))
            self.assertEqual(result.read_text(), "Chapter 1\n\n\n\nChapter 2\n")

    def test_existing_fulltext_is_reused(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "book.txt"
            existing.write_text("x" * 100001)
            with patch("pypdf.PdfReader") as reader:
                self.assertEqual(ensure_fulltext(root / "book.pdf", root), existing)
            reader.assert_not_called()

    def test_image_only_pdf_does_not_overwrite_existing_text(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "book.txt"
            existing.write_text("previous text")
            with patch("pypdf.PdfReader", return_value=SimpleNamespace(pages=[])):
                with self.assertRaisesRegex(ValueError, "OCR"):
                    ensure_fulltext(root / "book.pdf", root)
            self.assertEqual(existing.read_text(), "previous text")
