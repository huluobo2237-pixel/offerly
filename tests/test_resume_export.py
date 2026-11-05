"""Compatibility export preserves page content and appearance."""

import io
import unittest
from concurrent.futures import ThreadPoolExecutor
from PIL import ImageChops
from pypdf import PdfReader
import pypdfium2 as pdfium
from reportlab.pdfgen import canvas
from offerly.resume_layout import export_compatible_pdf


class ResumeExportTests(unittest.TestCase):
    def source(self, pages):
        output = io.BytesIO()
        c = canvas.Canvas(output)
        for index in range(pages):
            c.drawString(40, 750, f"Candidate - Developer - Page {index + 1}")
            c.drawString(40, 700, "Python, JavaScript, SQL and project experience")
            c.showPage()
        c.save()
        return output.getvalue()

    def test_reexport_preserves_searchable_text_and_pixels_on_every_page(self):
        source = self.source(2)
        result = export_compatible_pdf(source)
        before = PdfReader(io.BytesIO(source), strict=True)
        after = PdfReader(io.BytesIO(result), strict=True)
        self.assertEqual(after.pdf_header, "%PDF-1.7")
        self.assertEqual(len(after.pages), 2)
        self.assertEqual(
            [p.extract_text() for p in before.pages],
            [p.extract_text() for p in after.pages],
        )
        self.assertEqual(set(after.trailer["/Root"].keys()), {"/Type", "/Pages"})
        with pdfium.PdfDocument(source) as a, pdfium.PdfDocument(result) as b:
            for index in range(len(a)):
                old, new = a[index], b[index]
                first_bitmap, second_bitmap = old.render(), new.render()
                try:
                    first = first_bitmap.to_pil().copy()
                    second = second_bitmap.to_pil().copy()
                    self.assertIsNone(ImageChops.difference(first, second).getbbox())
                finally:
                    first_bitmap.close()
                    second_bitmap.close()
                    old.close()
                    new.close()

    def test_export_serializes_concurrent_native_library_use(self):
        source = self.source(1)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(export_compatible_pdf, [source] * 3))
        for result in results:
            self.assertEqual(len(PdfReader(io.BytesIO(result), strict=True).pages), 1)
