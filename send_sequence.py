"""
Sends the PalmDesk cold-outreach sequence to leads in data/leads.json.
Run daily via GitHub Actions. Uses only the standard library.
"""
import json
import os
import random
import smtplib
import sys
import time
import uuid
from datetime import datetime, timezone
from email.mime.text import MIMEText

LEADS_PATH = os.path.join(os.path.dirname(__file__), "data", "leads.json")

SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]
FROM_NAME = os.environ.get("FROM_NAME", "Jefferson")

FOOTER = (
    "\n\nJefferson\n"
    "PalmDesk | 1309 Coffeen Avenue STE 1200, Sheridan, WY 82801, USA\n"
    "Don't want these? Reply \"unsubscribe\"."
)

TRIAL_LINK = "https://dashboard.palmdesk.me/signup"

STEP_DELAYS_DAYS = [0, 4, 5]  # delay BEFORE sending this step, counted from previous send
MAX_SENDS_PER_RUN = 25  # keep daily volume sane; leftovers roll to the next run automatically


def step_content(step, lead):
    first = lead["first_name"]
    company = lead["company_name"]
    if step == 0:
        subject = f"quick q about {company}'s work orders"
        body = (
            f"Hey {first} — noticed {company} has techs out in the field. "
            "Curious how you're currently handling work orders and getting them "
            "back to the office — spreadsheet, paper, something else?\n\n"
            "We built PalmDesk to take that whole loop (ticket, dispatch, signed "
            "work order, client) down to a few taps for the tech. Worth a 15-min look?"
        )
    elif step == 1:
        subject = f"Re: quick q about {company}'s work orders"
        body = (
            f"Following up — one thing that seems to matter most to teams like "
            f"{company}'s is the client-facing side: techs generate a branded, "
            "signed work-order PDF on-site and it emails itself. No re-typing "
            "anything back at the office.\n\n"
            "Happy to send a 2-min video instead of a call if that's easier."
        )
    elif step == 2:
        subject = "should I close this out?"
        body = (
            "No worries if the timing's off — I'll stop following up. If it's ever "
            f"useful, PalmDesk's free to try for 5 seats, no card needed: {TRIAL_LINK}"
        )
    else:
        return None, None
    return subject, body + FOOTER


def due(lead, now):
    step = lead["step"]
    if step >= len(STEP_DELAYS_DAYS):
        return False
    if step == 0:
        return True  # first send, always due immediately once picked up
    if not lead.get("last_sent_at"):
        return True
    last_sent = datetime.fromisoformat(lead["last_sent_at"])
    gap_days = STEP_DELAYS_DAYS[step]
    return (now - last_sent).days >= gap_days


def send_email(lead, subject, body):
    """Opens its own fresh SMTP connection per email. Hostinger drops idle
    connections faster than our inter-send pacing delay, so a single shared
    connection across the whole batch dies partway through and poisons every
    send after it — reconnecting per email avoids that entirely."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{SMTP_USER}>"
    msg["To"] = lead["email"]
    msg["Message-ID"] = f"<{uuid.uuid4()}@outreach.bloobeach.com>"
    msg["List-Unsubscribe"] = f"<mailto:{SMTP_USER}?subject=unsubscribe>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    thread_id = lead.get("thread_message_id")
    if thread_id and lead["step"] > 0:
        msg["In-Reply-To"] = thread_id
        msg["References"] = thread_id

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.sendmail(SMTP_USER, [lead["email"]], msg.as_string())
    return msg["Message-ID"]


def main():
    with open(LEADS_PATH, "r", encoding="utf-8") as f:
        leads = json.load(f)

    now = datetime.now(timezone.utc)
    sent_count = 0

    for lead in leads:
        if sent_count >= MAX_SENDS_PER_RUN:
            break
        if lead.get("status") != "active":
            continue
        if not due(lead, now):
            continue

        subject, body = step_content(lead["step"], lead)
        if subject is None:
            continue

        try:
            message_id = send_email(lead, subject, body)
        except Exception as e:
            print(f"FAILED to send to {lead['email']}: {e}", file=sys.stderr, flush=True)
            continue

        if lead["step"] == 0:
            lead["thread_message_id"] = message_id
        lead["last_sent_at"] = now.isoformat()
        lead["step"] += 1
        sent_count += 1
        print(f"Sent step {lead['step']} to {lead['email']}", flush=True)

        # gentle pacing between sends (each send opens its own fresh connection,
        # so a longer gap here is safe now — it no longer risks an idle timeout)
        time.sleep(random.uniform(20, 60))

    with open(LEADS_PATH, "w", encoding="utf-8") as f:
        json.dump(leads, f, indent=2)

    print(f"Done. Sent {sent_count} emails this run.")


if __name__ == "__main__":
    main()
