"""
Daily RI report — runs via GitHub Actions every evening at 8pm IST.
Fetches today's production + dispatch from Supabase and emails the owner.
"""
import os
import requests
import smtplib
import sys
from datetime import date, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
GMAIL_USER   = os.environ["GMAIL_USER"]
GMAIL_PASS   = os.environ["GMAIL_APP_PASSWORD"]
TO_EMAIL     = os.environ.get("REPORT_TO_EMAIL", GMAIL_USER)

REPORT_DATE = date.today() - timedelta(days=1)
TODAY = str(REPORT_DATE)
LAKH  = 100_000

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}


def _fetch(table, date_filter=True):
    params = {"select": "*", "limit": "1000"}
    if date_filter:
        params["date"] = f"eq.{TODAY}"
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=HEADERS, params=params)
    if r.status_code != 200:
        return []
    return r.json()


def build_email():
    prod_rows = _fetch("production")
    disp_rows = _fetch("dispatch")

    # ── Production totals ─────────────────────────────────────────────────────
    total_nos     = sum(r.get("nos", 0) for r in prod_rows)
    total_revenue = sum(r.get("revenue", 0) for r in prod_rows)
    total_cost    = sum(r.get("total_cost", 0) for r in prod_rows)
    total_profit  = sum(r.get("profit", 0) for r in prod_rows)
    profit_pct    = (total_profit / total_revenue * 100) if total_revenue else 0

    # Product breakdown
    product_nos = {}
    for r in prod_rows:
        p = r.get("product", "Unknown")
        product_nos[p] = product_nos.get(p, 0) + r.get("nos", 0)

    # ── Dispatch totals ───────────────────────────────────────────────────────
    total_dispatch = sum(r.get("dispatch_value", 0) for r in disp_rows)
    dispatch_trips = len(disp_rows)

    # ── Colour logic ──────────────────────────────────────────────────────────
    profit_color = "#27AE60" if total_profit >= 0 else "#E05252"
    profit_label = "PROFIT" if total_profit >= 0 else "LOSS"
    no_prod = len(prod_rows) == 0

    # ── Product rows HTML ─────────────────────────────────────────────────────
    prod_rows_html = ""
    for prod, nos in sorted(product_nos.items(), key=lambda x: -x[1]):
        prod_rows_html += f"""
        <tr>
          <td style="padding:8px 12px;color:#C4AEAE;font-size:13px;">{prod}</td>
          <td style="padding:8px 12px;color:#F2EDED;font-size:13px;text-align:right;font-weight:600;">{nos:,} nos</td>
        </tr>"""

    if not prod_rows_html:
        prod_rows_html = '<tr><td colspan="2" style="padding:12px;color:#5A4848;text-align:center;font-size:13px;">No production recorded today</td></tr>'

    # ── Full HTML email ───────────────────────────────────────────────────────
    formatted_date = REPORT_DATE.strftime("%A, %d %B %Y")

    html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0D0B0B;font-family:'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0D0B0B;padding:32px 16px;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#141010;border-radius:16px;border:1px solid rgba(139,36,40,0.22);border-top:4px solid #8B2428;overflow:hidden;">

        <!-- Header -->
        <tr>
          <td style="padding:28px 32px 20px;border-bottom:1px solid rgba(139,36,40,0.15);">
            <div style="font-size:22px;font-weight:800;color:#F2EDED;letter-spacing:-0.02em;">RI</div>
            <div style="font-size:11px;color:#5A4848;letter-spacing:0.14em;text-transform:uppercase;margin-top:3px;">Daily Operations Report</div>
            <div style="font-size:12px;color:#7A6565;margin-top:6px;">{formatted_date}</div>
          </td>
        </tr>

        <!-- KPI Row -->
        <tr>
          <td style="padding:24px 32px;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td width="33%" style="text-align:center;padding:0 8px;">
                  <div style="font-size:11px;color:#5A4848;letter-spacing:0.12em;text-transform:uppercase;margin-bottom:6px;">Production</div>
                  <div style="font-size:28px;font-weight:800;color:#F2EDED;letter-spacing:-0.03em;">{total_nos:,}</div>
                  <div style="font-size:11px;color:#7A6565;margin-top:2px;">nos</div>
                </td>
                <td width="1" style="background:rgba(139,36,40,0.20);"></td>
                <td width="33%" style="text-align:center;padding:0 8px;">
                  <div style="font-size:11px;color:#5A4848;letter-spacing:0.12em;text-transform:uppercase;margin-bottom:6px;">{profit_label}</div>
                  <div style="font-size:28px;font-weight:800;color:{profit_color};letter-spacing:-0.03em;">₹{abs(total_profit):,.0f}</div>
                  <div style="font-size:11px;color:#7A6565;margin-top:2px;">{profit_pct:.1f}% margin</div>
                </td>
                <td width="1" style="background:rgba(139,36,40,0.20);"></td>
                <td width="33%" style="text-align:center;padding:0 8px;">
                  <div style="font-size:11px;color:#5A4848;letter-spacing:0.12em;text-transform:uppercase;margin-bottom:6px;">Dispatched</div>
                  <div style="font-size:28px;font-weight:800;color:#3B82F6;letter-spacing:-0.03em;">₹{total_dispatch:,.0f}</div>
                  <div style="font-size:11px;color:#7A6565;margin-top:2px;">{dispatch_trips} trip(s)</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Revenue vs Cost -->
        <tr>
          <td style="padding:0 32px 20px;">
            <table width="100%" cellpadding="0" cellspacing="0" style="background:rgba(139,36,40,0.06);border:1px solid rgba(139,36,40,0.12);border-radius:10px;">
              <tr>
                <td style="padding:12px 16px;">
                  <table width="100%">
                    <tr>
                      <td style="font-size:12px;color:#7A6565;">Production Value</td>
                      <td style="font-size:13px;color:#F2EDED;font-weight:600;text-align:right;">₹{total_revenue:,.0f}</td>
                    </tr>
                    <tr>
                      <td style="font-size:12px;color:#7A6565;padding-top:6px;">Total Cost</td>
                      <td style="font-size:13px;color:#F2EDED;font-weight:600;text-align:right;padding-top:6px;">₹{total_cost:,.0f}</td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Product breakdown -->
        <tr>
          <td style="padding:0 32px 24px;">
            <div style="font-size:10px;font-weight:700;color:#C8575B;letter-spacing:0.14em;text-transform:uppercase;border-left:3px solid #8B2428;padding-left:10px;margin-bottom:10px;">Product Breakdown</div>
            <table width="100%" cellpadding="0" cellspacing="0" style="background:#181212;border-radius:8px;border:1px solid rgba(139,36,40,0.12);">
              {prod_rows_html}
            </table>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:16px 32px;border-top:1px solid rgba(139,36,40,0.12);text-align:center;">
            <div style="font-size:10px;color:#3A2A2A;letter-spacing:0.12em;text-transform:uppercase;">RI · RAMESHWARAM INDUSTRIES · Automated Daily Report</div>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>
"""
    return html, no_prod


def send_email(html, no_prod):
    subject_flag = "⚠️ No Production" if no_prod else "✅"
    subject = f"{subject_flag} RI Daily Report — {REPORT_DATE.strftime('%d %b %Y')}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"RI Reports <{GMAIL_USER}>"
    msg["To"]      = TO_EMAIL
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, TO_EMAIL, msg.as_string())

    print(f"Report sent to {TO_EMAIL}")


if __name__ == "__main__":
    html, no_prod = build_email()
    send_email(html, no_prod)
    sys.exit(0)
