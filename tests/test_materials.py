import io
import tempfile
import unittest
from pathlib import Path
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
from pypdf import PdfReader
from offerly.services import ApplicationServices
from frontend.backend import make_app
from offerly.resume import build_document, render_resume, validate_plan


class MaterialsTests(unittest.IsolatedAsyncioTestCase):

    async def test_named_other_attachment_and_image_resume_are_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            service = ApplicationServices(Path(directory))
            async with TestClient(TestServer(make_app(service))) as client:
                client.session.headers["X-Offerly-Token"] = service.token
                revision = 0
                for kind, label in [("other", "作品集"), ("resume", "")]:
                    image = io.BytesIO()
                    Image.new("RGB", (20, 20), "white").save(image, format="PNG")
                    form = FormData()
                    form.add_field("type", kind)
                    if label:
                        form.add_field("label", label)
                    form.add_field(
                        "file",
                        image.getvalue(),
                        filename="example.png",
                        content_type="image/png",
                    )
                    response = await client.post(
                        "/api/attachments",
                        data=form,
                        headers={"X-Workspace-Revision": str(revision)},
                    )
                    self.assertEqual(response.status, 201, await response.text())
                    result = await response.json()
                    revision = result["revision"]
                    self.assertEqual(result["attachment"]["label"], label)
                    self.assertEqual(result["attachment"]["type"], kind)
                form = FormData()
                form.add_field("type", "other")
                form.add_field(
                    "file", b"test", filename="a.pdf", content_type="application/pdf"
                )
                self.assertEqual(
                    (await client.post("/api/attachments", data=form)).status, 400
                )

    async def test_single_description_replaces_legacy_split_text(self):
        doc = build_document(
            {
                "basic": {"name": "Tester"},
                "contact": {"phone": "123456789", "email": "test@example.invalid"},
                "projects": [
                    {
                        "name": "Data project",
                        "duties": "Old responsibilities",
                        "results": "Old results",
                        "resumeDescription": "One unified project description.",
                    }
                ],
            }
        )
        details = doc.modules["projects"][0].details
        self.assertEqual(details, ["One unified project description."])

    async def test_custom_module_name_and_content_reach_pdf_and_tailoring(self):
        doc = build_document(
            {
                "basic": {"name": "Tester"},
                "contact": {"phone": "123456789", "email": "test@example.invalid"},
                "customModules": [
                    {
                        "name": "Portfolio highlights",
                        "content": "Built a Python data reporting tool.",
                    }
                ],
            }
        )
        plan = validate_plan(
            {
                "rewrites": {
                    "custom:0": [
                        {
                            "text": "Developed a Python reporting tool for data analysis.",
                            "sources": [
                                {
                                    "id": "custom:0",
                                    "quote": "Built a Python data reporting tool.",
                                }
                            ],
                        }
                    ]
                }
            },
            doc,
        )
        result = render_resume(doc, "classic", ["custom"], "Data Engineer", plan)
        text = PdfReader(io.BytesIO(result.data)).pages[0].extract_text()
        self.assertIn("Portfolio highlights", text)
        self.assertIn("Developed a Python reporting tool", text)
