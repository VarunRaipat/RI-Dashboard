"""
PDF generation — Dispatch Instruction and Quotation.
Premium letterhead style: gold/ink palette, tinted header card, dark
ink+gold table headers, and a running gold-bar/footer on every page.
"""
import io
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
    ss.add(ParagraphStyle("Label", fontName="Helvetica", fontSize=7.3,
                          textColor=MUTED, leading=10))
    ss.add(ParagraphStyle("Value", fontName="Helvetica-Bold", fontSize=9.5,
                          textColor=INK, leading=13))
    ss.add(ParagraphStyle("SectionLabel", fontName="Helvetica-Bold", fontSize=7.8,
                          textColor=ACCENT, leading=10))
    ss.add(ParagraphStyle("FooterNote", fontName="Helvetica", fontSize=7.3,
                          textColor=MUTED, alignment=TA_RIGHT))
    ss.add(ParagraphStyle("TableHeadL", fontName="Helvetica-Bold", fontSize=6.3,
                          textColor=GOLD_ON_DARK, leading=7.6))
    ss.add(ParagraphStyle("TableHeadR", fontName="Helvetica-Bold", fontSize=6.3,
                          textColor=GOLD_ON_DARK, leading=7.6, alignment=TA_RIGHT))
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
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]
    for i in range(len(rows) - 1):
        style.append(("LINEBELOW", (0, i), (-1, i), 0.4, HAIRLINE))
    tbl.setStyle(TableStyle(style))
    return tbl


def _section_label(ss, text):
    return Paragraph(_tracked(text), ss["SectionLabel"])


def _letterhead(ss, doc_title, subtitle_lines):
    """Logo + doc title inside a soft-tinted card, closed off with a gold
    rule over a thin hairline (a "double rule") for a premium letterhead feel."""
    if LOGO_PATH.exists():
        logo = Image(str(LOGO_PATH), width=LOGO_W, height=LOGO_H)
    else:
        logo = Paragraph("RAMESHWARAM INDUSTRIES", ss["Wordmark"])

    title_block = [Paragraph(doc_title, ss["DocTitle"])] + \
        [Paragraph(t, ss["DocSub"]) for t in subtitle_lines]

    header_tbl = Table([[logo, title_block]], colWidths=[85 * mm, 89 * mm])
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, -1), ZEBRA),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return [
        header_tbl,
        Spacer(1, 2.4 * mm),
        HRFlowable(width="100%", thickness=1.1, color=ACCENT),
        Spacer(1, 0.6 * mm),
        HRFlowable(width="100%", thickness=0.4, color=HAIRLINE),
        Spacer(1, 7 * mm),
    ]


def _style_product_table(rows, col_widths, total_rows=1):
    """Shared styling for the dispatch/quotation line-item tables: a dark
    ink header band with gold text, warm zebra striping, and a soft gold
    wash on the closing total/subtotal row(s)."""
    body_end = -1 - (total_rows - 1) if total_rows > 1 else -2
    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("FONTSIZE", (0, 1), (-1, -1), 9.3),
        ("FONTNAME", (0, -total_rows), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, -total_rows), (-1, -1), INK),
        ("BACKGROUND", (0, -total_rows), (-1, -1), GOLD_TINT),
        ("LINEBELOW", (0, 0), (-1, 0), 1.0, ACCENT),
        ("LINEABOVE", (0, -total_rows), (-1, -total_rows), 0.8, ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, body_end), [colors.white, ZEBRA]),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, 0), 3),
        ("RIGHTPADDING", (0, 0), (-1, 0), 3),
        ("LINEBELOW", (0, 1), (-1, body_end), 0.3, HAIRLINE),
    ]
    tbl.setStyle(TableStyle(style))
    return tbl


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
                     remarks.
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
        topMargin=18 * mm, bottomMargin=16 * mm,
        leftMargin=MARGIN, rightMargin=MARGIN,
    )
    story = []

    story += _letterhead(ss, "DISPATCH INSTRUCTION", [f"DI No. {di_no}"])

    # ── Order details ─────────────────────────────────────────────────────
    story.append(_section_label(ss, "Order"))
    story.append(Spacer(1, 2 * mm))
    order_pairs = [
        [("DI No.", di_no), ("Order Date", header.get("order_date", "—"))],
        [("Payment Mode", header.get("mode_of_payment", "—")), ("Sale Type", header.get("sale_type", "—"))],
    ]
    story.append(_detail_grid(ss, order_pairs, [43 * mm, 43 * mm, 43 * mm, 45 * mm]))
    story.append(Spacer(1, 5 * mm))

    # ── Client details ───────────────────────────────────────────────────
    story.append(_section_label(ss, "Client"))
    story.append(Spacer(1, 2 * mm))
    client_pairs = [
        [("Client Name", header.get("client_name", "—")), ("Contact Person", header.get("contact_person", "—"))],
        [("Phone", header.get("phone", "—")), ("Client Type", header.get("client_type", "—"))],
        [("Office", header.get("office", "—")), ("GSTIN", header.get("gstin", "—"))],
    ]
    story.append(_detail_grid(ss, client_pairs, [43 * mm, 43 * mm, 43 * mm, 45 * mm]))
    story.append(Spacer(1, 5 * mm))

    # ── Site details ──────────────────────────────────────────────────────
    story.append(_section_label(ss, "Site / Delivery"))
    story.append(Spacer(1, 2 * mm))
    site_pairs = [
        [("Site Address", header.get("delivery_address", "—")), ("Site Person", header.get("site_person", "—"))],
        [("Site Phone No.", header.get("site_phone", "—")), ("", "")],
    ]
    story.append(_detail_grid(ss, site_pairs, [43 * mm, 43 * mm, 43 * mm, 45 * mm]))
    story.append(Spacer(1, 7 * mm))

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
    if has_transport:
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(
            f"GRAND TOTAL (incl. Transport): RS. {total_amount + total_transport:,.2f}",
            ss["FooterNote"],
        ))
    story.append(Spacer(1, 7 * mm))

    remarks = (header.get("remarks") or "").strip()
    if remarks:
        story.append(_section_label(ss, "Remarks"))
        story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph(remarks, ss["Value"]))
        story.append(Spacer(1, 8 * mm))

    # ── Signature blocks ─────────────────────────────────────────────────────
    story.append(HRFlowable(width="100%", thickness=0.4, color=HAIRLINE))
    story.append(Spacer(1, 12 * mm))
    sign_tbl = Table(
        [["", "", ""],
         ["Prepared By", "Dispatch Approved By", "Received By (Client)"]],
        colWidths=[58 * mm] * 3,
    )
    sign_tbl.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, MUTED),
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


def generate_quotation(quote_no, header, lines):
    """
    Build a client-facing Quotation PDF — same premium letterhead style as
    generate_dispatch_instruction (logo + doc title, no fabricated company
    address/GSTIN/bank details since we don't have real values for those).

    Args:
        quote_no : Quotation number (str), e.g. "QTN/25-26/0001".
        header   : dict with quote_date, valid_until, client_name, contact_person,
                   phone, office, gstin, client_type, sales_person, sale_type,
                   discount_pct, remarks.
        lines    : list of dicts with product, qty, unit, rate, amount, gst_amount.
    Returns:
        bytes of the generated PDF.
    """
    ss = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=16 * mm,
        leftMargin=MARGIN, rightMargin=MARGIN,
    )
    story = []

    story += _letterhead(ss, "QUOTATION", [
        f"No. {quote_no}",
        f"Date: {header.get('quote_date', '—')}",
        f"Valid Until: {header.get('valid_until', '—')}",
    ])

    story.append(_section_label(ss, "Client"))
    story.append(Spacer(1, 2 * mm))
    client_pairs = [
        [("Client Name", header.get("client_name", "—")), ("Contact Person", header.get("contact_person", "—"))],
        [("Phone", header.get("phone", "—")), ("Client Type", header.get("client_type", "—"))],
        [("Office", header.get("office", "—")), ("GSTIN", header.get("gstin", "—"))],
    ]
    story.append(_detail_grid(ss, client_pairs, [43 * mm, 43 * mm, 43 * mm, 45 * mm]))
    story.append(Spacer(1, 7 * mm))

    has_gst       = any(float(line.get("gst_amount", 0) or 0) > 0 for line in lines)
    has_transport = any(float(line.get("transport_value", 0) or 0) > 0 for line in lines)
    head = ["PRODUCT", "QTY", "UNIT", "RATE (RS.)"]
    if has_gst:
        head += ["GST (RS.)"]
    if has_transport:
        head += ["TRANSPORT (RS.)"]
    head += ["AMOUNT (RS.)"]

    rows = [_header_row(head, ss)]
    subtotal = 0.0
    total_gst = 0.0
    total_transport = 0.0
    for line in lines:
        prod = line.get("product", "")
        qty  = float(line.get("qty", 0) or 0)
        unit = line.get("unit", "")
        rate = float(line.get("rate", 0) or 0)
        amt  = float(line.get("amount", 0) or 0)
        gst  = float(line.get("gst_amount", 0) or 0)
        transport = float(line.get("transport_value", 0) or 0)
        transport_gst = float(line.get("transport_gst_amount", 0) or 0)
        subtotal        += amt
        total_gst        += gst
        total_transport  += transport + transport_gst
        row = [prod, f"{qty:,.0f}", unit, f"{rate:,.2f}"]
        if has_gst:
            row += [f"{gst:,.2f}"]
        if has_transport:
            row += [f"{transport + transport_gst:,.2f}"]
        row += [f"{amt + gst:,.2f}"]
        rows.append(row)

    discount_pct = float(header.get("discount_pct", 0) or 0)
    discount_amt = round((subtotal + total_gst) * discount_pct / 100, 2)
    grand_total  = subtotal + total_gst - discount_amt + total_transport

    total_row = ["", "", "", ""]
    if has_gst:
        total_row += [f"{total_gst:,.2f}"]
    if has_transport:
        total_row += [f"{total_transport:,.2f}"]
    total_row += [f"{subtotal:,.2f}"]
    rows.append(["SUBTOTAL"] + total_row[1:])

    n_cols = len(head)
    prod_w = 55 * mm
    remaining = (174 * mm) - prod_w
    other_w = remaining / (n_cols - 1)
    col_widths = [prod_w] + [other_w] * (n_cols - 1)

    story.append(_style_product_table(rows, col_widths))
    story.append(Spacer(1, 3 * mm))

    summary_lines = []
    if discount_pct:
        summary_lines.append(f"Discount ({discount_pct:g}%): -Rs. {discount_amt:,.2f}")
    summary_lines.append(f"GRAND TOTAL: Rs. {grand_total:,.2f}")
    for sl in summary_lines:
        story.append(Paragraph(sl, ss["FooterNote"]))
    story.append(Spacer(1, 7 * mm))

    sales_person = (header.get("sales_person") or "").strip()
    if sales_person:
        story.append(_section_label(ss, "Sales Person"))
        story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph(sales_person, ss["Value"]))
        story.append(Spacer(1, 5 * mm))

    remarks = (header.get("remarks") or "").strip()
    if remarks:
        story.append(_section_label(ss, "Remarks"))
        story.append(Spacer(1, 1.5 * mm))
        story.append(Paragraph(remarks, ss["Value"]))
        story.append(Spacer(1, 8 * mm))

    story.append(HRFlowable(width="100%", thickness=0.4, color=HAIRLINE))
    story.append(Spacer(1, 12 * mm))
    sign_tbl = Table(
        [["", ""],
         ["Prepared By", "Accepted By (Client)"]],
        colWidths=[87 * mm, 87 * mm],
    )
    sign_tbl.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 0.5, MUTED),
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
