"""Exporter les sources Markdown en Word/PDF, sans téléchargement.

Par défaut : guide public rc7 et quatre annexes, dont VALIDATION_RC7.md requis.
Installer les outils documentaires avec requirements-docs.txt ; ils ne sont
pas nécessaires au fonctionnement du mailleur. --historical-rc5 réutilise les
sources de l'ancienne collection ; il ne restaure pas des versions effacées.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import json
import math
import os
from pathlib import Path
import re
import unicodedata


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_SOURCES = (
    ("GUIDE_COMPLET_MAILLEUR.md", "Guide principal", "guide"),
    ("MESURES_M3.md", "Annexe A — Mesures sur Apple M3", "mesures"),
    ("API_UTILISATION.md", "Annexe B — Utiliser l’API", "api"),
    ("TAILLES_ET_CAPACITES.md", "Annexe C — Tailles et capacités", "tailles"),
)
PUBLIC_SOURCES = (
    ("UTILISATION_ET_DEVELOPPEMENT.md", "Guide public", "guide"),
    ("FORMATS.md", "Annexe A — Formats et exports solveurs", "formats"),
    ("IMPORTS_NATIFS.md", "Annexe B — Imports natifs et passerelles", "imports"),
    ("PUBLICATION.md", "Annexe C — Publication et contribution", "publication"),
    ("VALIDATION_RC7.md", "Annexe D — Vérifications étendues de la rc7", "validation"),
)
NAVY = "16334F"
BLUE = "246E9A"
PALE = "EEF4F8"
GRAY = "596875"
INLINE = re.compile(r"(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))")


@dataclass(frozen=True)
class GuideProfile:
    sources: tuple
    title: str
    subtitle: str
    version: str
    revision: str
    basename: str
    qa_name: str
    manifest_name: str
    cover_note: str
    introduction: str
    toc_note: str


PUBLIC_PROFILE = GuideProfile(
    PUBLIC_SOURCES, "Utiliser, comprendre\net développer Mailleur",
    "Imports CAO et maillages, exports solveurs, API et publication",
    "1.0.0rc7", "7 novembre 2026", "Mailleur_Guide_public_rc7",
    "verification_texte_pdf_rc7.json", "sources_et_verification_rc7.json",
    "Formats 2020+ : cible d’échange, périmètres documentés",
    "Guide public destiné aux utilisateurs et contributeurs. Les formats intégrés, les passerelles conditionnelles et les validations externes sont distingués explicitement.",
    "Les liens et signets rejoignent les chapitres. Les annexes regroupent les formats, les passerelles natives, la publication et les preuves de validation de cette révision. Les fichiers Markdown restent les sources éditables de référence.",
)
HISTORICAL_PROFILE = GuideProfile(
    HISTORICAL_SOURCES, "Comprendre, utiliser\net partager Mailleur",
    "Fonctionnement, architecture, apprentissage, API et diffusion",
    "1.0.0rc5", "4 novembre 2026", "Mailleur_Guide_complet",
    "verification_texte_pdf.json", "sources_et_verification.json",
    "Mesures sur Apple M3, 16 Go",
    "Guide destiné à comprendre le projet, démarrer sur cet ordinateur, utiliser l’API et préparer une diffusion reproductible.",
    "Les liens et signets rejoignent les chapitres. Les annexes regroupent les mesures, les exemples d’API et les tailles. Les fichiers Markdown restent les sources éditables de référence.",
)


@dataclass
class Block:
    kind: str
    text: str = ""
    level: int = 0
    marker: str = ""
    cells: list[list[str]] = field(default_factory=list)
    source: str = ""
    key: str = ""


def parts(text):
    """Jetons inline simples ; aucune exécution HTML ou Markdown."""
    for token in INLINE.split(text):
        if not token:
            continue
        if token.startswith("`") and token.endswith("`"):
            yield "code", token[1:-1], None
        elif token.startswith("**") and token.endswith("**"):
            yield "bold", token[2:-2], None
        elif token.startswith("[") and "](" in token:
            label, target = token[1:].split("](", 1)
            yield "link", label, target[:-1]
        else:
            yield "text", token, None


def plain(text):
    return "".join(value for _, value, _ in parts(text))


def table_row(line):
    return [cell.strip().replace(r"\|", "|") for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def parse_markdown(text, source, main=False):
    lines = text.splitlines()
    blocks = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line.startswith("```"):
            language = line[3:].strip()
            index += 1
            code = []
            while index < len(lines) and not lines[index].startswith("```"):
                code.append(lines[index])
                index += 1
            if index >= len(lines):
                raise ValueError(f"Bloc de code non fermé : {source}")
            blocks.append(Block("code", "\n".join(code), marker=language, source=source))
            index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            level = len(heading[1])
            if level != 1:  # Le titre de chaque source est déjà présenté.
                mapped = max(1, level - 1) if main else level
                blocks.append(Block("heading", heading[2].strip(), level=mapped, source=source))
            index += 1
            continue
        if line.lstrip().startswith("|") and index + 1 < len(lines) and re.match(r"^\s*\|?[\s:|\-]+\|\s*$", lines[index + 1]):
            rows = [table_row(line)]
            index += 2
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                rows.append(table_row(lines[index]))
                index += 1
            width = len(rows[0])
            if any(len(row) != width for row in rows):
                raise ValueError(f"Tableau irrégulier dans {source}")
            blocks.append(Block("table", cells=rows, source=source))
            continue
        item = re.match(r"^\s*(?:([-*])\s+|(\d+[.)])\s+)(.+)$", line)
        if item:
            blocks.append(Block("list", item[3], marker=item[2] or "•", source=source))
            index += 1
            continue
        if re.match(r"^\s*([-*_])\1{2,}\s*$", line):
            index += 1
            continue
        paragraph = [line.lstrip("> ") if line.startswith(">") else line.strip()]
        index += 1
        while index < len(lines) and lines[index].strip():
            next_line = lines[index]
            if re.match(r"^(#{1,6})\s|^```|^\s*\||^\s*(?:[-*]|\d+[.)])\s", next_line):
                break
            paragraph.append(next_line.strip().lstrip("> "))
            index += 1
        blocks.append(Block("paragraph", " ".join(paragraph), source=source))
    return blocks


def load_blocks(profile=PUBLIC_PROFILE, source_directory=None):
    source_directory = Path(source_directory) if source_directory is not None else ROOT / "docs"
    missing = [name for name, _, _ in profile.sources if not (source_directory / name).is_file()]
    if missing:
        raise ValueError("Sources manquantes ; aucun document produit : " + ", ".join(missing))
    blocks = []
    for index, (name, title, key) in enumerate(profile.sources):
        if index:
            blocks.append(Block("heading", title, level=1, source=name, key=key))
        parsed = parse_markdown((source_directory / name).read_text(encoding="utf-8"), name, main=index == 0)
        blocks.extend(parsed)
    count = 0
    for block in blocks:
        if block.kind == "heading" and not block.key:
            count += 1
            block.key = f"section_{count}"
    chapters = [block for block in blocks if block.kind == "heading" and block.level == 1]
    if not chapters:
        raise ValueError("Les sources ne contiennent aucun chapitre exportable")
    return blocks, chapters


def resolved_link(target, source, output, profile=PUBLIC_PROFILE, source_directory=None):
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
        return target
    path_part = target.split("#", 1)[0]
    for filename, _, key in profile.sources[1:]:
        if Path(path_part).name == filename:
            return "#" + key
    if Path(path_part).name == profile.sources[0][0]:
        return "#section_1"
    if not path_part:
        return None
    source_directory = Path(source_directory) if source_directory is not None else ROOT / "docs"
    path = (source_directory / source).parent / path_part
    return os.path.relpath(path.resolve(), output.resolve()).replace(os.sep, "/")


def source_counts(chapters, profile):
    main = profile.sources[0][0]
    return sum(chapter.source == main for chapter in chapters), len(profile.sources) - 1


def resolve_fonts(directory=None):
    """Chercher les polices installées ; Vera de ReportLab reste le repli libre."""
    import reportlab
    candidates = []
    if directory is not None:
        candidates.append(Path(directory))
    else:
        candidates.extend((Path("/System/Library/Fonts/Supplemental"),
                           Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                           Path("/usr/share/fonts/truetype/dejavu"),
                           Path("/usr/share/fonts/dejavu"),
                           Path("/usr/share/fonts/truetype/liberation2")))
    candidates.append(Path(reportlab.__file__).parent / "fonts")
    families = (
        ("Arial.ttf", "Arial Bold.ttf", "Arial Italic.ttf", "Courier New.ttf"),
        ("arial.ttf", "arialbd.ttf", "ariali.ttf", "cour.ttf"),
        ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSansMono.ttf"),
        ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf", "LiberationMono-Regular.ttf"),
        ("Vera.ttf", "VeraBd.ttf", "VeraIt.ttf", None),
    )
    for folder in candidates:
        for normal, bold, italic, mono in families:
            if all((folder / file).is_file() for file in (normal, bold, italic)):
                return {"normal": folder / normal, "bold": folder / bold, "italic": folder / italic,
                        "mono": folder / mono if mono and (folder / mono).is_file() else None}
    raise ValueError("Aucune police utilisable : indiquer --fonts avec Arial, DejaVu ou Liberation Sans")


def column_weights(rows):
    columns = len(rows[0])
    scores = [math.sqrt(max(14, min(90, max(len(plain(row[index])) for row in rows)))) for index in range(columns)]
    total = sum(scores)
    return [score / total for score in scores]


def build_pdf(blocks, chapters, output, revision, fonts, profile=PUBLIC_PROFILE, source_directory=None):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.pdfmetrics import Font
    from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle, XPreformatted
    from reportlab.platypus.tableofcontents import TableOfContents

    if not isinstance(fonts, dict):
        fonts = resolve_fonts(fonts)
    for name, key in (("GuideArial", "normal"), ("GuideArialBold", "bold"), ("GuideArialItalic", "italic")):
        pdfmetrics.registerFont(TTFont(name, str(fonts[key])))
    if fonts["mono"] is not None:
        pdfmetrics.registerFont(TTFont("GuideMono", str(fonts["mono"])))
    else:
        pdfmetrics.registerFont(Font("GuideMono", "Courier", "WinAnsiEncoding"))
    pdfmetrics.registerFontFamily("GuideArial", normal="GuideArial", bold="GuideArialBold", italic="GuideArialItalic", boldItalic="GuideArialBold")
    pdfmetrics.registerFontFamily("GuideMono", normal="GuideMono", bold="GuideMono", italic="GuideMono", boldItalic="GuideMono")
    substitutions = Counter()
    # Les schémas restent lisibles avec les seuls Vera/Courier : on remplace
    # leurs glyphes absents par des équivalents ASCII, jamais par des carrés.
    ascii_equivalents = {"→": "->", "←": "<-", "↔": "<->", "⇒": "=>",
                         "│": "|", "─": "-", "└": "+", "├": "+", "┌": "+",
                         "┐": "+", "┘": "+", "┤": "+", "┬": "+", "┴": "+", "┼": "+"}

    def printable(text, font_name):
        face = pdfmetrics.getFont(font_name).face
        mapping = getattr(face, "charToGlyph", None)
        rendered = []
        for char in text:
            if char in "\n\r\t":
                rendered.append(char)
                continue
            if mapping is not None:
                supported = bool(mapping.get(ord(char)))
            else:
                try:
                    char.encode("cp1252")
                    supported = True
                except UnicodeEncodeError:
                    supported = False
            if supported:
                rendered.append(char)
            elif char in ascii_equivalents:
                replacement = ascii_equivalents[char]
                substitutions[char + " -> " + replacement] += 1
                rendered.append(replacement)
            else:
                raise ValueError(f"La police {font_name} ne couvre pas U+{ord(char):04X} ; choisir --fonts avec Arial ou DejaVu")
        return "".join(rendered)

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("GuideBody", fontName="GuideArial", fontSize=9.3, leading=13.4,
                             textColor=colors.HexColor("#" + NAVY), spaceAfter=7, alignment=TA_LEFT,
                             splitLongWords=True, allowWidows=0, allowOrphans=0))
    styles.add(ParagraphStyle("GuideH1", parent=styles["GuideBody"], fontName="GuideArialBold", fontSize=18,
                             leading=22, spaceAfter=15, keepWithNext=True))
    styles.add(ParagraphStyle("GuideH2", parent=styles["GuideBody"], fontName="GuideArialBold", fontSize=12.2,
                             leading=16, spaceBefore=12, spaceAfter=7, keepWithNext=True))
    styles.add(ParagraphStyle("GuideH3", parent=styles["GuideH2"], fontSize=10.3, leading=14))
    styles.add(ParagraphStyle("GuideCode", fontName="GuideMono", fontSize=7.25, leading=9.25,
                             textColor=colors.HexColor("#" + NAVY), backColor=colors.HexColor("#F1F4F6"),
                             borderPadding=6, leftIndent=6, rightIndent=6, spaceBefore=5, spaceAfter=10))
    styles.add(ParagraphStyle("GuideCell", parent=styles["GuideBody"], fontSize=7.9, leading=10.5, spaceAfter=0))
    styles.add(ParagraphStyle("GuideCellHead", parent=styles["GuideCell"], fontName="GuideArialBold", textColor=colors.white))
    styles.add(ParagraphStyle("GuideList", parent=styles["GuideBody"], leftIndent=14, firstLineIndent=0,
                             bulletIndent=1, bulletFontName="GuideArial", bulletFontSize=9, spaceAfter=4))
    styles.add(ParagraphStyle("GuideTOC", parent=styles["GuideBody"], fontSize=10.1, leading=16,
                             leftIndent=0, firstLineIndent=0, spaceBefore=7))

    def rich(text, source, cell=False, header=False):
        result = []
        for kind, value, target in parts(text):
            value = escape(printable(value, "GuideMono" if kind == "code" else "GuideArial"))
            if kind == "code":
                result.append(f'<font name="GuideMono" size="{7.1 if cell else 8.25}">{value}</font>')
            elif kind == "bold":
                result.append(f"<b>{value}</b>")
            elif kind == "link":
                href = resolved_link(target, source, output, profile, source_directory)
                if href:
                    result.append(f'<link href="{escape(href, quote=True)}" color="{("#FFFFFF" if header else "#246E9A")}"><u>{value}</u></link>')
                else:
                    result.append(value)
            else:
                result.append(value)
        return "".join(result)

    class GuidePDF(BaseDocTemplate):
        def afterFlowable(self, flowable):
            if hasattr(flowable, "guide_key"):
                key = flowable.guide_key
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(flowable.getPlainText(), key, level=flowable.guide_level - 1, closed=False)
                if flowable.guide_level == 1:
                    self.notify("TOCEntry", (0, flowable.getPlainText(), self.page, key))

    def page(canvas, doc):
        canvas.saveState()
        if doc.page == 1:
            canvas.setFillColor(colors.HexColor("#" + NAVY))
            canvas.rect(0, A4[1] - 18 * mm, A4[0], 18 * mm, fill=1, stroke=0)
            canvas.setFillColor(colors.HexColor("#" + BLUE))
            canvas.rect(20 * mm, 30 * mm, 35 * mm, 2 * mm, fill=1, stroke=0)
        else:
            canvas.setFont("GuideArial", 7.2)
            canvas.setFillColor(colors.HexColor("#" + GRAY))
            canvas.drawString(19 * mm, A4[1] - 13 * mm, f"MAILLEUR {profile.version}  •  GUIDE D’UTILISATION ET DE DÉVELOPPEMENT")
            canvas.setStrokeColor(colors.HexColor("#D7E2EA"))
            canvas.line(19 * mm, A4[1] - 16 * mm, A4[0] - 19 * mm, A4[1] - 16 * mm)
            canvas.drawString(19 * mm, 12 * mm, f"Révision du {revision}  •  Sources Markdown incluses dans le dépôt")
            canvas.drawRightString(A4[0] - 19 * mm, 12 * mm, str(doc.page))
        canvas.restoreState()

    chapter_count, appendix_count = source_counts(chapters, profile)
    path = output / (profile.basename + ".pdf")
    doc = GuidePDF(str(path), pagesize=A4, rightMargin=19 * mm, leftMargin=19 * mm,
                   topMargin=23 * mm, bottomMargin=21 * mm, title=profile.title.replace("\n", " "),
                   subject=profile.subtitle)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id="Guide", frames=[frame], onPage=page))
    story = [Spacer(1, 43 * mm),
             Paragraph("ATELIER MAILLEUR", ParagraphStyle("CoverLabel", fontName="GuideArialBold", fontSize=12, leading=16, textColor=colors.HexColor("#" + BLUE), spaceAfter=20)),
             Paragraph(profile.title.replace("\n", "<br/>"), ParagraphStyle("CoverTitle", fontName="GuideArialBold", fontSize=30, leading=38, textColor=colors.HexColor("#" + NAVY), spaceAfter=20)),
             Paragraph(profile.subtitle, ParagraphStyle("CoverSubtitle", parent=styles["GuideBody"], fontSize=14, leading=20)),
             Spacer(1, 20 * mm), Paragraph(f"Version {profile.version}", styles["GuideH2"]),
             Paragraph(f"{escape(revision)}<br/>{chapter_count} chapitres et {appendix_count} annexes<br/>{escape(profile.cover_note)}", styles["GuideBody"]),
             Spacer(1, 13 * mm), Paragraph(profile.introduction, styles["GuideBody"]),
             PageBreak(), Paragraph("Sommaire", styles["GuideH1"])]
    toc = TableOfContents()
    toc.levelStyles = [styles["GuideTOC"]]
    toc.dotsMinLevel = 0
    story.extend([toc, Spacer(1, 12 * mm),
                  Paragraph(profile.toc_note, styles["GuideBody"])])
    for block in blocks:
        if block.kind == "heading":
            if block.level == 1:
                story.append(PageBreak())
            paragraph = Paragraph(rich(block.text, block.source), styles[f"GuideH{min(block.level, 3)}"])
            paragraph.guide_key = block.key
            paragraph.guide_level = min(block.level, 2)
            story.append(paragraph)
        elif block.kind == "paragraph":
            story.append(Paragraph(rich(block.text, block.source), styles["GuideBody"]))
        elif block.kind == "list":
            story.append(Paragraph(rich(block.text, block.source), styles["GuideList"], bulletText=block.marker))
        elif block.kind == "code":
            displayed_code = printable(block.text, "GuideMono")
            longest = max((pdfmetrics.stringWidth(line, "GuideMono", 1) for line in displayed_code.splitlines()), default=1)
            size = min(7.25, (doc.width - 14) / max(1, longest))
            code_style = ParagraphStyle("AdaptiveCode", parent=styles["GuideCode"], fontSize=size, leading=size * 1.28)
            code = escape(displayed_code)
            if fonts["mono"] is None:
                # Courier standard ne couvre pas les flèches et caractères de
                # schéma Unicode. Vera/Sans les dessine sans changer le texte.
                code = "".join(f'<font name="GuideArial">{escape(char)}</font>' if ord(char) > 255 else escape(char)
                               for char in displayed_code)
            story.append(XPreformatted(code, code_style))
        elif block.kind == "table":
            rows = [[Paragraph(rich(cell, block.source, cell=True, header=row_index == 0),
                               styles["GuideCellHead" if row_index == 0 else "GuideCell"])
                     for cell in row] for row_index, row in enumerate(block.cells)]
            table = Table(rows, colWidths=[doc.width * value for value in column_weights(block.cells)], repeatRows=1, hAlign="LEFT")
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + NAVY)),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#F0F5F8"), colors.white]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, 0), .5, colors.HexColor("#" + NAVY)),
                ("LINEBELOW", (0, 1), (-1, -1), .25, colors.HexColor("#D7E2EA")),
            ]))
            story.extend([table, Spacer(1, 9)])
    doc.multiBuild(story)
    fonts["substitutions"] = dict(substitutions)
    return path


def build_docx(blocks, chapters, output, revision, profile=PUBLIC_PROFILE, source_directory=None):
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, RGBColor
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin, section.bottom_margin = Mm(21), Mm(21)
    section.left_margin, section.right_margin = Mm(20), Mm(20)
    section.header_distance, section.footer_distance = Mm(9), Mm(9)
    section.different_first_page_header_footer = True
    normal = document.styles["Normal"]
    normal.font.name, normal.font.size = "Arial", Pt(10)
    normal.font.color.rgb = RGBColor.from_string(NAVY)
    normal.paragraph_format.space_after, normal.paragraph_format.line_spacing = Pt(7), 1.12
    for level, size in ((1, 20), (2, 13), (3, 11)):
        style = document.styles[f"Heading {level}"]
        style.font.name, style.font.size = "Arial", Pt(size)
        style.font.color.rgb = RGBColor.from_string(NAVY if level == 1 else BLUE)
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.space_before, style.paragraph_format.space_after = Pt(13), Pt(8)
    document.core_properties.title = profile.title.replace("\n", " ")
    document.core_properties.subject = profile.subtitle
    document.core_properties.author = ""

    header = section.header.paragraphs[0]
    header.add_run(f"MAILLEUR {profile.version}  •  UTILISATION ET DÉVELOPPEMENT").font.size = Pt(7)
    header.runs[0].font.color.rgb = RGBColor.from_string(GRAY)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run(f"Révision du {revision}  •  Page ").font.size = Pt(7)
    number = OxmlElement("w:fldSimple")
    number.set(qn("w:instr"), "PAGE")
    footer._p.append(number)

    def hyperlink(paragraph, text, target):
        element = OxmlElement("w:hyperlink")
        if target.startswith("#"):
            element.set(qn("w:anchor"), target[1:])
        else:
            element.set(qn("r:id"), paragraph.part.relate_to(target, RT.HYPERLINK, is_external=True))
        run = OxmlElement("w:r")
        properties = OxmlElement("w:rPr")
        color = OxmlElement("w:color")
        color.set(qn("w:val"), BLUE)
        properties.append(color)
        underline = OxmlElement("w:u")
        underline.set(qn("w:val"), "single")
        properties.append(underline)
        run.append(properties)
        value = OxmlElement("w:t")
        value.text = text
        run.append(value)
        element.append(run)
        paragraph._p.append(element)

    def fill(paragraph, text, source, small=False, header=False):
        for kind, value, target in parts(text):
            if kind == "link":
                href = resolved_link(target, source, output, profile, source_directory)
                if href:
                    hyperlink(paragraph, value, href)
                    continue
            run = paragraph.add_run(value)
            run.font.name = "Courier New" if kind == "code" else "Arial"
            if small:
                run.font.size = Pt(7.7 if kind == "code" else 8.2)
            elif kind == "code":
                run.font.size = Pt(9)
            run.bold = kind == "bold" or header
            if header:
                run.font.color.rgb = RGBColor(255, 255, 255)

    def shade(cell, value):
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), value)
        cell._tc.get_or_add_tcPr().append(shading)

    cover = document.add_paragraph()
    cover.paragraph_format.space_before = Mm(37)
    label = cover.add_run("ATELIER MAILLEUR")
    label.bold, label.font.size, label.font.color.rgb = True, Pt(13), RGBColor.from_string(BLUE)
    cover = document.add_paragraph()
    cover.paragraph_format.space_after = Pt(22)
    run = cover.add_run(profile.title)
    run.bold, run.font.size = True, Pt(32)
    document.add_paragraph(profile.subtitle).runs[0].font.size = Pt(15)
    document.add_paragraph(f"Version {profile.version}").paragraph_format.space_before = Mm(21)
    chapter_count, appendix_count = source_counts(chapters, profile)
    document.add_paragraph(f"{revision}\n{chapter_count} chapitres et {appendix_count} annexes\n{profile.cover_note}")
    document.add_paragraph(profile.introduction)
    document.add_page_break()
    document.add_paragraph("Sommaire", style="Title")
    explanation = document.add_paragraph("Le sommaire est cliquable. Word peut le mettre à jour avec les numéros de page : clic droit sur le sommaire, puis « Mettre à jour les champs ». Les fichiers Markdown restent les sources éditables de référence.")
    explanation.runs[0].font.size = Pt(9)
    field_paragraph = document.add_paragraph()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = ' TOC \\o "1-1" \\h \\z \\u '
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    for element in (begin, instruction, separate):
        run = OxmlElement("w:r")
        run.append(element)
        field_paragraph._p.append(run)
    for chapter in chapters:
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_after = Pt(6)
        hyperlink(paragraph, plain(chapter.text), "#" + chapter.key)
    end_paragraph = document.add_paragraph()
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run = OxmlElement("w:r")
    run.append(end)
    end_paragraph._p.append(run)
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    document.settings.element.append(update)

    bookmark_id = 0
    for block in blocks:
        if block.kind == "heading":
            if block.level == 1:
                document.add_page_break()
            paragraph = document.add_paragraph(style=f"Heading {min(block.level, 3)}")
            bookmark_id += 1
            begin = OxmlElement("w:bookmarkStart")
            begin.set(qn("w:id"), str(bookmark_id))
            begin.set(qn("w:name"), block.key)
            paragraph._p.append(begin)
            fill(paragraph, block.text, block.source)
            end = OxmlElement("w:bookmarkEnd")
            end.set(qn("w:id"), str(bookmark_id))
            paragraph._p.append(end)
        elif block.kind in ("paragraph", "list"):
            paragraph = document.add_paragraph()
            if block.kind == "list":
                paragraph.paragraph_format.left_indent = Mm(5)
                paragraph.paragraph_format.first_line_indent = Mm(-4)
                paragraph.add_run(block.marker + " ")
            fill(paragraph, block.text, block.source)
        elif block.kind == "code":
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.left_indent = Mm(2)
            paragraph.paragraph_format.right_indent = Mm(2)
            longest = max((len(line) for line in block.text.splitlines()), default=1)
            size = min(7.5, (166 * 72 / 25.4) / max(1, longest * .6))
            paragraph.paragraph_format.line_spacing = Pt(size * 1.28)
            paragraph.paragraph_format.widow_control = False
            shading = OxmlElement("w:shd")
            shading.set(qn("w:fill"), "F1F4F6")
            paragraph._p.get_or_add_pPr().append(shading)
            run = paragraph.add_run(block.text)
            run.font.name, run.font.size = "Courier New", Pt(size)
        elif block.kind == "table":
            table = document.add_table(rows=1, cols=len(block.cells[0]))
            table.autofit = False
            widths = column_weights(block.cells)
            for column, weight in zip(table.columns, widths):
                column.width = Mm(170 * weight)
            for index, row in enumerate(block.cells):
                cells = table.rows[0].cells if index == 0 else table.add_row().cells
                if index == 0:
                    repeat = OxmlElement("w:tblHeader")
                    table.rows[0]._tr.get_or_add_trPr().append(repeat)
                for cell, value, weight in zip(cells, row, widths):
                    cell.width = Mm(170 * weight)
                    paragraph = cell.paragraphs[0]
                    paragraph.paragraph_format.space_after = Pt(3)
                    paragraph.paragraph_format.space_before = Pt(3)
                    fill(paragraph, value, block.source, small=True, header=index == 0)
                    shade(cell, NAVY if index == 0 else PALE if index % 2 else "FFFFFF")
            document.add_paragraph().paragraph_format.space_after = Pt(2)
    path = output / (profile.basename + ".docx")
    document.save(path)
    return path


def validate_pdf(path, blocks, chapters, output, profile=PUBLIC_PROFILE):
    from pypdf import PdfReader
    reader = PdfReader(path)
    pages = [page.extract_text() or "" for page in reader.pages]
    extracted = "\n".join(pages)
    compact = lambda text: re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).replace("\u200b", "")
    all_text = compact(extracted)
    missing_headings = [plain(chapter.text) for chapter in chapters if compact(plain(chapter.text)) not in all_text]
    expected = Counter()
    for block in blocks:
        strings = [value for row in block.cells for value in row] if block.kind == "table" else [block.text]
        for value in strings:
            expected.update(re.findall(r"[\wÀ-ÿ]{6,}", plain(value).casefold()))
    actual = set(re.findall(r"[\wÀ-ÿ]{6,}", extracted.casefold().replace("\u200b", "")))
    missing = sorted(word for word in expected if word not in actual)
    coverage = 1 - sum(expected[word] for word in missing) / max(1, sum(expected.values()))
    result = {"pages": len(reader.pages), "chapters_and_appendices": len(chapters),
              "missing_headings": missing_headings, "text_token_coverage": coverage,
              "missing_words": missing, "extracted_characters": len(extracted)}
    (output / profile.qa_name).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if missing_headings or coverage < .98:
        raise ValueError("La vérification textuelle du PDF a échoué ; consulter " + profile.qa_name)
    return result


def validate_docx(path, blocks, chapters):
    from docx import Document
    document = Document(path)
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    expected_code = Counter(block.text for block in blocks if block.kind == "code")
    found_code = Counter(text for text in paragraphs if text in expected_code)
    missing_code = [text for text, count in expected_code.items() if found_code[text] < count]
    missing_headings = [plain(chapter.text) for chapter in chapters if plain(chapter.text) not in paragraphs]
    expected_tables = sum(block.kind == "table" for block in blocks)
    result = {"tables": len(document.tables), "expected_tables": expected_tables,
              "code_blocks": sum(expected_code.values()), "code_blocks_preserved": not missing_code,
              "missing_headings": missing_headings}
    if missing_code or missing_headings or len(document.tables) != expected_tables:
        raise ValueError("Le document Word ne conserve pas tous les titres, tableaux ou blocs de code")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "livrables")
    parser.add_argument("--fonts", type=Path, help="dossier Arial, DejaVu ou Liberation ; sinon détection et repli Vera/Courier")
    parser.add_argument("--revision", help="date affichée ; défaut : date de la collection choisie")
    parser.add_argument("--source-directory", type=Path, default=ROOT / "docs", help="sources Markdown, éventuellement archivées")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--public-release", action="store_true", help="guide public rc7 (défaut), validation rc7 obligatoire")
    mode.add_argument("--historical-rc5", action="store_true", help="ancienne collection ; fournir des sources historiques si nécessaire")
    parser.add_argument("--check-sources", action="store_true", help="vérifier les sources et le plan sans générer de livrable")
    args = parser.parse_args(argv)
    profile = HISTORICAL_PROFILE if args.historical_rc5 else PUBLIC_PROFILE
    revision = args.revision or profile.revision
    try:
        blocks, chapters = load_blocks(profile, args.source_directory)
    except ValueError as error:
        parser.exit(2, str(error) + "\n")
    chapter_count, appendix_count = source_counts(chapters, profile)
    if args.check_sources:
        print(json.dumps({"version": profile.version, "revision": revision,
                          "chapters": chapter_count, "appendices": appendix_count,
                          "sources": [name for name, _, _ in profile.sources], "blocks": len(blocks)}, ensure_ascii=False))
        return 0
    try:
        fonts = resolve_fonts(args.fonts)
    except (ImportError, ValueError) as error:
        parser.exit(2, str(error) + "; installer requirements-docs.txt pour les outils documentaires.\n")
    args.output.mkdir(parents=True, exist_ok=True)
    pdf = build_pdf(blocks, chapters, args.output, revision, fonts, profile, args.source_directory)
    word = build_docx(blocks, chapters, args.output, revision, profile, args.source_directory)
    verification = validate_pdf(pdf, blocks, chapters, args.output, profile)
    verification_docx = validate_docx(word, blocks, chapters)
    verification["verification_docx"] = verification_docx
    (args.output / profile.qa_name).write_text(json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"generated_at_utc": datetime.now(timezone.utc).isoformat(), "revision": revision,
                "version": profile.version, "chapters": chapter_count, "appendices": appendix_count,
                "fonts": {key: fonts[key].name if fonts[key] is not None else "Courier standard" for key in ("normal", "bold", "italic", "mono")},
                "pdf_font_substitutions": fonts.get("substitutions", {}),
                "sources": [{"path": "docs/" + name, "bytes": (args.source_directory / name).stat().st_size,
                             "sha256": sha256((args.source_directory / name).read_bytes()).hexdigest()} for name, _, _ in profile.sources],
                "outputs": [{"name": file.name, "bytes": file.stat().st_size,
                             "sha256": sha256(file.read_bytes()).hexdigest()} for file in (pdf, word)],
                "verification_pdf": verification, "verification_docx": verification_docx}
    (args.output / profile.manifest_name).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pdf": str(pdf), "word": str(word), "pages": verification["pages"],
                      "chapters": chapter_count, "appendices": appendix_count,
                      "text_token_coverage": verification["text_token_coverage"],
                      "code_blocks_preserved_in_docx": verification_docx["code_blocks_preserved"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
