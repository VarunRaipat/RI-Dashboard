"""
PDF generation — Dispatch Instruction and Quotation.
Premium letterhead style: gold/ink palette, tinted header card, dark
ink+gold table headers, and a running gold-bar/footer on every page.
"""
import io
from xml.sax.saxutils import escape
from core.config import GST_PCT, COMPANY_GSTIN, COMPANY_PHONES, COMPANY_BANK
import re
from core.tz import now_ist
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, HRFlowable,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT

LOGO_PATH = Path(__file__).parent.parent / "assets" / "Logo.png"
ISI_PATH  = Path(__file__).parent.parent / "assets" / "ISI.png"

INK         = colors.HexColor("#181818")   # near-black body text
MUTED       = colors.HexColor("#8C8C8C")   # grey labels / secondary text
ACCENT      = colors.HexColor("#A8843C")   # muted bronze/gold — rules & emphasis
GOLD_ON_DARK = colors.HexColor("#D3B673")  # brighter gold, for text on ink background
GOLD_TINT   = colors.HexColor("#F6EEDD")   # soft gold wash — total/subtotal rows
HAIRLINE    = colors.HexColor("#E4E1D8")   # warm light hairline rule
ZEBRA       = colors.HexColor("#FAF9F6")   # near-white warm row tint
LAKH = 100_000

MARGIN = 18 * mm

# Logo.png is 1600x716px — width/height below preserve that aspect ratio
# instead of stretching it into a squashed banner.
LOGO_W = 28 * mm
LOGO_H = LOGO_W * 716 / 1600


def _tracked(text):
    """Letter-spaced caps for section headers — a light, editorial touch
    that's safe to use only where the text has a full line to itself
    (narrow columns would wrap awkwardly with the extra spacing). Words are
    tracked individually and rejoined with a wider gap so word boundaries
    (e.g. "Sales Person") stay legible instead of reading as one word.
    Uses non-breaking spaces (\\xa0) since Paragraph's XML parser collapses
    runs of regular spaces down to one, which would erase the tracking."""
    letter_gap, word_gap = "\xa0", "\xa0" * 3
    return word_gap.join(letter_gap.join(word) for word in str(text).split(" "))


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("Wordmark", fontName="Helvetica-Bold", fontSize=15,
                          textColor=INK, leading=17))
    ss.add(ParagraphStyle("Eyebrow", fontName="Helvetica", fontSize=8,
                          textColor=ACCENT, leading=10, alignment=TA_RIGHT))
    ss.add(ParagraphStyle("DocTitle", fontName="Helvetica-Bold", fontSize=13,
                          textColor=INK, alignment=TA_RIGHT, spaceAfter=0))
    ss.add(ParagraphStyle("DocSub", fontName="Helvetica", fontSize=9,
                          textColor=MUTED, alignment=TA_RIGHT))
    ss.add(ParagraphStyle("Label", fontName="Helvetica-Bold", fontSize=8.6,
                          textColor=MUTED, leading=11))
    ss.add(ParagraphStyle("Value", fontName="Helvetica-Bold", fontSize=9.5,
                          textColor=INK, leading=13))
    ss.add(ParagraphStyle("Term", fontName="Helvetica", fontSize=8.8,
                          textColor=INK, leading=12, leftIndent=10, firstLineIndent=-10))
    ss.add(ParagraphStyle("SectionLabel", fontName="Helvetica-Bold", fontSize=7.8,
                          textColor=ACCENT, leading=10))
    ss.add(ParagraphStyle("TableHeadL", fontName="Helvetica-Bold", fontSize=8.2,
                          textColor=GOLD_ON_DARK, leading=10))
    ss.add(ParagraphStyle("TableHeadR", fontName="Helvetica-Bold", fontSize=8.2,
                          textColor=GOLD_ON_DARK, leading=10, alignment=TA_RIGHT))
    ss.add(ParagraphStyle("SummaryLabel", fontName="Helvetica", fontSize=8.7,
                          textColor=INK, leading=13))
    ss.add(ParagraphStyle("SummaryValue", fontName="Helvetica-Bold", fontSize=8.7,
                          textColor=INK, leading=13, alignment=TA_RIGHT))
    ss.add(ParagraphStyle("SummaryTotalLabel", fontName="Helvetica-Bold", fontSize=11,
                          textColor=INK, leading=15))
    ss.add(ParagraphStyle("SummaryTotalValue", fontName="Helvetica-Bold", fontSize=11,
                          textColor=ACCENT, leading=15, alignment=TA_RIGHT))
    return ss


def _header_row(head, ss):
    """Wrap table header labels in Paragraphs so long ones (e.g. "TRANSPORT
    (RS.)") wrap onto a second line instead of overflowing into the next
    column — plain strings in a Table cell never wrap, only Flowables do."""
    return [Paragraph(head[0], ss["TableHeadL"])] + \
        [Paragraph(h, ss["TableHeadR"]) for h in head[1:]]


def _info_block(ss, label, value):
    if not label:
        return [Paragraph("", ss["Label"]), Paragraph("", ss["Value"])]
    return [Paragraph(label.upper(), ss["Label"]), Paragraph(str(value or "—"), ss["Value"])]


def _detail_grid(ss, pairs, col_widths):
    """Render label/value pairs as a clean hairline-separated grid (no boxes)."""
    rows = [[cell for pair in row_pairs for cell in _info_block(ss, *pair)] for row_pairs in pairs]
    tbl = Table(rows, colWidths=col_widths)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for i in range(len(rows) - 1):
        style.append(("LINEBELOW", (0, i), (-1, i), 0.4, HAIRLINE))
    tbl.setStyle(TableStyle(style))
    return tbl


def _section_label(ss, text):
    return Paragraph(_tracked(text), ss["SectionLabel"])


def _letterhead(ss, doc_title, subtitle_lines, isi_mark=False):
    """Logo + doc title inside a soft-tinted card, closed off with a gold
    rule over a thin hairline (a "double rule") for a premium letterhead feel."""
    if LOGO_PATH.exists():
        logo = Image(str(LOGO_PATH), width=LOGO_W, height=LOGO_H)
    else:
        logo = Paragraph("RAMESHWARAM INDUSTRIES", ss["Wordmark"])

    # Optional ISI certification mark beside the company logo
    if isi_mark and ISI_PATH.exists():
        isi_h = LOGO_H * 0.9
        isi_w = isi_h * 666 / 519   # ISI.png is 666x519px
        isi = Image(str(ISI_PATH), width=isi_w, height=isi_h)
        logo = Table([[logo, isi]], colWidths=[LOGO_W + 4 * mm, isi_w + 2 * mm])
        logo.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        logo.hAlign = "LEFT"
        # Company name in text under the logos (quotation only)
        if LOGO_PATH.exists():
            logo = [logo, Spacer(1, 2 * mm),
                    Paragraph("RAMESHWARAM INDUSTRIES", ss["Wordmark"])]

    title_block = [Paragraph(doc_title, ss["DocTitle"])] + \
        [Paragraph(t, ss["DocSub"]) for t in subtitle_lines]

    header_tbl = Table([[logo, title_block]], colWidths=[85 * mm, 89 * mm])
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, -1), ZEBRA),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return [
        header_tbl,
        Spacer(1, 2 * mm),
        HRFlowable(width="100%", thickness=1.1, color=ACCENT),
        Spacer(1, 0.6 * mm),
        HRFlowable(width="100%", thickness=0.4, color=HAIRLINE),
        Spacer(1, 2 * mm),
    ]


def _style_product_table(rows, col_widths, total_rows=1):
    """Shared styling for the dispatch/quotation line-item tables: a dark
    ink header band with gold text, warm zebra striping, and a soft gold
    wash on the closing total/subtotal row(s)."""
    body_end = -1 - (total_rows - 1) if total_rows > 1 else -2
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("FONTSIZE", (0, 1), (-1, -1), 8.8),
        ("FONTNAME", (0, -total_rows), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, -total_rows), (-1, -1), INK),
        ("BACKGROUND", (0, -total_rows), (-1, -1), GOLD_TINT),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, ACCENT),
        ("LINEABOVE", (0, -total_rows), (-1, -total_rows), 0.8, ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, body_end), [colors.white, ZEBRA]),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 3.5),
        ("TOPPADDING", (0, 1), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, 0), 3),
        ("RIGHTPADDING", (0, 0), (-1, 0), 3),
        ("LINEBELOW", (0, 1), (-1, body_end), 0.3, HAIRLINE),
    ]
    tbl.setStyle(TableStyle(style))
    return tbl


def _summary_box(ss, rows, box_width=78 * mm, page_width=174 * mm):
    """Right-aligned, gold-tinted settlement box (Untaxed Amount / GST /
    Total) below the line-item table — pulled out of the plain footer text
    so the figures that actually matter for payment stand out instead of
    blending into small grey print."""
    body = []
    for label, value, is_total in rows:
        lstyle = ss["SummaryTotalLabel"] if is_total else ss["SummaryLabel"]
        vstyle = ss["SummaryTotalValue"] if is_total else ss["SummaryValue"]
        body.append([Paragraph(label, lstyle), Paragraph(value, vstyle)])

    box = Table(body, colWidths=[box_width * 0.5, box_width * 0.5])
    style = [
        ("BACKGROUND", (0, 0), (-1, -1), GOLD_TINT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("BOX", (0, 0), (-1, -1), 0.7, ACCENT),
    ]
    if len(body) > 1:
        style.append(("LINEABOVE", (0, -1), (-1, -1), 0.8, ACCENT))
        style.append(("TOPPADDING", (0, -1), (-1, -1), 6))
    box.setStyle(TableStyle(style))

    wrapper = Table([["", box]], colWidths=[page_width - box_width, box_width])
    wrapper.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return wrapper


def _make_canvas(footer_right_text):
    """Canvas that stamps a thin gold bar across the top and a page-number
    + timestamp footer on every page — done as a two-pass canvas since the
    total page count isn't known until the whole story is laid out."""

    class _NumberedCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            pdfcanvas.Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._draw_decorations(total_pages)
                pdfcanvas.Canvas.showPage(self)
            pdfcanvas.Canvas.save(self)

        def _draw_decorations(self, total_pages):
            w, h = A4
            self.saveState()
            self.setFillColor(ACCENT)
            self.rect(0, h - 2 * mm, w, 2 * mm, stroke=0, fill=1)
            self.setStrokeColor(HAIRLINE)
            self.setLineWidth(0.6)
            self.line(MARGIN, 13 * mm, w - MARGIN, 13 * mm)
            self.setFont("Helvetica", 7.3)
            self.setFillColor(MUTED)
            self.drawString(MARGIN, 9 * mm, f"Page {self._pageNumber} of {total_pages}")
            self.drawRightString(w - MARGIN, 9 * mm, footer_right_text)
            self.restoreState()

    return _NumberedCanvas


def generate_dispatch_instruction(di_no, header, lines, dispatched=None):
    """
    Build a Dispatch Instruction PDF for one DI.

    Args:
        di_no      : DI number (str).
        header     : dict with order_date, client_name, contact_person, phone,
                     office, gstin, client_type, mode_of_payment, sale_type,
                     delivery_address (site address), site_person, site_phone,
                     po_name, po_date, remarks.
        lines      : list of dicts with product, qty_ordered, rate, total_amount.
        dispatched : optional dict {product: {"qty": x, "value": y}} of qty already
                     dispatched against this DI, for a pending-qty column.
    Returns:
        bytes of the generated PDF.
    """
    ss = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=8 * mm, bottomMargin=14 * mm,
        leftMargin=MARGIN, rightMargin=MARGIN,
    )
    story = []

    story += _letterhead(ss, "DISPATCH INSTRUCTION", [f"DI No. {di_no}"])

    # ── Order details ─────────────────────────────────────────────────────
    story.append(_section_label(ss, "Order"))
    story.append(Spacer(1, 1.5 * mm))
    order_pairs = [
        [("DI No.", di_no), ("Order Date", header.get("order_date", "—"))],
        [("Payment Mode", header.get("mode_of_payment", "—")), ("Sale Type", header.get("sale_type", "—"))],
        [("PO Name", header.get("po_name") or "—"), ("PO Date", header.get("po_date") or "—")],
    ]
    story.append(_detail_grid(ss, order_pairs, [43 * mm, 43 * mm, 43 * mm, 45 * mm]))
    story.append(Spacer(1, 2 * mm))

    # ── Client details ───────────────────────────────────────────────────
    story.append(_section_label(ss, "Client"))
    story.append(Spacer(1, 1.5 * mm))
    client_pairs = [
        [("Client Name", header.get("client_name", "—")), ("Contact Person", header.get("contact_person", "—"))],
        [("Phone", header.get("phone", "—")), ("Client Type", header.get("client_type", "—"))],
        [("Office", header.get("office", "—")), ("GSTIN", header.get("gstin", "—"))],
    ]
    story.append(_detail_grid(ss, client_pairs, [43 * mm, 43 * mm, 43 * mm, 45 * mm]))
    story.append(Spacer(1, 2 * mm))

    # ── Site details ──────────────────────────────────────────────────────
    story.append(_section_label(ss, "Site / Delivery"))
    story.append(Spacer(1, 1.5 * mm))
    site_pairs = [
        [("Site Address", header.get("delivery_address", "—")), ("Site Person", header.get("site_person", "—"))],
        [("Site Phone No.", header.get("site_phone", "—")), ("", "")],
    ]
    story.append(_detail_grid(ss, site_pairs, [43 * mm, 43 * mm, 43 * mm, 45 * mm]))
    story.append(Spacer(1, 3 * mm))

    # ── Product lines table ─────────────────────────────────────────────────
    has_pending   = dispatched is not None
    has_gst       = any(float(line.get("gst_amount", 0) or 0) > 0 for line in lines)
    has_transport = any(float(line.get("transport_value", 0) or 0) > 0 for line in lines)
    head = ["PRODUCT", "QTY ORDERED"]
    if has_pending:
        head += ["DISPATCHED", "PENDING"]
    head += ["RATE (RS.)"]
    if has_gst:
        head += ["GST (RS.)"]
    if has_transport:
        head += ["TRANSPORT (RS.)"]
    head += ["TOTAL (RS.)"]

    rows = [_header_row(head, ss)]
    total_amount    = 0.0
    total_gst       = 0.0
    total_qty       = 0.0
    total_transport = 0.0
    for line in lines:
        prod   = line.get("product", "")
        qty    = float(line.get("qty_ordered", 0) or 0)
        rate   = float(line.get("rate", 0) or 0)
        amt    = float(line.get("total_amount", 0) or 0)
        gst    = float(line.get("gst_amount", 0) or 0)
        transport = float(line.get("transport_value", 0) or 0)
        transport_gst = float(line.get("transport_gst_amount", 0) or 0)
        total_amount    += amt
        total_gst       += gst
        total_qty       += qty
        total_transport += transport + transport_gst
        row = [prod, f"{qty:,.0f}"]
        if has_pending:
            d = (dispatched or {}).get(prod, {"qty": 0})
            d_qty = float(d.get("qty", 0) or 0)
            row += [f"{d_qty:,.0f}", f"{max(qty - d_qty, 0):,.0f}"]
        row += [f"{rate:,.2f}"]
        if has_gst:
            row += [f"{gst:,.2f}"]
        if has_transport:
            row += [f"{transport + transport_gst:,.2f}"]
        row += [f"{amt:,.2f}"]
        rows.append(row)

    total_row = ["TOTAL", f"{total_qty:,.0f}"]
    if has_pending:
        total_row += ["", ""]
    total_row += [""]
    if has_gst:
        total_row += [f"{total_gst:,.2f}"]
    if has_transport:
        total_row += [f"{total_transport:,.2f}"]
    total_row += [f"{total_amount:,.2f}"]
    rows.append(total_row)

    n_cols = len(head)
    prod_w = 55 * mm
    remaining = (174 * mm) - prod_w
    other_w = remaining / (n_cols - 1)
    col_widths = [prod_w] + [other_w] * (n_cols - 1)

    story.append(_style_product_table(rows, col_widths))
    story.append(Spacer(1, 2.5 * mm))

    untaxed_amount = total_amount - total_gst
    summary_rows = [("Untaxed Amount", f"Rs. {untaxed_amount:,.2f}", False)]
    if has_gst:
        summary_rows.append((f"GST @ {GST_PCT:.0f}%", f"Rs. {total_gst:,.2f}", False))
    if has_transport:
        summary_rows.append(("Transport (incl. GST)", f"Rs. {total_transport:,.2f}", False))
    summary_rows.append(("TOTAL AMOUNT", f"Rs. {total_amount + total_transport:,.2f}", True))
    story.append(_summary_box(ss, summary_rows))
    story.append(Spacer(1, 3 * mm))

    remarks = (header.get("remarks") or "").strip()
    if remarks:
        story.append(_section_label(ss, "Remarks"))
        story.append(Spacer(1, 0.8 * mm))
        story.append(Paragraph(remarks, ss["Value"]))
        story.append(Spacer(1, 3 * mm))

    # ── Signature blocks ─────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.4, color=HAIRLINE))
    story.append(Spacer(1, 3 * mm))
    sign_tbl = Table(
        [["", "", ""],
         ["Prepared By", "Dispatch Approved By", "Received By (Client)"]],
        colWidths=[58 * mm] * 3,
    )
    sign_tbl.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, MUTED),
        ("FONTSIZE", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 0), (-1, 0), 0),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 0),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, 1), 7.5),
        ("TEXTCOLOR", (0, 1), (-1, 1), MUTED),
        ("TOPPADDING", (0, 1), (-1, 1), 3),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
    ]))
    story.append(sign_tbl)

    doc.build(story, canvasmaker=_make_canvas(f"Generated {now_ist().strftime('%d %b %Y, %I:%M %p')}"))
    return buf.getvalue()


# ════════════════════════════════════════════════════════════════════════════
# QUOTATION — premium client-facing design (navy + gold, Montserrat + Inter).
# Kept separate from the Dispatch Instruction helpers above so the internal
# DI document keeps its existing look.
# ════════════════════════════════════════════════════════════════════════════
from datetime import datetime
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether

FONT_DIR = Path(__file__).parent.parent / "assets" / "fonts"

Q_NAVY    = colors.HexColor("#0F2B46")   # deep navy — harmonises with the RI logo blue
Q_GOLD    = colors.HexColor("#B08D4A")   # antique gold accents
Q_GOLD_LT = colors.HexColor("#D9BF85")   # gold on navy
Q_INK     = colors.HexColor("#1D232B")
Q_MUTED   = colors.HexColor("#7A818A")
Q_HAIR    = colors.HexColor("#E3E0D8")
Q_CARD    = colors.HexColor("#F7F5F0")   # warm paper tint for the client card

COMPANY_NAME = "RAMESHWARAM INDUSTRIES"
COMPANY_TAGLINE = "RCC Hume Pipes | Boundary Walls | PSC Poles"

_Q_FONTS = None


def _q_fonts():
    """Register the bundled Inter + Montserrat TTFs once (they carry
    the ₹ glyph, which built-in Helvetica doesn't). Falls back to built-in
    fonts if the files are missing so the PDF never fails to generate."""
    global _Q_FONTS
    if _Q_FONTS:
        return _Q_FONTS
    files = {
        "Q-Regular": "Inter-Regular.ttf", "Q-Medium": "Inter-Medium.ttf",
        "Q-SemiBold": "Inter-SemiBold.ttf", "Q-Bold": "Inter-Bold.ttf",
        "Q-Display": "Montserrat-Bold.ttf", "Q-DisplaySemi": "Montserrat-SemiBold.ttf",
    }
    try:
        for name, f in files.items():
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / f)))
        _Q_FONTS = {"reg": "Q-Regular", "med": "Q-Medium", "semi": "Q-SemiBold",
                    "bold": "Q-Bold", "disp": "Q-Display", "dispSemi": "Q-DisplaySemi", "rs": "₹"}
    except Exception:
        _Q_FONTS = {"reg": "Helvetica", "med": "Helvetica", "semi": "Helvetica-Bold",
                    "bold": "Helvetica-Bold", "disp": "Helvetica-Bold", "dispSemi": "Helvetica-Bold", "rs": "Rs."}
    return _Q_FONTS


def _inr(x, decimals=2):
    """Indian digit grouping: 418900.5 -> '4,18,900.50'."""
    x = float(x or 0)
    neg = x < 0
    whole, _, frac = f"{abs(x):.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("-" if neg else "") + whole + (f".{frac}" if decimals else "")


_ONES = ("Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen "
         "Fourteen Fifteen Sixteen Seventeen Eighteen Nineteen").split()
_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _words_below_1000(n):
    parts = []
    if n >= 100:
        parts.append(f"{_ONES[n // 100]} Hundred")
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        parts.append(_ONES[n])
    return " ".join(parts)


def _int_words(n):
    """Indian numbering system words for a non-negative int."""
    if n == 0:
        return "Zero"
    parts = []
    if n >= 10_000_000:
        parts.append(f"{_int_words(n // 10_000_000)} Crore")
        n %= 10_000_000
    for div, name in ((100_000, "Lakh"), (1_000, "Thousand")):
        if n >= div:
            parts.append(f"{_words_below_1000(n // div)} {name}")
            n %= div
    if n:
        parts.append(_words_below_1000(n))
    return " ".join(parts)


def _amount_in_words(amount):
    """418900 -> 'Rupees Four Lakh Eighteen Thousand Nine Hundred Only'."""
    paise_total = int(round(abs(float(amount or 0)) * 100))
    rupees, paise = divmod(paise_total, 100)
    out = f"Rupees {_int_words(rupees)}"
    if paise:
        out += f" and {_words_below_1000(paise)} Paise"
    return out + " Only"


def _fmt_date(value):
    """Render any date shape the app passes ('2026-11-13', '13-Nov-2026',
    a date object) as '13 Nov 2026'."""
    if value is None or str(value).strip() in ("", "—", "nan", "NaT", "None"):
        return "—"
    if hasattr(value, "strftime"):
        return value.strftime("%d %b %Y")
    s = str(value).strip()[:19]
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%d %b %Y")
        except ValueError:
            continue
    return str(value)


def _q_styles(F):
    def ps(name, **kw):
        kw.setdefault("fontName", F["reg"])
        kw.setdefault("textColor", Q_INK)
        return ParagraphStyle(name, **kw)
    return {
        "company":   ps("qCompany", fontName=F["disp"], fontSize=15, leading=18, textColor=Q_NAVY),
        "tagline":   ps("qTagline", fontName=F["med"], fontSize=7, leading=10, textColor=Q_GOLD),
        "title":     ps("qTitle", fontName=F["disp"], fontSize=26, leading=30, textColor=Q_NAVY, alignment=TA_RIGHT),
        "metaL":     ps("qMetaL", fontName=F["med"], fontSize=6.8, leading=10, textColor=Q_MUTED),
        "metaV":     ps("qMetaV", fontName=F["semi"], fontSize=9, leading=11, textColor=Q_INK, alignment=TA_RIGHT),
        "eyebrow":   ps("qEyebrow", fontName=F["semi"], fontSize=7, leading=9, textColor=Q_GOLD),
        "client":    ps("qClient", fontName=F["dispSemi"], fontSize=13.5, leading=17, textColor=Q_NAVY),
        "clientSub": ps("qClientSub", fontSize=8.6, leading=12.5, textColor=Q_INK),
        "kvL":       ps("qKvL", fontName=F["med"], fontSize=6.8, leading=11, textColor=Q_MUTED),
        "kvV":       ps("qKvV", fontName=F["semi"], fontSize=8.6, leading=11, textColor=Q_INK),
        "th":        ps("qTh", fontName=F["semi"], fontSize=7, leading=9, textColor=colors.white),
        "thR":       ps("qThR", fontName=F["semi"], fontSize=7, leading=9, textColor=colors.white, alignment=TA_RIGHT),
        "thC":       ps("qThC", fontName=F["semi"], fontSize=7, leading=9, textColor=colors.white, alignment=TA_CENTER),
        "tdProd":    ps("qTdProd", fontName=F["semi"], fontSize=8.8, leading=11.5, textColor=Q_NAVY),
        "tdR":       ps("qTdR", fontSize=8.8, leading=11.5, alignment=TA_RIGHT),
        "tdRB":      ps("qTdRB", fontName=F["semi"], fontSize=8.8, leading=11.5, alignment=TA_RIGHT),
        "tdC":       ps("qTdC", fontSize=8.8, leading=11.5, textColor=Q_MUTED, alignment=TA_CENTER),
        "sumL":      ps("qSumL", fontSize=8.8, leading=12, textColor=Q_MUTED),
        "sumV":      ps("qSumV", fontName=F["med"], fontSize=8.8, leading=12, alignment=TA_RIGHT),
        "totL":      ps("qTotL", fontName=F["semi"], fontSize=7.4, leading=14, textColor=Q_GOLD_LT),
        "totV":      ps("qTotV", fontName=F["disp"], fontSize=13, leading=16, textColor=colors.white, alignment=TA_RIGHT),
        "words":     ps("qWords", fontName=F["semi"], fontSize=10.5, leading=14.5, textColor=Q_NAVY),
        "note":      ps("qNote", fontSize=7.4, leading=10, textColor=Q_MUTED),
        "contact":   ps("qContact", fontSize=7.8, leading=11, textColor=Q_MUTED),
        "body":      ps("qBody", fontSize=8.8, leading=13),
        "termN":     ps("qTermN", fontName=F["semi"], fontSize=8.2, leading=12.5, textColor=Q_GOLD),
        "term":      ps("qTerm", fontSize=8.4, leading=12.5),
        "thanks":    ps("qThanks", fontName=F["dispSemi"], fontSize=9.5, leading=13, textColor=Q_NAVY, alignment=TA_CENTER),
    }


def _q_layout(t, extra=()):
    """Zero-padding style for layout-only tables."""
    t.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), *extra,
    ]))
    return t


def _q_section(ss, text, width):
    """Gold tracked eyebrow label with a hairline running on to the margin."""
    label = _tracked(text.upper())
    st = ss["eyebrow"]
    lw = pdfmetrics.stringWidth(label, st.fontName, st.fontSize) + 8
    return _q_layout(Table([[Paragraph(escape(label), st), ""]], colWidths=[lw, width - lw]), [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (1, 0), (1, 0), 0.5, Q_HAIR),
        ("BOTTOMPADDING", (1, 0), (1, 0), 3.2),
    ])


def _quote_canvas(quote_no, F):
    """Navy band + gold rule across the top of every page, and a quiet
    footer: company line on the left, quote no. + page count on the right."""

    class _QuoteCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            pdfcanvas.Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._decorate(total)
                pdfcanvas.Canvas.showPage(self)
            pdfcanvas.Canvas.save(self)

        def _decorate(self, total):
            w, h = A4
            self.saveState()
            self.setFillColor(Q_NAVY)
            self.rect(0, h - 5 * mm, w, 5 * mm, stroke=0, fill=1)
            self.setFillColor(Q_GOLD)
            self.rect(0, h - 5.9 * mm, w, 0.9 * mm, stroke=0, fill=1)

            self.setStrokeColor(Q_GOLD)
            self.setLineWidth(0.6)
            self.line(MARGIN, 14 * mm, w - MARGIN, 14 * mm)
            self.setFont(F["semi"], 6.8)
            self.setFillColor(Q_NAVY)
            self.drawString(MARGIN, 9.5 * mm, COMPANY_NAME)
            self.setFont(F["reg"], 6.8)
            self.setFillColor(Q_MUTED)
            self.drawString(MARGIN + pdfmetrics.stringWidth(COMPANY_NAME, F["semi"], 6.8) + 8,
                            9.5 * mm, COMPANY_TAGLINE)
            self.drawRightString(w - MARGIN, 9.5 * mm,
                                 f"{quote_no}     Page {self._pageNumber} of {total}")
            self.restoreState()

    return _QuoteCanvas


def generate_quotation(quote_no, header, lines):
    """
    Build a client-facing Quotation PDF — premium navy/gold design with
    Montserrat headings, Indian ₹ formatting and amount in words. No company
    address/GSTIN/bank details are printed since we don't hold real values.

    Args:
        quote_no : Quotation number (str), e.g. "QTN/26-27/0105".
        header   : dict with quote_date, valid_until, client_name, contact_person,
                   phone, office, gstin, client_type, sales_person, sale_type,
                   delivery_address, discount_pct, remarks, terms (newline-separated).
        lines    : list of dicts with product, qty, unit, rate, amount, gst_amount,
                   transport_value, transport_gst_amount.
    Returns:
        bytes of the generated PDF.
    """
    F = _q_fonts()
    ss = _q_styles(F)
    RS = F["rs"]
    W = A4[0] - 2 * MARGIN   # 174mm content width

    def esc(v, dash="—"):
        v = "" if v is None else str(v).strip()
        return escape(v) if v and v.lower() not in ("nan", "none") else dash

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        # frames pad 6pt inside the margins — offset so content sits exactly on MARGIN
        topMargin=13 * mm, bottomMargin=18 * mm,
        leftMargin=MARGIN - 6, rightMargin=MARGIN - 6,
        title=f"Quotation {quote_no}", author=COMPANY_NAME.title(),
    )
    story = []

    # ── Letterhead ────────────────────────────────────────────────────────
    logo_w = 30 * mm
    logo_h = logo_w * 716 / 1600            # Logo.png is 1600x716
    marks, mark_w = [], []
    if LOGO_PATH.exists():
        marks.append(Image(str(LOGO_PATH), width=logo_w, height=logo_h))
        mark_w.append(logo_w + 5 * mm)
    if ISI_PATH.exists():
        isi_h = logo_h * 0.92
        isi_w = isi_h * 666 / 519           # ISI.png is 666x519
        marks.append(Image(str(ISI_PATH), width=isi_w, height=isi_h))
        mark_w.append(isi_w)
    left = []
    if marks:
        mt = _q_layout(Table([marks], colWidths=mark_w), [("VALIGN", (0, 0), (-1, -1), "MIDDLE")])
        mt.hAlign = "LEFT"
        left += [mt, Spacer(1, 4 * mm)]
    left += [Paragraph(COMPANY_NAME, ss["company"]), Spacer(1, 1.2 * mm),
             Paragraph(escape(_tracked(COMPANY_TAGLINE.upper())), ss["tagline"])]
    contact_bits = []
    if COMPANY_GSTIN:
        contact_bits.append(f"GSTIN <font name='{F['semi']}' color='#1D232B'>{escape(COMPANY_GSTIN)}</font>")
    if COMPANY_PHONES:
        contact_bits.append(f"Ph. <font name='{F['semi']}' color='#1D232B'>"
                            f"{escape(' | '.join(COMPANY_PHONES))}</font>")
    if contact_bits:
        left += [Spacer(1, 2.2 * mm),
                 Paragraph("   ·   ".join(contact_bits), ss["contact"])]

    meta = Table([
        [Paragraph(_tracked("QUOTE NO."), ss["metaL"]), Paragraph(esc(quote_no), ss["metaV"])],
        [Paragraph(_tracked("DATE"), ss["metaL"]), Paragraph(_fmt_date(header.get("quote_date")), ss["metaV"])],
        [Paragraph(_tracked("VALID UNTIL"), ss["metaL"]), Paragraph(_fmt_date(header.get("valid_until")), ss["metaV"])],
    ], colWidths=[26 * mm, 40 * mm])
    _q_layout(meta, [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, Q_HAIR),
        ("TOPPADDING", (0, 0), (-1, -1), 2.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
    ])
    meta.hAlign = "RIGHT"
    right = [Paragraph("QUOTATION", ss["title"]), Spacer(1, 4 * mm), meta]

    head = _q_layout(Table([[left, right]], colWidths=[W - 70 * mm, 70 * mm]),
                     [("VALIGN", (0, 0), (-1, -1), "BOTTOM")])
    story += [head, Spacer(1, 5 * mm),
              HRFlowable(width="100%", thickness=1.2, color=Q_NAVY, spaceAfter=1.1 * mm),
              HRFlowable(width="100%", thickness=0.5, color=Q_GOLD),
              Spacer(1, 6 * mm)]

    # ── Client card ───────────────────────────────────────────────────────
    client_left = [Paragraph(_tracked("PREPARED FOR"), ss["eyebrow"]), Spacer(1, 2 * mm),
                   Paragraph(esc(header.get("client_name")), ss["client"]), Spacer(1, 1.2 * mm)]
    attn = esc(header.get("contact_person"), "")
    if attn:
        client_left.append(Paragraph(f"Kind Attn: <font name='{F['semi']}'>{attn}</font>", ss["clientSub"]))
    contact = "  ·  ".join(b for b in (esc(header.get("phone"), ""), esc(header.get("office"), "")) if b)
    if contact:
        client_left.append(Paragraph(contact, ss["clientSub"]))

    kv_rows = [("GSTIN", header.get("gstin")), ("Client Type", header.get("client_type")),
               ("Sales Contact", header.get("sales_person"))]
    kv = Table([[Paragraph(_tracked(k.upper()), ss["kvL"]), Paragraph(esc(v), ss["kvV"])] for k, v in kv_rows],
               colWidths=[31 * mm, 38 * mm])
    _q_layout(kv, [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, Q_HAIR),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])
    card_rows = [[client_left, kv]]
    card_style = [
        ("BACKGROUND", (0, 0), (-1, -1), Q_CARD),
        ("LINEBEFORE", (0, 0), (0, -1), 2.4, Q_GOLD),
        ("LINEBEFORE", (1, 0), (1, 0), 0.5, Q_HAIR),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 11), ("RIGHTPADDING", (0, 0), (-1, -1), 11),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]
    delivery = esc(header.get("delivery_address"), "")
    if delivery:
        # full-width strip under the client details, split off by a hairline
        card_rows.append([[Paragraph(_tracked("DELIVERY ADDRESS"), ss["eyebrow"]), Spacer(1, 1.5 * mm),
                           Paragraph(delivery, ss["kvV"])], ""])
        card_style += [
            ("SPAN", (0, 1), (1, 1)),
            ("LINEABOVE", (0, 1), (1, 1), 0.5, Q_HAIR),
            ("TOPPADDING", (0, 1), (-1, 1), 7), ("BOTTOMPADDING", (0, 1), (-1, 1), 9),
        ]
    card = Table(card_rows, colWidths=[W - 80 * mm, 80 * mm])
    card.setStyle(TableStyle(card_style))
    story += [card, Spacer(1, 7 * mm)]

    # ── Line items ────────────────────────────────────────────────────────
    has_gst       = any(float(l.get("gst_amount", 0) or 0) > 0 for l in lines)
    has_transport = any(float(l.get("transport_value", 0) or 0) > 0 for l in lines)

    cols = [("#", 8 * mm, "thC"), ("DESCRIPTION", None, "th"), ("QTY", 16 * mm, "thR"),
            ("UNIT", 12 * mm, "thC"), (f"RATE ({RS})", 20 * mm, "thR")]
    if has_gst:
        cols.append((f"GST ({RS})", 21 * mm, "thR"))
    if has_transport:
        cols.append((f"TRANSPORT ({RS})", 23 * mm, "thR"))
    cols.append((f"AMOUNT ({RS})", 27 * mm, "thR"))
    fixed = sum(c[1] for c in cols if c[1])
    widths = [c[1] or (W - fixed) for c in cols]

    rows = [[Paragraph(c[0], ss[c[2]]) for c in cols]]
    subtotal = total_gst = total_transport = 0.0
    for n, l in enumerate(lines, 1):
        qty  = float(l.get("qty", 0) or 0)
        rate = float(l.get("rate", 0) or 0)
        amt  = float(l.get("amount", 0) or 0)
        gst  = float(l.get("gst_amount", 0) or 0)
        trn  = float(l.get("transport_value", 0) or 0) + float(l.get("transport_gst_amount", 0) or 0)
        subtotal += amt
        total_gst += gst
        total_transport += trn
        r = [Paragraph(f"{n:02d}", ss["tdC"]), Paragraph(esc(l.get("product")), ss["tdProd"]),
             Paragraph(_inr(qty, 0) if qty == int(qty) else _inr(qty).rstrip("0"), ss["tdR"]),
             Paragraph(esc(l.get("unit"), ""), ss["tdC"]), Paragraph(_inr(rate), ss["tdR"])]
        if has_gst:
            r.append(Paragraph(_inr(gst) if gst else "—", ss["tdR"]))
        if has_transport:
            r.append(Paragraph(_inr(trn) if trn else "—", ss["tdR"]))
        r.append(Paragraph(_inr(amt + gst), ss["tdRB"]))
        rows.append(r)

    items = Table(rows, colWidths=widths, repeatRows=1)
    items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), Q_NAVY),
        ("LINEBELOW", (0, 0), (-1, 0), 1.6, Q_GOLD),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 1), (-1, -2), 0.5, Q_HAIR),
        ("LINEBELOW", (0, -1), (-1, -1), 1.0, Q_NAVY),
    ]))
    story += [items, Spacer(1, 6 * mm)]

    # ── Totals + amount in words ──────────────────────────────────────────
    discount_pct = float(header.get("discount_pct", 0) or 0)
    discount_amt = round((subtotal + total_gst) * discount_pct / 100, 2)
    grand_total  = subtotal + total_gst - discount_amt + total_transport

    sum_rows = [("Taxable Value", f"{RS} {_inr(subtotal)}")]
    if has_gst:
        sum_rows.append((f"GST @ {GST_PCT:.0f}%", f"{RS} {_inr(total_gst)}"))
    if discount_pct:
        sum_rows.append((f"Discount ({discount_pct:g}%)", f"– {RS} {_inr(discount_amt)}"))
    if has_transport:
        sum_rows.append(("Transport (incl. GST)", f"{RS} {_inr(total_transport)}"))
    body = [[Paragraph(a, ss["sumL"]), Paragraph(b, ss["sumV"])] for a, b in sum_rows]
    body.append([Paragraph(_tracked("GRAND TOTAL"), ss["totL"]),
                 Paragraph(f"{RS} {_inr(grand_total)}", ss["totV"])])
    sw = 82 * mm
    summary = Table(body, colWidths=[sw * 0.46, sw * 0.54])
    summary.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -2), 3.2), ("BOTTOMPADDING", (0, 0), (-1, -2), 3.2),
        ("LINEBELOW", (0, 0), (-1, -3), 0.4, Q_HAIR),
        ("BACKGROUND", (0, -1), (-1, -1), Q_NAVY),
        ("LINEABOVE", (0, -1), (-1, -1), 1.6, Q_GOLD),
        ("TOPPADDING", (0, -1), (-1, -1), 8), ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
    ]))

    words = [Paragraph(_tracked("AMOUNT IN WORDS"), ss["eyebrow"]), Spacer(1, 2 * mm),
             Paragraph(escape(_amount_in_words(grand_total)), ss["words"])]
    if COMPANY_BANK:
        lw = W - sw - 10 * mm
        b = COMPANY_BANK
        bank_rows = [
            ("Account", b.get("Account Name")),
            ("Bank", ", ".join(x for x in (b.get("Bank"), b.get("Branch")) if x)),
            ("A/C No.", b.get("A/C No.")),
            ("IFSC", b.get("IFSC")),
        ]
        bank_rows = [(k, v) for k, v in bank_rows if v]
        # A/C No. and IFSC share one row to keep the panel compact
        cells = []
        for k, v in bank_rows:
            if k == "IFSC" and cells and cells[-1][0] == "A/C No.":
                cells[-1] = ("A/C No.", f"{cells[-1][1]}   <font name='{F['med']}' size='6.8' "
                                         f"color='#7A818A'>IFSC</font>  {escape(v)}")
                continue
            cells.append((k, escape(v)))
        bank = Table([[Paragraph(_tracked(k.upper()), ss["kvL"]), Paragraph(v, ss["kvV"])]
                      for k, v in cells], colWidths=[20 * mm, lw - 20 * mm])
        _q_layout(bank, [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, Q_HAIR),
            ("TOPPADDING", (0, 0), (-1, -1), 2.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.2),
        ])
        words += [Spacer(1, 4.5 * mm), Paragraph(_tracked("BANK DETAILS"), ss["eyebrow"]),
                  Spacer(1, 1.5 * mm), bank]
    totals = _q_layout(Table([[words, "", summary]], colWidths=[W - sw - 10 * mm, 10 * mm, sw]))
    story += [KeepTogether(totals), Spacer(1, 6 * mm)]

    # ── Terms (the form's "Additional Note" prints as the last term) ─────
    # strip any numbering/bullets typed into the terms box — the PDF numbers them itself
    terms = [re.sub(r"^\s*(?:\d+\s*[.)]|[•\-*·])\s*", "", t).strip()
             for t in str(header.get("terms") or "").splitlines() if t.strip()]
    terms = [t for t in terms if t]
    remarks = " ".join(str(header.get("remarks") or "").split())
    if remarks and remarks.lower() != "nan":
        terms.append(remarks)
    if terms:
        tt = _q_layout(Table([[Paragraph(f"{i:02d}", ss["termN"]), Paragraph(escape(t), ss["term"])]
                              for i, t in enumerate(terms, 1)], colWidths=[9 * mm, W - 9 * mm]),
                       [("TOPPADDING", (0, 0), (-1, -1), 1.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2)])
        story += [_q_section(ss, "Terms & Conditions", W), Spacer(1, 2.5 * mm), tt, Spacer(1, 7 * mm)]

    # ── Closing line (no signature block — removed at user's request) ────
    story.append(Paragraph("Thank you for the opportunity — we look forward to working with you.",
                           ss["thanks"]))

    doc.build(story, canvasmaker=_quote_canvas(quote_no, F))
    return buf.getvalue()
