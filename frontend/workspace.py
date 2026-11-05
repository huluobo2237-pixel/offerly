"""Private first-chapter storage and authenticated HTTP handlers."""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
from pathlib import Path, PureWindowsPath
from typing import Literal
from urllib.parse import unquote, urlsplit

from aiohttp import web
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
)

from offerly.attachments import ATTACHMENTS, MAX_FILE, MAX_TOTAL
from offerly.resume import MODULES, ModuleId


class Attachment(BaseModel):
    id: str
    type: str
    name: str
    label: str = Field(default="", max_length=120)
    size: int
    sha256: str


class WorkspaceRecord(BaseModel):
    revision: int = 0
    profile: dict[str, JsonValue] = Field(default_factory=dict)
    resumePlan: Literal["upload", "template"] = "upload"
    resumeTemplate: Literal["classic", "technical", "graduate"] = "classic"
    resumeSections: list[ModuleId] = Field(default_factory=lambda: list(MODULES))
    resumeOnePage: bool = True
    attachments: list[Attachment] = Field(default_factory=list)


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    profile: dict[str, JsonValue]
    resumePlan: Literal["upload", "template"] = "upload"
    resumeTemplate: Literal["classic", "technical", "graduate"] = "classic"
    resumeSections: list[ModuleId] = Field(
        default_factory=lambda: list(MODULES), min_length=1, max_length=13
    )
    resumeOnePage: bool = True

    @field_validator("resumeSections")
    @classmethod
    def unique_sections(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("模块不能重复。")
        return value


class ModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["DeepSeek"] = "DeepSeek"
    model: str = Field(min_length=1, max_length=200)
    baseUrl: str = Field(max_length=4096)
    apiKey: str = Field(default="", max_length=1000, repr=False)

    @field_validator("model", "apiKey")
    @classmethod
    def single_line(cls, value):
        if "\n" in value or "\r" in value:
            raise ValueError("配置不能包含换行。")
        return value.strip()

    @field_validator("baseUrl")
    @classmethod
    def endpoint(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("https", "http")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("请输入有效的模型 API 地址。")
        return value.rstrip("/")


class Workspace:
    """Atomic snapshots; files and credentials never enter the public static tree."""

    def __init__(self, root: Path, model_store):
        self.root = root
        self.record_path = root / "workspace.json"
        self.model_store = model_store
        self.lock = asyncio.Lock()

    def read(self):
        if not self.record_path.is_file():
            return WorkspaceRecord()
        return WorkspaceRecord.model_validate_json(
            self.record_path.read_text(encoding="utf-8")
        )

    def write(self, record):
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.record_path.with_suffix(".tmp")
        temporary.write_text(record.model_dump_json(), encoding="utf-8")
        temporary.replace(self.record_path)

    def path(self, attachment):
        if not re.fullmatch(r"[0-9a-f]{32}", attachment.id):
            raise ValueError("附件编号不正确。")
        path = (
            self.root / "files" / (attachment.id + Path(attachment.name).suffix.lower())
        ).resolve()
        path.relative_to(self.root.resolve())
        return path

    def upload_path(self, attachment):
        """Browser file inputs use the basename, so expose a verified original-name copy."""
        source = self.path(attachment)
        name = attachment.name
        if (
            not name
            or name in (".", "..")
            or name != PureWindowsPath(name).name
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.rstrip(" .") != name
        ):
            raise ValueError("附件文件名不正确，请重新上传。")
        data = source.read_bytes()
        if (
            len(data) != attachment.size
            or hashlib.sha256(data).hexdigest() != attachment.sha256
        ):
            raise ValueError("附件已失效，请重新上传。")
        target = source.parent / attachment.id / name
        target.resolve().relative_to(self.root.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_bytes() != data:
            temporary = target.with_name(name + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(target)
        return target

    def remove_file(self, attachment):
        source = self.path(attachment)
        directory = source.parent / attachment.id
        # Only delete known files in this attachment's private directory.
        target = directory / attachment.name
        target.resolve().relative_to(directory.resolve())
        target.unlink(missing_ok=True)
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
        source.unlink(missing_ok=True)

    def public_model(self):
        saved = self.model_store.read() or {}
        return {k: saved[k] for k in ("provider", "model", "baseUrl") if k in saved} | {
            "configured": bool(saved.get("apiKey"))
        }

    async def get(self, request):
        async with self.lock:
            return web.json_response(
                {**self.read().model_dump(), "model": self.public_model()}
            )

    async def save_profile(self, request):
        try:
            raw = await request.read()
            if len(raw) > 1024 * 1024:
                raise ValueError("资料不能超过 1 MB。")
            update = ProfileUpdate.model_validate_json(raw)
        except (ValidationError, ValueError):
            raise web.HTTPBadRequest(text="资料格式不正确或超过大小限制。") from None
        async with self.lock:
            record = self.read()
            if record.revision != update.revision:
                raise web.HTTPConflict(text="资料已在其他页面更新，请刷新后再保存。")
            record.profile = update.profile
            record.resumePlan, record.resumeTemplate = (
                update.resumePlan,
                update.resumeTemplate,
            )
            record.resumeSections = update.resumeSections
            record.resumeOnePage = update.resumeOnePage
            record.revision += 1
            self.write(record)
            return web.json_response({"revision": record.revision})

    async def save_model(self, request):
        try:
            raw = await request.read()
            if len(raw) > 8192:
                raise ValueError("配置过长。")
            update = ModelUpdate.model_validate_json(raw)
            if not update.model:
                raise ValueError("缺少模型名称。")
        except (ValidationError, ValueError):
            raise web.HTTPBadRequest(
                text="请检查 API 地址、模型名称和密钥格式。"
            ) from None
        async with self.lock:
            saved = self.model_store.read() or {}
            key = update.apiKey
            if not key:
                if update.baseUrl != saved.get("baseUrl", "").rstrip("/"):
                    raise web.HTTPBadRequest(text="更换 API 地址时需要输入新密钥。")
                key = saved.get("apiKey", "")
            if not key:
                raise web.HTTPBadRequest(text="请输入 API Key。")
            self.model_store.write(
                {**update.model_dump(exclude={"apiKey"}), "apiKey": key}
            )
            return web.json_response(self.public_model())

    def check_revision(self, request, record):
        if request.headers.get("X-Workspace-Revision") != str(record.revision):
            raise web.HTTPConflict(text="资料或附件已在其他页面更新，请刷新后再操作。")

    async def upload(self, request):
        if not request.content_type.startswith("multipart/"):
            raise web.HTTPBadRequest(text="请选择附件文件。")
        reader = await request.multipart()
        part = await reader.next()
        if part is None or part.name != "type":
            raise web.HTTPBadRequest(text="缺少附件类别。")
        kind_raw = await part.read_chunk()
        if len(kind_raw) > 80:
            raise web.HTTPBadRequest(text="附件类别不正确。")
        kind = kind_raw.decode("utf-8")
        if kind not in ATTACHMENTS:
            raise web.HTTPBadRequest(text="附件类别不正确。")
        part = await reader.next()
        label = ""
        if part is not None and part.name == "label":
            raw_label = await part.read()
            if len(raw_label) > 480:
                raise web.HTTPBadRequest(text="附件说明过长。")
            label = raw_label.decode("utf-8").strip()
            if not label or len(label) > 120:
                raise web.HTTPBadRequest(text="请填写附件是什么。")
            part = await reader.next()
        if kind == "other" and not label:
            raise web.HTTPBadRequest(text="请先填写其他附件的名称或用途。")
        name = unquote(part.filename or "") if part else ""
        if (
            not part
            or part.name != "file"
            or not name
            or len(name) > 180
            or name != PureWindowsPath(name).name
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.rstrip(" .") != name
        ):
            raise web.HTTPBadRequest(text="附件文件名不正确。")
        extension = Path(name).suffix.lower()
        if extension not in ATTACHMENTS[kind]:
            raise web.HTTPBadRequest(text="附件格式不符合类别。")
        async with self.lock:
            record = self.read()
            self.check_revision(request, record)
            single = kind in ("resume", "englishResume", "photo")
            replaced = (
                [a for a in record.attachments if a.type == kind] if single else []
            )
            retained = [a for a in record.attachments if a not in replaced]
            if len(retained) >= 100:
                raise web.HTTPBadRequest(text="附件数量已达上限。")
            attachment = Attachment(
                id=secrets.token_hex(16),
                type=kind,
                name=name,
                label=label,
                size=0,
                sha256="",
            )
            path = self.path(attachment)
            path.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            head = bytearray()
            try:
                with path.open("wb") as target:
                    while chunk := await part.read_chunk():
                        attachment.size += len(chunk)
                        if (
                            attachment.size > MAX_FILE
                            or sum(a.size for a in retained) + attachment.size
                            > MAX_TOTAL
                        ):
                            raise ValueError("每份最多 20 MB，全部附件最多 100 MB。")
                        digest.update(chunk)
                        if len(head) < 1024:
                            head.extend(chunk[: 1024 - len(head)])
                        target.write(chunk)
                if not attachment.size or (
                    extension == ".pdf" and b"%PDF-" not in head
                ):
                    raise ValueError("文件为空或不是有效的 PDF。")
                if await reader.next() is not None:
                    raise ValueError("一次只能上传一个附件。")
                attachment.sha256 = digest.hexdigest()
                record.attachments = retained + [attachment]
                record.revision += 1
                self.write(record)
            except BaseException as exc:
                path.unlink(missing_ok=True)
                if isinstance(exc, ValueError):
                    raise web.HTTPBadRequest(text=str(exc)) from None
                raise
            for old in replaced:
                self.remove_file(old)
            return web.json_response(
                {"attachment": attachment.model_dump(), "revision": record.revision},
                status=201,
            )

    async def download(self, request):
        attachment = next(
            (a for a in self.read().attachments if a.id == request.match_info["id"]),
            None,
        )
        if not attachment:
            raise web.HTTPNotFound(text="附件不存在。")
        path = self.path(attachment)
        if (
            not path.is_file()
            or path.stat().st_size != attachment.size
            or hashlib.sha256(path.read_bytes()).hexdigest() != attachment.sha256
        ):
            raise web.HTTPConflict(text="附件已变化，请重新上传。")
        return web.FileResponse(path)

    async def remove(self, request):
        async with self.lock:
            record = self.read()
            self.check_revision(request, record)
            attachment = next(
                (a for a in record.attachments if a.id == request.match_info["id"]),
                None,
            )
            if not attachment:
                raise web.HTTPNotFound(text="附件不存在。")
            record.attachments = [
                a for a in record.attachments if a.id != attachment.id
            ]
            record.revision += 1
            self.write(record)
            self.remove_file(attachment)
            return web.json_response({"revision": record.revision})

    def routes(self):
        return [
            web.get("/api/workspace", self.get),
            web.put("/api/profile", self.save_profile),
            web.put("/api/model", self.save_model),
            web.post("/api/attachments", self.upload),
            web.get("/api/attachments/{id}", self.download),
            web.delete("/api/attachments/{id}", self.remove),
        ]
