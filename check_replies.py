"""
Scans the mailbox inbox for replies, unsubscribe requests, and bounces from
leads in data/leads.json, and updates their status so send_sequence.py stops
emailing them. Run before send_sequence.py on the same schedule.
"""
import email
import imaplib
import json
import os
import re
from email.header import decode_header

LEADS_PATH = os.path.join(os.path.dirname(__file__), "data", "leads.json")

IMAP_HOST = os.environ["IMAP_HOST"]
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_USER = os.environ["SMTP_USER"]
IMAP_PASS = os.environ["SMTP_PASS"]

UNSUBSCRIBE_RE = re.compile(r"unsubscribe|stop emailing|remove me|\bstop\b", re.IGNORECASE)
BOUNCE_SENDER_RE = re.compile(r"mailer-daemon|postmaster|mail delivery", re.IGNORECASE)


def decode(value):
    if not value:
        return ""
    parts = decode_header(value)
    out = ""
    for text, enc in parts:
        if isinstance(text, bytes):
            out += text.decode(enc or "utf-8", errors="ignore")
        else:
            out += text
    return out


def get_body_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(errors="ignore")
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(errors="ignore")
    except Exception:
        return ""


def main():
    with open(LEADS_PATH, "r", encoding="utf-8") as f:
        leads = json.load(f)

    lead_by_email = {l["email"].lower(): l for l in leads if l.get("status") == "active"}
    if not lead_by_email:
        print("No active leads to check against.")
        return

    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    imap.login(IMAP_USER, IMAP_PASS)
    imap.select("INBOX")

    # only look at the last 200 messages to keep this fast
    status, data = imap.search(None, "ALL")
    ids = data[0].split()[-200:]

    changed = 0
    for msg_id in ids:
        status, msg_data = imap.fetch(msg_id, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])

        from_header = decode(msg.get("From", ""))
        from_match = re.search(r"[\w\.\+-]+@[\w\.-]+", from_header)
        sender = from_match.group(0).lower() if from_match else ""

        subject = decode(msg.get("Subject", ""))
        body = get_body_text(msg)

        # bounce: sender is a mail daemon, body mentions one of our lead addresses
        if BOUNCE_SENDER_RE.search(from_header):
            for addr, lead in lead_by_email.items():
                if addr in body.lower() or addr in subject.lower():
                    lead["status"] = "bounced"
                    changed += 1
            continue

        lead = lead_by_email.get(sender)
        if not lead:
            continue

        if UNSUBSCRIBE_RE.search(subject) or UNSUBSCRIBE_RE.search(body):
            lead["status"] = "unsubscribed"
            changed += 1
            print(f"Unsubscribed: {sender}")
        else:
            lead["status"] = "replied"
            changed += 1
            print(f"Replied, stopping sequence: {sender}")

    imap.logout()

    if changed:
        with open(LEADS_PATH, "w", encoding="utf-8") as f:
            json.dump(leads, f, indent=2)
    print(f"Done. Updated {changed} lead(s).")


if __name__ == "__main__":
    main()
