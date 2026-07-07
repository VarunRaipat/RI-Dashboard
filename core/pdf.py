"""
PDF generation — Dispatch Instruction for Sales Orders.
Minimalist, premium letterhead style — no marketing copy, generous
whitespace, hairline rules, single muted accent colour.
"""
import io
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, HRFlowable,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT

LOGO_PATH = Path(__file__).parent.parent / "assets" / "Logo.png"

INK      = colors.HexColor("#181818")   # near-black body text
MUTED    = colors.HexColor("#8C8C8C")   # grey labels / secondary text
ACCENT   = colors.HexColor("#A8843C")   # muted bronze/gold — used sparingly
HAIRLINE = colors.HexColor("#E4E1D8")   # warm light hairline rule
ZEBRA    = colors.HexColor("#FAF9F6")   # near-white warm row tint
LAKH     = 100_000


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
    ss.add(ParagraphStyle("SectionLabel", fontName="Helvetica-Bold", fontSize=7.5,
                          textColor=ACCENT, leading=10))
    ss.add(ParagraphStyle("FooterNote", fontName="Helvetica", fontSize=7.3,
                          textColor=MUTED, alignment=TA_RIGHT))
    return ss


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
        leftMargin=18 * mm, rightMargin=18 * mm,
    )
    story = []

    # ── Letterhead: logo + doc title, no marketing copy ─────────────────────
    if LOGO_PATH.exists():
        logo = Image(str(LOGO_PATH), width=32 * mm, height=8 * mm)
    else:
        logo = Paragraph("RAMESHWARAM INDUSTRIES", ss["Wordmark"])

    title_block = [
        Paragraph("DISPATCH INSTRUCTION", ss["DocTitle"]),
        Paragraph(f"DI No. {di_no}", ss["DocSub"]),
    ]
    header_tbl = Table([[logo, title_block]], colWidths=[85 * mm, 89 * mm])
    header_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(header_tbl)
    story.append(Spacer(1, 5 * mm))
    story.append(HRFlowable(width="100%", thickness=0.8, color=ACCENT))
    story.append(Spacer(1, 7 * mm))

    # ── Order details ─────────────────────────────────────────────────────
    story.append(Paragraph("ORDER", ss["SectionLabel"]))
    story.append(Spacer(1, 2 * mm))
    order_pairs = [
        [("DI No.", di_no), ("Order Date", header.get("order_date", "—"))],
        [("Payment Mode", header.get("mode_of_payment", "—")), ("Sale Type", header.get("sale_type", "—"))],
    ]
    story.append(_detail_grid(ss, order_pairs, [43 * mm, 43 * mm, 43 * mm, 45 * mm]))
    story.append(Spacer(1, 5 * mm))

    # ── Client details ───────────────────────────────────────────────────
    story.append(Paragraph("CLIENT", ss["SectionLabel"]))
    story.append(Spacer(1, 2 * mm))
    client_pairs = [
        [("Client Name", header.get("client_name", "—")), ("Contact Person", header.get("contact_person", "—"))],
        [("Phone", header.get("phone", "—")), ("Client Type", header.get("client_type", "—"))],
        [("Office", header.get("office", "—")), ("GSTIN", header.get("gstin", "—"))],
    ]
    story.append(_detail_grid(ss, client_pairs, [43 * mm, 43 * mm, 43 * mm, 45 * mm]))
    story.append(Spacer(1, 5 * mm))

    # ── Site details ──────────────────────────────────────────────────────
    story.append(Paragraph("SITE / DELIVERY", ss["SectionLabel"]))
    story.append(Spacer(1, 2 * mm))
    site_pairs = [
        [("Site Address", header.get("delivery_address", "—")), ("Site Person", header.get("site_person", "—"))],
        [("Site Phone No.", header.get("site_phone", "—")), ("", "")],
    ]
    story.append(_detail_grid(ss, site_pairs, [43 * mm, 43 * mm, 43 * mm, 45 * mm]))
    story.append(Spacer(1, 7 * mm))

    # ── Product lines table ─────────────────────────────────────────────────
    has_pending  = dispatched is not None
    has_gst      = any(float(line.get("gst_amount", 0) or 0) > 0 for line in lines)
    head = ["PRODUCT", "QTY ORDERED"]
    if has_pending:
        head += ["DISPATCHED", "PENDING"]
    head += ["RATE (RS.)"]
    if has_gst:
        head += ["GST (RS.)"]
    head += ["TOTAL (RS.)"]

    rows = [head]
    total_amount = 0.0
    total_gst    = 0.0
    total_qty    = 0.0
    for line in lines:
        prod   = line.get("product", "")
        qty    = float(line.get("qty_ordered", 0) or 0)
        rate   = float(line.get("rate", 0) or 0)
        amt    = float(line.get("total_amount", 0) or 0)
        gst    = float(line.get("gst_amount", 0) or 0)
        total_amount += amt
        total_gst    += gst
        total_qty    += qty
        row = [prod, f"{qty:,.0f}"]
        if has_pending:
            d = (dispatched or {}).get(prod, {"qty": 0})
            d_qty = float(d.get("qty", 0) or 0)
            row += [f"{d_qty:,.0f}", f"{max(qty - d_qty, 0):,.0f}"]
        row += [f"{rate:,.2f}"]
        if has_gst:
            row += [f"{gst:,.2f}"]
        row += [f"{amt:,.2f}"]
        rows.append(row)

    total_row = ["TOTAL", f"{total_qty:,.0f}"]
    if has_pending:
        total_row += ["", ""]
    total_row += [""]
    if has_gst:
        total_row += [f"{total_gst:,.2f}"]
    total_row += [f"{total_amount:,.2f}"]
    rows.append(total_row)

    n_cols = len(head)
    prod_w = 55 * mm
    remaining = (174 * mm) - prod_w
    other_w = remaining / (n_cols - 1)
    col_widths = [prod_w] + [other_w] * (n_cols - 1)

    prod_tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    prod_tbl.setStyle(TableStyle([
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.3),
        ("FONTSIZE", (0, 1), (-1, -1), 9.3),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, -1), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, ACCENT),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, ZEBRA]),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, HAIRLINE),
    ]))
    story.append(prod_tbl)
    story.append(Spacer(1, 7 * mm))

    remarks = (header.get("remarks") or "").strip()
    if remarks:
        story.append(Paragraph("REMARKS", ss["SectionLabel"]))
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
    story.append(Spacer(1, 10 * mm))
    story.append(HRFlowable(width="100%", thickness=0.4, color=HAIRLINE))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%d %b %Y, %I:%M %p')}",
        ss["FooterNote"],
    ))

    doc.build(story)
    return buf.getvalue()
