"""Distinct resume layouts with bounded one-page fitting and lossless pagination."""

from __future__ import annotations

import io
import os
import re
import threading
from pathlib import Path
from xml.sax.saxutils import escape

from pydantic import BaseModel
from pypdf import PdfReader, PdfWriter
import pypdfium2 as pdfium
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_font_lock = threading.Lock()
_pdfium_lock = threading.Lock()


def export_compatible_pdf(data: bytes) -> bytes:
    """Re-export static pages with PDFium, preserving searchable text and layout."""
    # PDFium's native library is not thread-safe; generation runs in worker threads.
    with _pdfium_lock:
        with pdfium.PdfDocument(data) as source:
            with pdfium.PdfDocument.new() as target:
                target.import_pages(source)
                output = io.BytesIO()
                target.save(output, version=17)
                exported = output.getvalue()
    original = PdfReader(io.BytesIO(data), strict=True)
    checked = PdfReader(io.BytesIO(exported), strict=True)
    if len(checked.pages) != len(original.pages) or checked.is_encrypted:
        raise ValueError("简历兼容导出检查失败，请重新生成。")
    return exported


class RenderedResume(BaseModel):
    data: bytes
    pages: int
    body_font_size: float
    one_page_preferred: bool
    compact_lines: bool = False

    @property
    def warning(self):
        return (
            f"当前为 {self.pages} 页；已保留全部内容，可取消不重要的模块或精简描述。"
            if self.one_page_preferred and self.pages > 1
            else ""
        )


def register_fonts():
    with _font_lock:
        if {"ResumeCJK", "ResumeCJKBold"}.issubset(pdfmetrics.getRegisteredFontNames()):
            return
        candidates = (
            [Path(os.environ["OFFERLY_RESUME_FONT"])]
            if os.environ.get("OFFERLY_RESUME_FONT")
            else [
                Path("C:/Windows/Fonts/msyh.ttc"),
                Path("C:/Windows/Fonts/simhei.ttf"),
                Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
            ]
        )
        for path in candidates:
            if path.is_file():
                try:
                    pdfmetrics.registerFont(TTFont("ResumeCJK", str(path)))
                    break
                except Exception:
                    continue
        else:
            raise ValueError("未找到可嵌入的中文字体，请配置 OFFERLY_RESUME_FONT。")
        bold = Path("C:/Windows/Fonts/msyhbd.ttc")
        try:
            pdfmetrics.registerFont(
                TTFont("ResumeCJKBold", str(bold if bold.is_file() else path))
            )
        except Exception:
            pdfmetrics.registerFont(TTFont("ResumeCJKBold", str(path)))


def paragraph(value, style):
    return Paragraph(escape(value).replace("\n", "<br/>"), style)


def height_of(story, width):
    measurement = canvas.Canvas(io.BytesIO())
    total = 0
    for item in story:
        _, height = item.wrapOn(measurement, width, 1000000)
        total += height + item.getSpaceBefore() + item.getSpaceAfter()
    return total


def stream(story, x, width, top, bottom):
    if not story:
        return None
    target = io.BytesIO()
    # Account explicitly for Platypus's 6pt inner frame padding.
    doc = SimpleDocTemplate(
        target,
        pagesize=A4,
        leftMargin=x - 6,
        rightMargin=A4[0] - x - width - 6,
        topMargin=top - 6,
        bottomMargin=bottom - 6,
    )
    doc.build(story)
    return PdfReader(io.BytesIO(target.getvalue()))


def trim_spacers(story):
    while story and isinstance(story[-1], Spacer):
        story.pop()
    return story


def sections_story(document, modules, labels, width, config, size, compact_lines=False):
    ink = colors.HexColor("#17232e")
    muted = colors.HexColor("#586674")
    accent = colors.HexColor(config["color"])
    body = ParagraphStyle(
        "body",
        fontName="ResumeCJK",
        fontSize=size,
        leading=size * 1.40,
        textColor=ink,
        wordWrap="CJK",
        spaceAfter=2,
    )
    heading = ParagraphStyle(
        "section",
        parent=body,
        fontName="ResumeCJKBold",
        fontSize=size + 1.2,
        leading=size * 1.65,
        textColor=accent,
        spaceBefore=size * 0.8,
        spaceAfter=4,
        keepWithNext=True,
    )
    title = ParagraphStyle(
        "entry",
        parent=body,
        fontName="ResumeCJKBold",
        fontSize=size + 0.2,
        leading=size * 1.45,
        spaceAfter=2,
        keepWithNext=True,
    )
    date = ParagraphStyle(
        "date",
        parent=body,
        fontSize=max(8.5, size - 0.7),
        textColor=muted,
        leading=size * 1.3,
        spaceAfter=3,
    )
    story = []
    for module in modules:
        rows = document.modules[module]
        if not rows:
            continue
        if module == "custom":
            for item in rows:
                story.append(paragraph(item.title, heading))
                for detail in item.details:
                    story.append(paragraph(detail, body))
                story.append(Spacer(1, 6))
            continue
        section = paragraph(labels[module], heading)
        if config["layout"] == "balanced":
            section = Table([[section]], colWidths=[width])
            section.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#edf3ee")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 7),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            section.spaceBefore = size * 0.8
            section.spaceAfter = 5
            section.keepWithNext = True
        story.append(section)
        if config["layout"] == "single":
            rule = HRFlowable(
                width="100%",
                thickness=0.45,
                color=colors.HexColor("#b5b9bf"),
                spaceAfter=5,
            )
            rule.keepWithNext = True
            story.append(rule)
        for item in rows:
            if item.title:
                if width >= 300 and item.date:
                    right = ParagraphStyle(
                        "date-right", parent=date, alignment=TA_RIGHT
                    )
                    row = Table(
                        [[paragraph(item.title, title), paragraph(item.date, right)]],
                        colWidths=[width - 113, 113],
                    )
                    row.setStyle(
                        TableStyle(
                            [
                                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                ("TOPPADDING", (0, 0), (-1, -1), 0),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                            ]
                        )
                    )
                    row.keepWithNext = bool(item.details)
                    story.append(row)
                else:
                    title_line = paragraph(item.title, title)
                    title_line.keepWithNext = bool(item.date or item.details)
                    story.append(title_line)
                    if item.date:
                        date_line = paragraph(item.date, date)
                        date_line.keepWithNext = bool(item.details)
                        story.append(date_line)
            elif item.date:
                story.append(paragraph(item.date, date))
            for detail in item.details:
                if compact_lines:
                    detail = re.sub(r"\s*\n\s*", " ", detail)
                story.append(paragraph(detail, body))
            story.append(Spacer(1, 4))
    return trim_spacers(story)


def header(document, job_title, config, margin):
    """Return exact header dimensions and its drawing routine."""
    layout = config["layout"]
    width = A4[0] - 2 * margin
    ink = colors.HexColor(config["color"])
    white = colors.white
    align = TA_CENTER if layout == "single" else 0
    name_style = ParagraphStyle(
        "name",
        fontName="ResumeCJKBold",
        fontSize=25,
        leading=34,
        alignment=align,
        textColor=white if layout == "sidebar" else ink,
        wordWrap="CJK",
    )
    small = ParagraphStyle(
        "contact",
        fontName="ResumeCJK",
        fontSize=9,
        leading=14,
        alignment=align,
        textColor=colors.HexColor("#d9e4ed")
        if layout == "sidebar"
        else colors.HexColor("#586674"),
        wordWrap="CJK",
    )
    target = ParagraphStyle(
        "target",
        parent=small,
        fontSize=10,
        leading=15,
        textColor=white if layout == "sidebar" else ink,
    )
    texts = [
        paragraph(document.name, name_style),
        paragraph("  |  ".join(document.contact), small),
    ]
    if job_title:
        texts.append(paragraph("求职意向：" + job_title, target))
    gap = 4
    start = 25 if layout == "sidebar" else 31
    # Header text is measured, so long names or position titles cannot overlap body.
    heights = [p.wrap(width, 10000)[1] for p in texts]
    bottom = start + sum(heights) + gap * (len(texts) - 1) + 12

    def draw(c):
        if layout == "sidebar":
            c.setFillColor(ink)
            c.rect(0, A4[1] - bottom, A4[0], bottom, fill=1, stroke=0)
            c.setFillColor(colors.HexColor("#38b9c5"))
            c.rect(0, A4[1] - bottom, A4[0], 3, fill=1, stroke=0)
        if layout == "balanced":
            c.setFillColor(ink)
            c.rect(
                margin - 12,
                A4[1] - bottom + 10,
                4,
                bottom - start - 9,
                fill=1,
                stroke=0,
            )
        y = A4[1] - start
        for p, h in zip(texts, heights):
            p.wrapOn(c, width, 10000)
            p.drawOn(c, margin, y - h)
            y -= h + gap
        if layout == "single":
            c.setStrokeColor(ink)
            c.setLineWidth(0.8)
            c.line(margin, A4[1] - bottom, A4[0] - margin, A4[1] - bottom)

    return bottom + 14, draw


def ordered_document(document, plan):
    result = document.model_copy(deep=True)
    for module, ids in (plan.order if plan else {}).items():
        ranks = {key: i for i, key in enumerate(ids)}
        result.modules[module].sort(key=lambda item: ranks.get(item.id, len(ranks)))
    if plan:
        for rows in result.modules.values():
            for item in rows:
                if item.id in plan.rewrites:
                    item.details = [bullet.text for bullet in plan.rewrites[item.id]]
    return result


def render_at_size(
    document, config, labels, selected, job_title, size, compact_lines=False
):
    compact = (10 - size) / 1.0
    margin = (42 if config["layout"] == "single" else 35) - compact * 5
    bottom = 34 - compact * 3
    width = A4[0] - margin * 2
    top, draw_header = header(document, job_title, config, margin)
    modules = [
        key for key in config["order"] if key in selected and document.modules[key]
    ]
    if not modules:
        raise ValueError("所选模块没有资料，请填写资料或勾选其他模块。")
    streams = []
    sidebar = None
    if config["layout"] == "single":
        streams.append(
            stream(
                sections_story(
                    document, modules, labels, width, config, size, compact_lines
                ),
                margin,
                width,
                top,
                bottom,
            )
        )
    elif config["layout"] == "sidebar":
        left = [
            key
            for key in modules
            if key in ("summary", "skills", "languages", "certificates", "awards")
        ]
        right = [key for key in modules if key not in left]
        if not left or not right:
            streams.append(
                stream(
                    sections_story(
                        document, modules, labels, width, config, size, compact_lines
                    ),
                    margin,
                    width,
                    top,
                    bottom,
                )
            )
        else:
            gap = 22
            left_width = width * 0.29
            right_width = width - left_width - gap
            sidebar = (margin - 10, left_width + 20)
            streams.append(
                stream(
                    sections_story(
                        document,
                        left,
                        labels,
                        left_width,
                        config,
                        max(9, size - 0.2),
                        compact_lines,
                    ),
                    margin,
                    left_width,
                    top,
                    bottom,
                )
            )
            streams.append(
                stream(
                    sections_story(
                        document,
                        right,
                        labels,
                        right_width,
                        config,
                        size,
                        compact_lines,
                    ),
                    margin + left_width + gap,
                    right_width,
                    top,
                    bottom,
                )
            )
    else:
        # Education and a short summary span the page, followed by balanced modules.
        featured = [key for key in ("education", "summary") if key in modules]
        prefix = sections_story(
            document, featured, labels, width, config, size, compact_lines
        )
        prefix_height = height_of(prefix, width)
        if prefix and prefix_height < 160:
            streams.append(stream(prefix, margin, width, top, bottom))
            top += prefix_height + 14
            modules = [key for key in modules if key not in featured]
        if len(modules) == 1:
            streams.append(
                stream(
                    sections_story(
                        document, modules, labels, width, config, size, compact_lines
                    ),
                    margin,
                    width,
                    top,
                    bottom,
                )
            )
            modules = []
        gap = 22
        column_width = (width - gap) / 2
        columns = [[], []]
        heights = [0.0, 0.0]
        for module in modules:
            block = sections_story(
                document, [module], labels, column_width, config, size, compact_lines
            )
            side = 0 if heights[0] <= heights[1] else 1
            columns[side].extend(block + [Spacer(1, 4)])
            heights[side] += height_of(block, column_width) + 4
        for i, column in enumerate(columns):
            if column:
                streams.append(
                    stream(
                        trim_spacers(column),
                        margin + i * (column_width + gap),
                        column_width,
                        top,
                        bottom,
                    )
                )
    streams = [s for s in streams if s is not None]
    count = max(len(s.pages) for s in streams)
    background = io.BytesIO()
    c = canvas.Canvas(background, pagesize=A4)
    for index in range(count):
        draw_header(c)
        if sidebar:
            c.setFillColor(colors.HexColor("#f0f4f8"))
            c.roundRect(
                sidebar[0],
                bottom - 7,
                sidebar[1],
                A4[1] - top - bottom + 14,
                6,
                fill=1,
                stroke=0,
            )
        c.setFont("ResumeCJK", 8)
        c.setFillColor(colors.HexColor("#82909d"))
        c.drawRightString(A4[0] - margin, 20, str(index + 1))
        c.showPage()
    c.save()
    base = PdfReader(io.BytesIO(background.getvalue()))
    writer = PdfWriter()
    for index, page in enumerate(base.pages):
        page = writer.add_page(page)
        for source in streams:
            if index < len(source.pages):
                page.merge_page(source.pages[index])
    writer.add_metadata(
        {
            "/Title": document.name + " - " + (job_title or "个人简历"),
            "/Author": document.name,
        }
    )
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue(), count


def create_pdf(
    document, config, labels, selected, job_title="", plan=None, one_page=True
):
    register_fonts()
    document = ordered_document(document, plan)
    attempts = []
    for size, compact_lines in (
        [(10.0, False), (10.0, True), (9.7, True), (9.4, True), (9.0, True)]
        if one_page
        else [(10.0, False)]
    ):
        data, pages = render_at_size(
            document, config, labels, selected, job_title, size, compact_lines
        )
        result = RenderedResume(
            data=data,
            pages=pages,
            body_font_size=size,
            one_page_preferred=one_page,
            compact_lines=compact_lines,
        )
        if pages == 1:
            result.data = export_compatible_pdf(result.data)
            return result
        attempts.append(result)
    # If one page is impossible, prefer fewer pages and then the larger readable font.
    result = min(attempts, key=lambda r: (r.pages, -r.body_font_size))
    result.data = export_compatible_pdf(result.data)
    return result
