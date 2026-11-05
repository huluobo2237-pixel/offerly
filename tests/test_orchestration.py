"""Task source validation and real PDF text extraction without model requests."""

import tempfile
import hashlib
import unittest
from pathlib import Path
from pydantic import ValidationError
from reportlab.pdfgen.canvas import Canvas
from pypdf import PdfWriter
from offerly.orchestration import TaskLaunch, extract_resume_text
from offerly.services import ApplicationServices
from frontend.workspace import Attachment, Workspace, WorkspaceRecord


class TaskInputTests(unittest.TestCase):
    def test_prepared_uploads_keep_original_names_and_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            services = ApplicationServices(Path(directory))
            workspace = services.workspace = Workspace(
                services.root / "workspace", services.model_store
            )
            attachments = []
            expected = {}
            for index, (kind, name) in enumerate(
                [
                    ("resume", "胡平-AI方向.pdf"),
                    ("photo", "个人照片.PNG"),
                    ("cet4", "四级成绩.pdf"),
                    ("other", "四级成绩.pdf"),
                ],
                1,
            ):
                data = f"fixture-{index}".encode()
                attachment = Attachment(
                    id=f"{index:032x}",
                    type=kind,
                    name=name,
                    size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
                source = workspace.path(attachment)
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(data)
                attachments.append(attachment)
                expected[attachment.id] = data
            workspace.write(WorkspaceRecord(attachments=attachments))
            _, files = services.agent.prepare_materials(0, "upload")
            self.assertEqual(len({f["path"] for f in files}), 4)
            for attachment, material in zip(attachments, files):
                path = Path(material["path"])
                self.assertEqual(path.name, attachment.name)
                self.assertEqual(path.read_bytes(), expected[attachment.id])
                self.assertEqual(
                    workspace.path(attachment).read_bytes(), path.read_bytes()
                )
            # A modified staging copy is repaired from the verified original.
            Path(files[0]["path"]).write_bytes(b"changed")
            _, repaired = services.agent.prepare_materials(0, "upload")
            self.assertEqual(
                Path(repaired[0]["path"]).read_bytes(), expected[attachments[0].id]
            )
            workspace.remove_file(attachments[0])
            self.assertFalse(Path(files[0]["path"]).exists())
            self.assertFalse(workspace.path(attachments[0]).exists())
            self.assertTrue(Path(files[1]["path"]).is_file())

    def test_upload_copy_rejects_tampered_source_and_unsafe_names(self):
        with tempfile.TemporaryDirectory() as directory:
            services = ApplicationServices(Path(directory))
            workspace = Workspace(services.root / "workspace", services.model_store)
            attachment = Attachment(
                id="1" * 32,
                type="resume",
                name="简历.pdf",
                size=3,
                sha256=hashlib.sha256(b"abc").hexdigest(),
            )
            source = workspace.path(attachment)
            source.parent.mkdir(parents=True)
            source.write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "附件已失效"):
                workspace.upload_path(attachment)
            source.write_bytes(b"abc")
            for name in ("../escape.pdf", "C:\\escape.pdf", "x.pdf "):
                with self.assertRaisesRegex(ValueError, "文件名"):
                    workspace.upload_path(attachment.model_copy(update={"name": name}))

    def test_pdf_emoji_surrogates_are_repaired_before_creating_agent_task(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from pydantic import TypeAdapter

        raw = (
            "Python API development and project engineering experience. " * 3
            + "\ud83d"
            + " 网站链接 "
            + "\ud83d\ude80"
        )
        reader = SimpleNamespace(
            is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: raw)]
        )
        with patch("pypdf.PdfReader", return_value=reader):
            result = extract_resume_text("unused.pdf")
        text = result["text"]
        self.assertIn("🚀", text)
        self.assertIn("网站链接", text)
        text.encode("utf-8")
        self.assertEqual(TypeAdapter(str).validate_python(text), text)

    def launch(self, **extra):
        return TaskLaunch.model_validate(
            {
                "browser": "edge",
                "pluginConfirmed": True,
                "workspaceRevision": 0,
                "resumePlan": "upload",
                "source": "search",
                "count": 2,
                "requirements": {"keywords": "Python"},
                **extra,
            }
        )

    def test_automatic_search_needs_no_preselected_jobs(self):
        self.assertEqual(self.launch().count, 2)

    def test_company_targets_match_count(self):
        value = self.launch(
            source="companies", targets=[{"company": "UCloud"}, {"company": "汇川技术"}]
        )
        self.assertEqual(len(value.targets), 2)
        with self.assertRaises(ValidationError):
            self.launch(
                source="companies",
                count=1,
                targets=[{"company": "UCloud"}, {"company": "汇川技术"}],
            )

    def test_company_targets_accept_names_and_application_links_together(self):
        value = self.launch(
            source="companies",
            targets=[
                {"company": "汇川技术"},
                {"url": "https://ucloud.zhiye.com/form?jobAdId=fixture"},
            ],
        )
        self.assertEqual(value.targets[0].company, "汇川技术")
        self.assertTrue(value.targets[1].url.startswith("https://"))
        with self.assertRaises(ValidationError):
            self.launch(
                source="companies",
                count=1,
                targets=[{"company": "UCloud", "url": "https://ucloud.zhiye.com/"}],
            )

    def test_website_rejects_script_and_credentials(self):
        for url in ["javascript:alert(1)", "https://user:password@example.invalid/"]:
            with self.assertRaises(ValidationError):
                self.launch(source="websites", count=1, targets=[{"url": url}])

    def test_search_tool_available_for_autonomous_planning(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = ApplicationServices(Path(directory)).agent
            agent.task_options = self.launch(
                source="companies", count=1, targets=[{"company": "UCloud"}]
            )
            names = agent.tools().registry.registry.actions
            self.assertIn("search", names)
            self.assertTrue(
                {
                    "search_jobs",
                    "choose_job",
                    "generate_resume",
                    "read_candidate_profile",
                }.issubset(names)
            )
            agent.task_options = self.launch(
                source="websites", count=1, targets=[{"url": "https://example.invalid"}]
            )
            self.assertIn("search", agent.tools().registry.registry.actions)

    def test_pdf_resume_is_read_and_contact_numbers_are_not_needed_for_matching(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resume.pdf"
            canvas = Canvas(str(path))
            canvas.drawString(
                40,
                750,
                "Computer Science graduate. Python backend API and engineering projects.",
            )
            canvas.drawString(40, 730, "Contact: local@example.invalid 13800000000")
            canvas.save()
            data = extract_resume_text(path)
            self.assertIn("Python backend", data["text"])
            self.assertNotIn("local@example.invalid", data["text"])
            self.assertNotIn("13800000000", data["text"])
            self.assertEqual(data["pages"], 1)

    def test_scanned_or_empty_pdf_is_not_fabricated_into_candidate_facts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.pdf"
            writer = PdfWriter()
            writer.add_blank_page(200, 200)
            with path.open("wb") as stream:
                writer.write(stream)
            with self.assertRaises(ValueError):
                extract_resume_text(path)


if __name__ == "__main__":
    unittest.main()
