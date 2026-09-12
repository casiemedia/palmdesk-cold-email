"""
Sends the PalmDesk cold-outreach sequence to leads in data/leads.json.
Run every few hours via GitHub Actions, but only sends ONE email per run —
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
import json
import os
import random
import smtplib
import sys
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
BUSINESS_HOURS_UTC = range(13, 23)  # ~8am-6pm US Eastern; skip nights entirely

STEP0_VARIANTS = [
    (
        "quick q about {company}'s work orders",
        "Hey {first} — noticed {company} has techs out in the field. Curious how "
        "you're currently handling work orders and getting them back to the "
        "office — spreadsheet, paper, something else?\n\n"
        "We built PalmDesk to take that whole loop (ticket, dispatch, signed "
        "work order, client) down to a few taps for the tech. Worth a 15-min look?",
    ),
    (
        "how does {company} handle work orders today?",
        "Hi {first} — quick one: when a tech at {company} finishes a job, how "
        "does the paperwork get back to the office? Still spreadsheets or paper?\n\n"
        "We built PalmDesk so techs can dispatch, log the job, and get a signed "
        "work order out to the client in a few taps. Open to a quick look?",
    ),
]

STEP1_VARIANTS = [
    (
        "Re: quick q about {company}'s work orders",
        "Following up — one thing that seems to matter most to teams like "
        "{company}'s is the client-facing side: techs generate a branded, "
        "signed work-order PDF on-site and it emails itself. No re-typing "
        "anything back at the office.\n\n"
        "Happy to send a 2-min video instead of a call if that's easier.",
    ),
    (
        "Re: how does {company} handle work orders today?",
        "Circling back on this — the part that usually lands well for teams "
        "like {company}'s is that the client gets a branded, signed work order "
        "by email the moment the tech finishes, no office re-entry needed.\n\n"
        "If a call's easier than reading, happy to just send a short video instead.",
    ),
]

STEP2_VARIANTS = [
    (
        "should I close this out?",
        "No worries if the timing's off — I'll stop following up. If it's ever "
        "useful, PalmDesk's free to try for 5 seats, no card needed: {trial_link}",
    ),
    (
        "last note from me",
        "I'll leave it here so I'm not cluttering your inbox. If this ever "
        "becomes useful, PalmDesk's free to try for 5 seats, no card needed: "
        "{trial_link}",
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
    body = body_t.format(first=first, company=company, trial_link=TRIAL_LINK)
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
    now = datetime.now(timezone.utc)

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
