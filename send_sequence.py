"""
Sends the PalmDesk cold-outreach sequence to leads in data/leads.json.
Run every few hours via GitHub Actions, but only sends ONE email per run;
volume comes from how often the workflow fires, not from looping inside the
script. Uses only the standard library.

Hardened after hello@outreach.bloobeach.com got suspended by Hostinger for
spam-like sending: the original version sent up to 25 near-identical emails
in a single ~15-minute burst, which is close to a textbook spam signature to
an automated abuse filter, especially from a mailbox with no ongoing warmup.
This version sends at most one email per invocation, only during a
business-hours window, and picks from a few subject/body variants per step
so it isn't the exact same template every time.
"""
import imaplib
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
IMAP_HOST = os.environ.get("IMAP_HOST", "imap.hostinger.com")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))

# Hostinger/Titan's Sent folder name varies by mailbox setup; try each until
# one accepts the APPEND. Best-effort only; a failure here never blocks the
# actual send, since the email already went out via SMTP by that point.
SENT_FOLDER_CANDIDATES = ["Sent", "INBOX.Sent", "Sent Items", "INBOX.Sent Items"]

FOOTER = (
    "\n\n"
    "I hope to hear from you soon. If these emails are out of line, let me "
    "know by replying \"stop\".\n\n"
    "Jefferson Geerman\n"
    "Founder, PalmDesk | Bloo Beach Softwares LLC\n"
    "palmdesk@bloobeach.com | 1-409-934-7648 | palmdesk.me\n"
    "PalmDesk, 1309 Coffeen Avenue STE 1200, Sheridan, WY 82801, USA"
)

STEP_DELAYS_DAYS = [0, 4, 5]  # delay BEFORE sending this step, counted from previous send
BUSINESS_HOURS_UTC = range(13, 23)  # ~8am-6pm US Eastern; skip nights entirely

STEP0_VARIANTS = [
    (
        "how does {company} handle work orders today?",
        "Hi {first},\n\n"
        "Quick one: when a tech at {company} finishes a job, how does the "
        "paperwork get back to the office? Still spreadsheets, paper, or "
        "something else?\n\n"
        "I'm working on PalmDesk, a field service management platform, and "
        "I'm reaching out to businesses like yours to learn how they handle "
        "dispatching and job tracking. We're trying to understand what "
        "works, what doesn't, and what we could do better.\n\n"
        "Would you be open to a quick chat this week? I'd love to hear how "
        "you handle things on your end.",
    ),
    (
        "quick q about {company}'s work orders",
        "Hi {first},\n\n"
        "I noticed {company} has techs out in the field, so curious how "
        "you're currently handling dispatching and getting paperwork back "
        "to the office: spreadsheet, paper, something else?\n\n"
        "I'm building PalmDesk, a field service management platform, and "
        "I'm talking to teams like yours to understand how this actually "
        "works day to day, what's painful, and what's fine as-is.\n\n"
        "Would you be open to a quick call this week? I'd really value "
        "hearing how you do it.",
    ),
]

STEP1_VARIANTS = [
    (
        "Re: how does {company} handle work orders today?",
        "Following up on this, still curious how {company} handles the "
        "handoff once a job wraps up. Most of the teams I've talked to end "
        "up re-typing the same info back at the office.\n\n"
        "If you've got 15 minutes this week, I'd love to hear how you do it "
        "and share what I'm learning from other teams.",
    ),
    (
        "Re: quick q about {company}'s work orders",
        "Circling back, still hoping to hear how {company} handles job "
        "paperwork day to day. Most of what I'm learning right now is from "
        "conversations like this one.\n\n"
        "Open to a quick call sometime this week?",
    ),
]

STEP2_VARIANTS = [
    (
        "should I close this out?",
        "No worries if the timing's off, I'll stop following up. If it's "
        "ever useful to trade notes on how {company} handles dispatching, "
        "just reply and we'll grab 15 minutes.",
    ),
    (
        "last note from me",
        "I'll leave it here so I'm not cluttering your inbox. If you're "
        "ever up for a quick chat about how {company} handles job "
        "tracking, just reply and we'll find a time.",
    ),
]


def step_content(step, lead):
    first = lead["first_name"]
    company = lead["company_name"]
    variants = {0: STEP0_VARIANTS, 1: STEP1_VARIANTS, 2: STEP2_VARIANTS}.get(step)
    if variants is None:
        return None, None
    subject_t, body_t = random.choice(variants)
    subject = subject_t.format(company=company)
    body = body_t.format(first=first, company=company)
    return subject, body


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


def save_to_sent(raw_message_bytes):
    """Best-effort copy into the IMAP Sent folder. Raw SMTP submission (what
    send_email uses) never does this on its own; a real mail client does it
    as a separate step after sending, which is why nothing showed up in the
    Sent view even though the emails genuinely went out."""
    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30)
        imap.login(SMTP_USER, SMTP_PASS)
        internal_date = imaplib.Time2Internaldate(time.time())
        for folder in SENT_FOLDER_CANDIDATES:
            status, _ = imap.append(folder, r"(\Seen)", internal_date, raw_message_bytes)
            if status == "OK":
                imap.logout()
                return folder
        imap.logout()
    except Exception as e:
        print(f"WARNING: could not save a copy to Sent: {e}", file=sys.stderr, flush=True)
    return None


def send_email(lead, subject, core_body):
    plain_body = core_body + FOOTER

    msg = MIMEText(plain_body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{SMTP_USER}>"
    msg["To"] = lead["email"]
    msg["Message-ID"] = f"<{uuid.uuid4()}@outreach.bloobeach.com>"

    thread_id = lead.get("thread_message_id")
    if thread_id and lead["step"] > 0:
        msg["In-Reply-To"] = thread_id
        msg["References"] = thread_id

    raw = msg.as_bytes()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.sendmail(SMTP_USER, [lead["email"]], raw)

    saved_to = save_to_sent(raw)
    if saved_to:
        print(f"Saved a copy to Sent folder '{saved_to}'", flush=True)
    else:
        print("Could not confirm a Sent-folder copy (see warning above, if any)", flush=True)

    return msg["Message-ID"]


def main():
    now = datetime.now(timezone.utc)

    test_email = os.environ.get("TEST_EMAIL", "").strip()
    if test_email:
        lead = {
            "email": test_email,
            "first_name": "there",
            "company_name": "your company",
            "step": 0,
            "thread_message_id": None,
        }
        subject, body = step_content(0, lead)
        try:
            send_email(lead, subject, body)
        except Exception as e:
            print(f"TEST SEND FAILED to {test_email}: {e}", file=sys.stderr, flush=True)
            raise
        print(f"TEST MODE: sent a Step 0 preview to {test_email}. leads.json untouched.", flush=True)
        return

    if now.hour not in BUSINESS_HOURS_UTC:
        print(f"Outside business-hours window (hour={now.hour} UTC). Skipping this run.")
        return

    with open(LEADS_PATH, "r", encoding="utf-8") as f:
        leads = json.load(f)

    candidates = [l for l in leads if l.get("status") == "active" and due(l, now)]
    if not candidates:
        print("No leads due right now.")
        return

    random.shuffle(candidates)
    lead = candidates[0]

    subject, body = step_content(lead["step"], lead)
    if subject is None:
        print(f"{lead['email']} has no more steps; leaving as-is.")
        return

    try:
        message_id = send_email(lead, subject, body)
    except Exception as e:
        print(f"FAILED to send to {lead['email']}: {e}", file=sys.stderr, flush=True)
        return

    if lead["step"] == 0:
        lead["thread_message_id"] = message_id
    lead["last_sent_at"] = now.isoformat()
    lead["step"] += 1
    print(f"Sent step {lead['step']} to {lead['email']}", flush=True)

    with open(LEADS_PATH, "w", encoding="utf-8") as f:
        json.dump(leads, f, indent=2)


if __name__ == "__main__":
    main()
