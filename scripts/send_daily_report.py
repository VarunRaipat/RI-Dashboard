"""
Daily RI report — runs via GitHub Actions and lands in the inbox at 20:00 IST.
Fetches the day's production + dispatch from Supabase and emails the owner.

GitHub's cron only starts a job *near* the requested time, so the workflow
triggers ~25 min early and this script sleeps until exactly 20:00:00 IST.
A `report_log` row per report date stops the backstop run from re-sending.
Set REPORT_FORCE=true (manual workflow input) to skip the wait and the guard.
"""
import hashlib
import os
import re
import requests
import smtplib
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
GMAIL_USER   = os.environ["GMAIL_USER"]
GMAIL_PASS   = os.environ["GMAIL_APP_PASSWORD"]


# Addresses that must not receive the report even while REPORT_TO_EMAIL still
# lists them, held as SHA-256 of the lowercased address rather than in the
# clear — this repository is public, and writing an address here in plain text
# would publish it to every scraper that walks GitHub.
#
# This is a backstop, not the way recipients are meant to be managed: taking
# an address out of the REPORT_TO_EMAIL secret is the real fix, and the
# matching entry here should be deleted once that's done. It exists because
# the secret can only be edited by hand in the repository settings, and two
# addresses that had been added to it needed to come off straight away.
EXCLUDED_SHA256 = {
    # both requested for removal 19 Aug 2026
    "4e51c56708fce2467b9e117fe336e5d3cb57a1ddb79d6c30b7464b93635e0967",
    "ea47449a8b0ecae3e02de2ff767c57881fad548f1cca919bcb0f3e837953d612",
}


def _excluded(address):
    return hashlib.sha256(address.strip().lower().encode()).hexdigest() in EXCLUDED_SHA256


def _recipients(raw):
    """The report can go to more than one person: set REPORT_TO_EMAIL to a
    comma- or semicolon-separated list ("owner@x.com, manager@y.com").
    A single address still works exactly as before, and an unset or blank
    value falls back to the sending account. Blanks left by a trailing
    separator are dropped and repeats removed, so nobody gets two copies
    of the same report. Anything listed in EXCLUDED_SHA256 is dropped,
    matched on the lowercased address so a differently-cased spelling in the
    secret can't slip past.

    Returns (addresses, excluded_count) — the count is only used to say in
    the run log that the filter did something, so an address quietly not
    arriving is traceable to this file rather than looking like a bug."""
    parts = [p.strip() for p in re.split(r"[,;]", raw or "")]
    present = list(dict.fromkeys(p for p in parts if p))
    kept = [p for p in present if not _excluded(p)]
    return (kept or [GMAIL_USER]), len(present) - len(kept)


TO_EMAILS, EXCLUDED_COUNT = _recipients(os.environ.get("REPORT_TO_EMAIL"))

REPORT_DATE = None        # settled below, once IST and _report_date() exist
TODAY = None
LAKH  = 100_000

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
}


# Seconds before a stalled Supabase read or Gmail handshake is given up on.
# Neither call had a timeout, so a connection that opened and then went quiet
# would hang the job until GitHub's 6-hour default killed it — a whole day
# with no report and nothing in the log to say why.
HTTP_TIMEOUT = 30
SMTP_TIMEOUT = 60

IST         = ZoneInfo("Asia/Kolkata")
SEND_HOUR   = 20          # report is sent at 20:00:00 IST
MAX_WAIT_S  = 65 * 60     # never sleep longer than this (workflow timeout is 80 min)
FORCE       = os.environ.get("REPORT_FORCE", "").strip().lower() in ("1", "true", "yes")


def _report_date():
    """The day the report covers: the day before the 20:00 IST slot this run
    is servicing.

    Read off the IST clock, never the runner's UTC clock. The two agree while
    the job runs near 14:30 UTC, which is why this went unnoticed — but once a
    runner passes 18:30 UTC the IST date has already rolled into tomorrow
    while the UTC date has not, and a UTC-based date then names the day
    before the one wanted. With jobs starting around 19:50 UTC, the report
    was covering 7 Oct while IST was already the 9th.

    _should_send() guarantees this is only reached within MAX_WAIT_S of
    today's 20:00 IST or after it — never 18 hours early — so "the slot this
    run services" is always today in IST."""
    return datetime.now(IST).date() - timedelta(days=1)


def _should_send():
    """Sleep until 20:00:00 IST and return True, or return False if this run
    is too early to be the one that sends.

    GitHub starts these jobs hours after their cron time, so the next 20:00
    IST is routinely most of a day away — far longer than a runner should be
    held open. That case used to fall through to "sending now instead",
    which is why the report had been arriving around 01:48 IST carrying the
    wrong day. Such a run now exits and leaves the send to a later trigger.

    The crons are spread across the window jobs actually start in, so one
    lands inside MAX_WAIT_S of 20:00 IST and sends exactly on time; if every
    one of them overshoots, the first past 20:00 sends immediately rather
    than skipping the day. The report_log row keeps the rest from
    re-sending."""
    if FORCE:
        return True
    now = datetime.now(IST)
    target = now.replace(hour=SEND_HOUR, minute=0, second=0, microsecond=0)
    wait = (target - now).total_seconds()
    if wait <= 0:
        print(f"Past 20:00 IST (now {now:%H:%M} IST) - sending now")
        return True
    if wait > MAX_WAIT_S:
        print(f"20:00 IST is {wait/60:.0f} min away, over the {MAX_WAIT_S/60:.0f} min cap - "
              f"too early to be the sending run, leaving it to a later trigger")
        return False
    print(f"Waiting {wait:.0f}s until 20:00 IST")
    time.sleep(wait)
    return True


# Set at import so the module is usable on its own (tests, a REPL), and set
# again in __main__ after the wait, which is the point at which the slot this
# run services is settled.
REPORT_DATE = _report_date()
TODAY = str(REPORT_DATE)


def _already_sent():
    """True if a report for REPORT_DATE is already logged. A missing report_log
    table (migration not run yet) disables the guard rather than the report."""
    r = requests.get(f"{SUPABASE_URL}/rest/v1/report_log", headers=HEADERS,
                     params={"select": "report_date", "report_date": f"eq.{TODAY}"},
                     timeout=HTTP_TIMEOUT)
    if r.status_code == 404:
        print("report_log table missing - double-send guard disabled")
        return False
    if r.status_code != 200:
        raise RuntimeError(f"report_log read failed with HTTP {r.status_code}: {r.text[:300]}")
    return bool(r.json())


def _mark_sent():
    r = requests.post(f"{SUPABASE_URL}/rest/v1/report_log", headers=HEADERS,
                      json={"report_date": TODAY}, timeout=HTTP_TIMEOUT)
    if r.status_code not in (200, 201, 204, 404):
        print(f"Warning: could not write report_log (HTTP {r.status_code})")


def _fetch(table, date_filter=True, date_col="date"):
    params = {"select": "*", "limit": "1000"}
    if date_filter:
        params[date_col] = f"eq.{TODAY}"
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=HEADERS, params=params,
                     timeout=HTTP_TIMEOUT)
    # Fail the run rather than returning no rows. An empty list here is
    # indistinguishable from a genuinely quiet day, so a failed read used to
    # send a perfectly normal-looking report claiming zero production, ₹0
    # profit and "No production recorded today" — worse than no report at
    # all, because nobody can tell it's wrong. A non-zero exit makes GitHub
    # send its workflow-failure notification instead.
    if r.status_code != 200:
        raise RuntimeError(
            f"Supabase read of '{table}' failed with HTTP {r.status_code}: "
            f"{r.text[:300]}"
        )
    return r.json()


def _count_of(resp):
    """Total row count from a PostgREST `Prefer: count=exact` response, which
    reports it in Content-Range as "0-0/1234" (or "*/0" for no rows)."""
    cr = resp.headers.get("Content-Range", "")
    return cr.rsplit("/", 1)[-1] if "/" in cr else "?"


def _census(table, date_col="date"):
    """One line per table saying what the report could actually see.

    An all-zero report has causes that look identical in the inbox: nothing
    happened that day, the read came back empty because it was blocked (row
    level security switched on in the Supabase dashboard, a key that no
    longer reaches the table), or it was aimed at a date with no rows. The
    HTTP layer cannot separate them — all three answer 200 with an empty
    list. Rows-for-the-date vs rows-at-all vs latest-date-present can.

    Diagnostics must never be what breaks the report, so any failure here is
    swallowed after saying so."""
    head = dict(HEADERS)
    head["Prefer"] = "count=exact"
    try:
        for_date = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=head,
                                params={"select": date_col, date_col: f"eq.{TODAY}",
                                        "limit": "1"}, timeout=HTTP_TIMEOUT)
        overall = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=head,
                               params={"select": date_col, "limit": "1"},
                               timeout=HTTP_TIMEOUT)
        newest = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=HEADERS,
                              params={"select": date_col, "order": f"{date_col}.desc",
                                      "limit": "1"}, timeout=HTTP_TIMEOUT)
        rows = newest.json() if newest.status_code == 200 else []
        latest = rows[0].get(date_col) if rows else "none"
        print(f"census {table}: {_count_of(for_date)} row(s) for {TODAY} | "
              f"{_count_of(overall)} row(s) total | latest {latest} | "
              f"http {for_date.status_code}/{overall.status_code}/{newest.status_code}")
    except Exception as exc:
        print(f"census {table}: unavailable ({type(exc).__name__}: {exc})")


def build_email():
    # Printed before the reads so a zero report can be diagnosed from the run
    # log alone. REPORT_DATE comes off the UTC clock while the send time is
    # IST, so showing both makes any skew between them obvious.
    now_utc = datetime.now(tz=ZoneInfo("UTC"))
    print(f"clock: UTC {now_utc:%Y-%m-%d %H:%M} | IST {now_utc.astimezone(IST):%Y-%m-%d %H:%M} "
          f"| reporting on {TODAY}")
    _census("production")
    _census("dispatch")
    _census("orders", date_col="order_date")

    prod_rows  = _fetch("production")
    disp_rows  = _fetch("dispatch")
    order_rows = _fetch("orders", date_col="order_date")

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
    # Cancelled challans are ₹0 voids that never left the yard — keep them out
    # of both the value and the trip count.
    live_disp = [r for r in disp_rows
                 if str(r.get("status") or "active").lower() != "cancelled"]
    total_dispatch = sum(r.get("dispatch_value", 0) for r in live_disp)
    dispatch_trips = len(live_disp)

    # ── Orders booked today ───────────────────────────────────────────────────
    # One order (DI) spans several rows — one per product line — so the order
    # count is the number of distinct di_no values, not len(order_rows).
    order_count  = len({r.get("di_no") for r in order_rows if r.get("di_no")})
    orders_value = sum(r.get("total_amount", 0) for r in order_rows)

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
                    <tr>
                      <td style="font-size:12px;color:#7A6565;padding-top:6px;border-top:1px solid rgba(139,36,40,0.12);">Orders Booked</td>
                      <td style="font-size:13px;color:#F2EDED;font-weight:600;text-align:right;padding-top:6px;border-top:1px solid rgba(139,36,40,0.12);">{order_count} &nbsp;·&nbsp; ₹{orders_value:,.0f}</td>
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
    # Everyone on the list is a visible To: recipient — this is an internal
    # report going to colleagues who know each other, so there's no reason
    # to hide the list behind Bcc.
    msg["To"]      = ", ".join(TO_EMAILS)
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=SMTP_TIMEOUT) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, TO_EMAILS, msg.as_string())

    # Count only, never the addresses themselves: this repository is public,
    # which makes its Actions logs public too, so printing the recipient list
    # published everyone's address to anyone who opened a run.
    print(f"Report sent to {len(TO_EMAILS)} recipient(s)")
    if EXCLUDED_COUNT:
        print(f"{EXCLUDED_COUNT} address(es) in REPORT_TO_EMAIL were skipped by "
              f"EXCLUDED_SHA256 in scripts/send_daily_report.py — remove them from "
              f"the secret and delete the matching entries there to retire the filter.")


if __name__ == "__main__":
    if not _should_send():
        sys.exit(0)
    REPORT_DATE = _report_date()
    TODAY = str(REPORT_DATE)
    if not FORCE and _already_sent():
        print(f"Report for {TODAY} already sent - nothing to do")
        sys.exit(0)
    html, no_prod = build_email()   # built after the wait so it sees the latest data
    send_email(html, no_prod)
    _mark_sent()
    sys.exit(0)
