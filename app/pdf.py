"""PDF для конспекта и расшифровки. Шрифт DejaVu Sans — чтобы работала кириллица."""

from pathlib import Path

from fpdf import FPDF
from fpdf.fonts import FontFace

FONTS = Path(__file__).parent / "fonts"
ACCENT = (47, 79, 216)
MUTED = (110, 110, 115)


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class _Pdf(FPDF):
    def __init__(self, title: str):
        super().__init__(format="A4")
        self.doc_title = title
        self.add_font("DejaVu", "", str(FONTS / "DejaVuSans.ttf"))
        self.add_font("DejaVu", "B", str(FONTS / "DejaVuSans-Bold.ttf"))
        self.set_auto_page_break(auto=True, margin=16)
        self.set_margins(16, 16, 16)
        self.set_title(title)
        self.add_page()

    def footer(self):
        self.set_y(-12)
        self.set_font("DejaVu", size=8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, f"{self.doc_title} · {self.page_no()}", align="C")

    def heading(self, text: str, size: float = 18):
        self.set_font("DejaVu", "B", size)
        self.set_text_color(20, 20, 20)
        self.multi_cell(0, size * 0.5, text, new_x="LMARGIN", new_y="NEXT")

    def muted(self, text: str):
        self.set_font("DejaVu", size=9)
        self.set_text_color(*MUTED)
        self.multi_cell(0, 5, text, new_x="LMARGIN", new_y="NEXT")

    def paragraph(self, text: str, size: float = 11):
        self.set_font("DejaVu", size=size)
        self.set_text_color(20, 20, 20)
        self.multi_cell(0, size * 0.52, text, align="L", new_x="LMARGIN", new_y="NEXT")

    def bullets(self, items: list[str]):
        self.set_font("DejaVu", size=11)
        self.set_text_color(20, 20, 20)
        for item in items:
            self.set_x(self.l_margin + 2)
            self.cell(5, 5.7, "•")
            self.multi_cell(0, 5.7, item, align="L", new_x="LMARGIN", new_y="NEXT")
            self.ln(0.8)


def summary_pdf(title: str, summary: dict, meta: str = "") -> bytes:
    pdf = _Pdf(title)
    pdf.heading(title)
    if meta:
        pdf.ln(1)
        pdf.muted(meta)
    pdf.ln(4)
    if summary.get("overview"):
        pdf.paragraph(summary["overview"])
        pdf.ln(3)

    for section in summary.get("sections") or []:
        pdf.ln(2)
        label = section.get("title", "")
        if section.get("start"):
            pdf.set_font("DejaVu", size=10)
            pdf.set_text_color(*ACCENT)
            pdf.cell(pdf.get_string_width(section["start"]) + 3, 7, section["start"])
        pdf.set_font("DejaVu", "B", 13)
        pdf.set_text_color(20, 20, 20)
        pdf.multi_cell(0, 7, label, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        pdf.bullets(section.get("points") or [])

    if summary.get("takeaways"):
        pdf.ln(3)
        pdf.heading("Главные выводы", 13)
        pdf.ln(1)
        pdf.bullets(summary["takeaways"])
    return bytes(pdf.output())


def transcript_pdf(title: str, paragraphs: list[dict]) -> bytes:
    pdf = _Pdf(title)
    pdf.heading(title)
    pdf.ln(1)
    pdf.muted("Расшифровка с переводом на русский")
    pdf.ln(4)

    pdf.set_font("DejaVu", size=9.5)
    pdf.set_text_color(20, 20, 20)
    head_style = FontFace(family="DejaVu", emphasis="BOLD", color=MUTED, fill_color=(240, 240, 238))
    with pdf.table(
        col_widths=(14, 88, 88),
        headings_style=head_style,
        line_height=4.8,
        padding=1.6,
        borders_layout="HORIZONTAL_LINES",
        text_align="LEFT",
        v_align="TOP",
    ) as table:
        table.row(["", "Оригинал", "Русский"])
        for p in paragraphs:
            table.row([format_time(p.get("start", 0)), p.get("original", ""), p.get("ru", "")])
    return bytes(pdf.output())
