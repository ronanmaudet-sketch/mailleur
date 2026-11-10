"""Build the five-page presentation from its current Markdown; no downloads.

Usage: /path/to/runtime/python3 documents/build_note.py
Outputs: the source basename + .pdf, .docx and .qa.json. The Markdown is read
afresh on every build. PDF and DOCX are checked against every source block.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html import escape
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata


BASE = Path(__file__).resolve().parent
NAVY, TEAL, LIGHT, GRAY = "19344A", "206A82", "EEF4F7", "637380"


def load_helpers():
    candidates = [BASE.parent / "repository/scripts/build_guide.py",
                  BASE.parents[1] / "atelier_mailleur/scripts/build_guide.py"]
    # Also work from repository/docs/cast3m/ in a normal GitHub clone.
    candidates.extend(ancestor / "scripts/build_guide.py" for ancestor in BASE.parents)
    for candidate in candidates:
        if candidate.is_file():
            spec = importlib.util.spec_from_file_location("mailleur_note_helpers", candidate)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
    raise RuntimeError("scripts/build_guide.py introuvable dans repository ou atelier_mailleur")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normal(text):
    return "".join(c.lower() for c in unicodedata.normalize("NFKD", text) if c.isalnum())


def typography(text):
    # Keep French closing punctuation with its preceding word; code stays literal.
    return re.sub(r" (?=[;:?!])", "\u00a0", text)


def block_texts(pages, helpers):
    return [[helpers.plain(cell) for block in blocks
             for cell in ([c for row in block.cells for c in row] if block.kind == "table" else [block.text])]
            for blocks in pages]


def build_pdf(pages, output, helpers, size):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Table, TableStyle, XPreformatted

    fonts = helpers.resolve_fonts()
    for name, key in (("NoteSans", "normal"), ("NoteBold", "bold"), ("NoteItalic", "italic"), ("NoteMono", "mono")):
        pdfmetrics.registerFont(TTFont(name, str(fonts[key] or fonts["normal"])))
    pdfmetrics.registerFontFamily("NoteSans", normal="NoteSans", bold="NoteBold", italic="NoteItalic", boldItalic="NoteBold")
    styles = {
        "body": ParagraphStyle("NoteBody", fontName="NoteSans", fontSize=size, leading=size*1.32,
                               textColor=colors.HexColor("#"+NAVY), spaceAfter=6.1, allowWidows=0, allowOrphans=0),
        "h1": ParagraphStyle("NoteH1", fontName="NoteBold", fontSize=18.5, leading=22.5,
                             textColor=colors.HexColor("#"+NAVY), spaceAfter=12, keepWithNext=True),
        "h2": ParagraphStyle("NoteH2", fontName="NoteBold", fontSize=11.7, leading=15.1,
                             textColor=colors.HexColor("#"+TEAL), spaceBefore=7, spaceAfter=5, keepWithNext=True),
        "code": ParagraphStyle("NoteCode", fontName="NoteMono", fontSize=8.2, leading=10.7,
                               textColor=colors.HexColor("#"+NAVY), backColor=colors.HexColor("#"+LIGHT),
                               borderPadding=7, leftIndent=7, rightIndent=7, spaceBefore=3, spaceAfter=10),
        "cell": ParagraphStyle("NoteCell", fontName="NoteSans", fontSize=9.0, leading=11.8,
                               textColor=colors.HexColor("#"+NAVY), spaceAfter=0),
        "cell_head": ParagraphStyle("NoteCellHead", fontName="NoteBold", fontSize=9.0, leading=11.8,
                                    textColor=colors.white, spaceAfter=0),
        "list": ParagraphStyle("NoteList", fontName="NoteSans", fontSize=size, leading=size*1.32,
                               textColor=colors.HexColor("#"+NAVY), leftIndent=12, bulletIndent=0,
                               bulletFontName="NoteSans", bulletFontSize=8, spaceAfter=4.8),
        "reference": ParagraphStyle("NoteReference", fontName="NoteSans", fontSize=8.5, leading=11.0,
                                    textColor=colors.HexColor("#"+GRAY), spaceAfter=4.5),
    }

    def rich(text):
        parts = []
        for kind, value, target in helpers.parts(text):
            if kind != "code": value = typography(value)
            # Fail on an unsupported glyph instead of silently inserting tofu.
            font = "NoteMono" if kind == "code" else "NoteSans"
            glyphs = pdfmetrics.getFont(font).face.charToGlyph
            bad = [c for c in value if c not in "\n\r\t" and not glyphs.get(ord(c))]
            if bad:
                raise ValueError(f"Glyphes absents de {font}: {set(bad)}")
            value = escape(value)
            if kind == "bold": parts.append(f"<b>{value}</b>")
            elif kind == "code": parts.append(f'<font name="NoteMono" size="8.5">{value}</font>')
            elif kind == "link": parts.append(f'<link href="{escape(target, quote=True)}" color="#{TEAL}">{value}</link>')
            else: parts.append(value)
        return "".join(parts)

    class NoteDocument(BaseDocTemplate):
        def afterFlowable(self, flowable):
            if hasattr(flowable, "note_bookmark"):
                self.canv.bookmarkPage(flowable.note_bookmark)
                self.canv.addOutlineEntry(flowable.getPlainText(), flowable.note_bookmark, level=0)

    width, height = A4
    left = right = 18*mm
    top, bottom = 19*mm, 17*mm

    def decorate(canvas, document):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#"+TEAL))
        canvas.rect(left, height-11.5*mm, 12*mm, 1.2*mm, stroke=0, fill=1)
        canvas.setFont("NoteBold", 7.5)
        canvas.setFillColor(colors.HexColor("#"+GRAY))
        canvas.drawString(left+15*mm, height-11.4*mm, "MAILLEUR  /  PROPOSITION DE PILOTE CAST3M")
        canvas.setStrokeColor(colors.HexColor("#D8E3E9"))
        canvas.line(left, 13*mm, width-right, 13*mm)
        canvas.setFont("NoteSans", 7.3)
        canvas.drawString(left, 8.7*mm, "Projet Mailleur · 10 novembre 2026")
        canvas.drawRightString(width-right, 8.7*mm, f"{document.page} / {len(pages)}")
        canvas.restoreState()

    document = NoteDocument(str(output), pagesize=A4, leftMargin=left, rightMargin=right,
                            topMargin=top, bottomMargin=bottom, title="Mailleur pour les utilisateurs de Cast3M",
                            author="Projet Mailleur", subject="Note de présentation et proposition de pilote")
    document.addPageTemplates(PageTemplate(id="note", frames=[Frame(left, bottom, width-left-right, height-top-bottom,
                               leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)], onPage=decorate))
    story = []
    for page_index, blocks in enumerate(pages):
        if page_index: story.append(PageBreak())
        for block in blocks:
            if block.kind == "heading":
                paragraph = Paragraph(rich(block.text), styles["h1" if block.level == 1 else "h2"])
                if block.level == 1: paragraph.note_bookmark = f"page_{page_index+1}"
                story.append(paragraph)
            elif block.kind == "table":
                weights = helpers.column_weights(block.cells)
                if page_index == 0: weights = [.23, .34, .43]
                elif page_index == 1: weights = [.29, .71]
                elif page_index == 2: weights = [.24, .12, .32, .13, .19]
                elif page_index == 3: weights = [.31, .69]
                data = [[Paragraph(rich(cell), styles["cell_head" if row_index == 0 else "cell"])
                         for cell in row] for row_index, row in enumerate(block.cells)]
                table = Table(data, colWidths=[(width-left-right)*weight for weight in weights], repeatRows=1, hAlign="LEFT")
                table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#"+NAVY)),
                    ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#"+LIGHT)]),
                    ("VALIGN", (0,0), (-1,-1), "TOP"), ("LEFTPADDING", (0,0), (-1,-1), 7),
                    ("RIGHTPADDING", (0,0), (-1,-1), 7), ("TOPPADDING", (0,0), (-1,-1), 6),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 6),
                    ("LINEBELOW", (0,-1), (-1,-1), .5, colors.HexColor("#D8E3E9"))]))
                table.spaceBefore, table.spaceAfter = 3, 9
                story.append(table)
            elif block.kind == "code":
                story.append(XPreformatted(escape(block.text), styles["code"]))
            elif block.kind == "list":
                story.append(Paragraph(rich(block.text), styles["list"], bulletText="•"))
            else:
                style = styles["reference"] if re.match(r"^\[\d+\] ", block.text) else styles["body"]
                story.append(Paragraph(rich(block.text), style))
    document.build(story)
    return {key: str(value) if value else None for key, value in fonts.items()}


def build_docx(pages, output, helpers, size):
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.enum.text import WD_BREAK, WD_LINE_SPACING, WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, RGBColor

    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin, section.bottom_margin = Mm(19), Mm(17)
    section.left_margin = section.right_margin = Mm(18)
    section.header_distance, section.footer_distance = Mm(8), Mm(8)
    normal_style = document.styles["Normal"]
    normal_style.font.name, normal_style.font.size = "Arial", Pt(size)
    normal_style.font.color.rgb = RGBColor.from_string(NAVY)
    normal_style.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    normal_style.paragraph_format.line_spacing = Pt(size*1.32)
    normal_style.paragraph_format.space_after = Pt(6.1)
    normal_style.paragraph_format.widow_control = True
    for level, font_size in ((1, 18.5), (2, 11.7)):
        style = document.styles[f"Heading {level}"]
        style.font.name, style.font.size = "Arial", Pt(font_size)
        style.font.color.rgb = RGBColor.from_string(NAVY if level == 1 else TEAL)
        style.paragraph_format.space_before = Pt(0 if level == 1 else 7)
        style.paragraph_format.space_after = Pt(12 if level == 1 else 5)
        style.paragraph_format.line_spacing = Pt(22.5 if level == 1 else 15.1)
        style.paragraph_format.keep_with_next = True
    properties = document.core_properties
    properties.title = "Mailleur pour les utilisateurs de Cast3M"
    properties.subject = "Note de présentation et proposition de pilote"
    properties.author = properties.last_modified_by = "Projet Mailleur"
    properties.created = datetime(2026, 11, 10, tzinfo=timezone.utc)
    properties.modified = datetime.now(timezone.utc)
    header = section.header.paragraphs[0]
    run = header.add_run("MAILLEUR  /  PROPOSITION DE PILOTE CAST3M")
    run.font.name, run.font.size, run.bold = "Arial", Pt(7.5), True
    run.font.color.rgb = RGBColor.from_string(TEAL)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = footer.add_run("Projet Mailleur · 10 novembre 2026     ")
    run.font.size = Pt(7.3)
    for instruction, suffix in (("PAGE", " / "), ("NUMPAGES", "")):
        field = OxmlElement("w:fldSimple"); field.set(qn("w:instr"), instruction)
        footer._p.append(field)
        footer.add_run(suffix).font.size = Pt(7.3)

    def fill(paragraph, text, small=False, header=False):
        for kind, value, target in helpers.parts(text):
            if kind != "code": value = typography(value)
            run = paragraph.add_run(value)
            run.font.name = "Courier New" if kind == "code" else "Arial"
            if small: run.font.size = Pt(9.0)
            elif kind == "code": run.font.size = Pt(8.5)
            run.bold = kind == "bold" or header
            if header: run.font.color.rgb = RGBColor(255,255,255)

    for page_index, blocks in enumerate(pages):
        if page_index: document.add_page_break()
        for block in blocks:
            if block.kind == "heading":
                paragraph = document.add_paragraph(style=f"Heading {min(2, block.level)}")
                fill(paragraph, block.text)
                for run in paragraph.runs: run.bold = True
            elif block.kind == "table":
                weights = helpers.column_weights(block.cells)
                if page_index == 0: weights = [.23, .34, .43]
                elif page_index == 1: weights = [.29, .71]
                elif page_index == 2: weights = [.24, .12, .32, .13, .19]
                elif page_index == 3: weights = [.31, .69]
                table = document.add_table(rows=0, cols=len(block.cells[0]))
                table.alignment, table.autofit = WD_TABLE_ALIGNMENT.LEFT, False
                for column, weight in zip(table.columns, weights): column.width = Mm(174*weight)
                for row_index, row in enumerate(block.cells):
                    cells = table.add_row().cells
                    tr_pr = table.rows[-1]._tr.get_or_add_trPr()
                    no_split = OxmlElement("w:cantSplit"); tr_pr.append(no_split)
                    if row_index == 0: tr_pr.append(OxmlElement("w:tblHeader"))
                    for cell, text, weight in zip(cells, row, weights):
                        cell.width = Mm(174*weight)
                        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
                        pr = cell._tc.get_or_add_tcPr()
                        shade = OxmlElement("w:shd"); shade.set(qn("w:fill"), NAVY if row_index == 0 else LIGHT if row_index%2==0 else "FFFFFF"); pr.append(shade)
                        margins = OxmlElement("w:tcMar")
                        for edge, value in (("top", 80), ("bottom", 80), ("left", 100), ("right", 100)):
                            part = OxmlElement("w:"+edge); part.set(qn("w:w"), str(value)); part.set(qn("w:type"), "dxa"); margins.append(part)
                        pr.append(margins)
                        paragraph = cell.paragraphs[0]
                        paragraph.paragraph_format.space_after = Pt(0)
                        paragraph.paragraph_format.line_spacing = Pt(11.8)
                        fill(paragraph, text, small=True, header=row_index == 0)
                after = document.add_paragraph(); after.paragraph_format.space_after = Pt(3); after.paragraph_format.line_spacing = Pt(3)
            elif block.kind == "code":
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.left_indent = Mm(2.5)
                paragraph.paragraph_format.space_after = Pt(9)
                paragraph.paragraph_format.line_spacing = Pt(10.7)
                paragraph.paragraph_format.keep_together = True
                shade = OxmlElement("w:shd"); shade.set(qn("w:fill"), LIGHT); paragraph._p.get_or_add_pPr().append(shade)
                run = paragraph.add_run(block.text); run.font.name, run.font.size = "Courier New", Pt(8.2)
            else:
                paragraph = document.add_paragraph()
                if block.kind == "list":
                    paragraph.paragraph_format.left_indent = Mm(4)
                    paragraph.paragraph_format.first_line_indent = Mm(-4)
                    paragraph.paragraph_format.space_after = Pt(4.8)
                    paragraph.add_run("• ")
                fill(paragraph, block.text)
                if re.match(r"^\[\d+\] ", block.text):
                    paragraph.paragraph_format.line_spacing = Pt(11)
                    paragraph.paragraph_format.space_after = Pt(4.5)
                    for run in paragraph.runs: run.font.size = Pt(8.5)
    document.save(output)


def check_pdf(path, expected_pages, expected_texts):
    import pdfplumber
    from pypdf import PdfReader
    reader = PdfReader(path)
    missing, outside, page_stats = [], [], []
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) != expected_pages:
            raise ValueError(f"{path.name}: {len(pdf.pages)} pages au lieu de {expected_pages}")
        for index, page in enumerate(pdf.pages):
            text = reader.pages[index].extract_text()
            compact = normal(text)
            for part in expected_texts[index]:
                if normal(part) and normal(part) not in compact: missing.append({"page":index+1,"text":part[:160]})
            for char in page.chars:
                if char.get("text", "").strip() and (char["x0"] < 0 or char["x1"] > page.width+.2 or char["top"] < 0 or char["bottom"] > page.height+.2):
                    outside.append({"page":index+1,"text":char["text"],"bbox":[char["x0"],char["top"],char["x1"],char["bottom"]]})
            body = [c for c in page.chars if c["top"] > 45 and c["bottom"] < page.height-45]
            page_stats.append({"page":index+1,"characters":len(page.chars),"body_bottom_points":max((c["bottom"] for c in body),default=None)})
    if missing or outside: raise ValueError(json.dumps({"missing_source_blocks":missing,"out_of_page_characters":outside},ensure_ascii=False))
    return {"pages":len(reader.pages),"missing_source_blocks":[],"out_of_page_characters":[],"page_statistics":page_stats}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=BASE/"PROPOSITION_CAST3M.md")
    parser.add_argument("--font-size", type=float, default=9.8)
    parser.add_argument("--skip-docx-render", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve(); before = digest(source)
    helpers = load_helpers()
    sections = re.split(r"\s*<!--\s*PAGEBREAK\s*-->\s*", source.read_text(encoding="utf-8"))
    if len(sections) != 5: raise ValueError("La note doit contenir exactement quatre marqueurs PAGEBREAK")
    pages = [helpers.parse_markdown(text, source.name, main=True) for text in sections]
    expected = block_texts(pages, helpers)
    pdf, docx = source.with_suffix(".pdf"), source.with_suffix(".docx")
    fonts = build_pdf(pages, pdf, helpers, args.font_size)
    build_docx(pages, docx, helpers, args.font_size)
    result = {"generated_utc":datetime.now(timezone.utc).isoformat(),"author":"Projet Mailleur",
              "source_sha256":before,"body_font_size":args.font_size,"fonts":fonts,
              "pdf":check_pdf(pdf,5,expected),"docx":{"explicit_page_breaks":4}}
    from docx import Document
    from docx.oxml.ns import qn
    document = Document(docx)
    compact = normal("".join(document.element.itertext()))
    missing = [text for page in expected for text in page if normal(text) and normal(text) not in compact]
    if missing: raise ValueError("Blocs absents du DOCX : "+str(missing))
    result["docx"]["missing_source_blocks"] = []
    result["docx"]["author"] = document.core_properties.author
    if not args.skip_docx_render:
        soffice = shutil.which("soffice")
        if not soffice: raise ValueError("soffice absent ; utiliser --skip-docx-render uniquement pour une reconstruction provisoire")
        with tempfile.TemporaryDirectory(prefix="mailleur-note-docx-") as temporary:
            root = Path(temporary)
            process = subprocess.run([soffice,"-env:UserInstallation="+(root/"profile").as_uri(),"--headless","--convert-to","pdf","--outdir",str(root),str(docx)],capture_output=True,text=True,timeout=90)
            rendered = root/pdf.name
            if process.returncode or not rendered.is_file(): raise ValueError("Rendu DOCX impossible : "+process.stdout+process.stderr)
            result["docx"]["libreoffice_render"] = check_pdf(rendered,5,expected)
    if digest(source) != before: raise ValueError("Le Markdown a changé pendant la reconstruction ; relancer la commande")
    result["files"] = {path.name:{"bytes":path.stat().st_size,"sha256":digest(path)} for path in (source,pdf,docx)}
    source.with_suffix(".qa.json").write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__ == "__main__":
    main()
