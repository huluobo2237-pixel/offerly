import io
import unittest
from pypdf import PdfReader
from offerly.resume import ResumeDocument, ResumeItem, validate_plan, render_resume
from offerly.resume_layout import ordered_document


class TailoringTests(unittest.TestCase):
    def document(self):
        return ResumeDocument(
            name="Test",
            contact=["test@example.invalid"],
            modules={
                "summary": [
                    ResumeItem(
                        id="summary:0",
                        title="",
                        details=["Built Python APIs and SQL reporting."],
                    )
                ],
                "projects": [
                    ResumeItem(
                        id="projects:0",
                        title="Data project",
                        date="2025",
                        details=["Built Python APIs and SQL reporting."],
                    )
                ],
                "skills": [],
                "education": [],
                "work": [],
                "internships": [],
                "campus": [],
                "practice": [],
                "awards": [],
                "certificates": [],
                "languages": [],
                "research": [],
            },
        )

    def plan(self, text):
        return {
            "rewrites": {
                "projects:0": [
                    {
                        "text": text,
                        "sources": [
                            {
                                "id": "projects:0",
                                "quote": "Built Python APIs and SQL reporting.",
                            }
                        ],
                    }
                ]
            }
        }

    def test_entry_reference_still_checks_original_facts(self):
        document = self.document()
        raw = self.plan("Developed Python APIs.")
        bullet = raw["rewrites"]["projects:0"][0]
        bullet["sources"] = [{"id": "projects:0"}]
        self.assertTrue(validate_plan(raw, document).rewrites)
        bullet["text"] = "Improved performance by 80%."
        with self.assertRaisesRegex(ValueError, "没有的数字"):
            validate_plan(raw, document)
        bullet["sources"][0]["id"] = "summary:0"
        with self.assertRaisesRegex(ValueError, "其他经历"):
            validate_plan(raw, document)

    def test_job_wording_changes_pdf_without_changing_original_identity_or_dates(self):
        doc = self.document()
        a = validate_plan(
            self.plan("Developed Python APIs to deliver application features."), doc
        )
        b = validate_plan(self.plan("Built SQL reporting to support analysis."), doc)
        original = doc.model_dump()
        first = (
            PdfReader(
                io.BytesIO(
                    render_resume(doc, "classic", ["projects"], "API Developer", a).data
                )
            )
            .pages[0]
            .extract_text()
        )
        second = (
            PdfReader(
                io.BytesIO(
                    render_resume(doc, "classic", ["projects"], "Data Analyst", b).data
                )
            )
            .pages[0]
            .extract_text()
        )
        self.assertIn("Developed Python APIs", first)
        self.assertIn("Built SQL reporting", second)
        self.assertNotEqual(first, second)
        self.assertEqual(doc.model_dump(), original)
        changed = ordered_document(doc, a)
        self.assertEqual(changed.modules["projects"][0].date, "2025")
        self.assertEqual(changed.modules["projects"][0].title, "Data project")

    def test_rejects_fake_sources_and_unsupported_metrics(self):
        doc = self.document()
        for raw in [
            self.plan("Improved performance by 80%"),
            {
                "rewrites": {
                    "missing": [
                        {
                            "text": "Test",
                            "sources": [{"id": "projects:0", "quote": "Built Python"}],
                        }
                    ]
                }
            },
            {
                "rewrites": {
                    "projects:0": [
                        {
                            "text": "Test",
                            "sources": [
                                {"id": "projects:0", "quote": "Invented experience"}
                            ],
                        }
                    ]
                }
            },
        ]:
            with self.assertRaises(ValueError):
                validate_plan(raw, doc)
