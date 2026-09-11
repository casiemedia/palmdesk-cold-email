# PalmDesk cold email sequence (free, self-hosted)

Replaces Instantly. Sends the 3-step PalmDesk outreach sequence to leads in
`data/leads.json` via your existing Hostinger mailbox (`hello@outreach.bloobeach.com`),
on a free GitHub Actions schedule. No server, no monthly cost.

## What it does

- **`check_replies.py`** — logs into the mailbox over IMAP, looks at recent
  inbox messages, and marks a lead `replied`, `unsubscribed`, or `bounced` so
  they stop getting emailed.
- **`send_sequence.py`** — for every lead still `active`, checks whether their
  next step is due (day 0 → day 4 → day 5 later) and sends it over SMTP.
  Caps at 25 sends per run so volume stays sane; anything left over just goes
  out on the next scheduled run.
- **`.github/workflows/sequence.yml`** — runs both scripts once a day
  (14:00 UTC) and commits the updated `data/leads.json` back to the repo, so
  state (who's on which step, who replied) persists between runs for free.

## One-time setup

1. Create a **private** GitHub repo and push this folder to it.
2. In the repo, go to **Settings → Secrets and variables → Actions** and add:
   - `SMTP_USER` — `hello@outreach.bloobeach.com`
   - `SMTP_PASS` — the mailbox password (the same one used for Instantly)
3. That's it — the workflow already knows the Hostinger host/ports
   (`smtp.hostinger.com:465`, `imap.hostinger.com:993`).
4. To fire it once immediately instead of waiting for the daily schedule, go
   to the **Actions** tab → "Cold email sequence" → **Run workflow**.

## Adding new leads later

For now, adding new leads is manual: append rows to `data/leads.json` in this
shape and commit:

```json
{
  "email": "person@company.com",
  "first_name": "First",
  "last_name": "Last",
  "company_name": "Company",
  "status": "active",
  "step": 0,
  "last_sent_at": null
}
```

Clay can still enrich and validate new leads the same way it did before — you'd
just export/paste the new rows in here instead of pushing to Instantly. If you
want this fully automated again (Clay auto-pushing new leads with zero manual
step), that's a follow-up: Clay would call GitHub's `repository_dispatch` API
with a personal access token, and a second workflow would append the row
automatically. Ask if you want that built.

## Trade-offs vs. Instantly (be aware)

- **No warmup network.** The mailbox already has ~2 weeks of warmup reputation
  from Instantly, which carries over since it's tied to the mailbox/domain, not
  the tool. But there's no ongoing automated warmup here — if you add a lot of
  new volume later, ramp it up gradually.
- **Reply/bounce detection is simpler.** It's a keyword-based IMAP scan, not a
  polished inbox — check in occasionally that it's not misclassifying anything.
- **No dashboard.** Open `data/leads.json` to see where each lead stands, or
  check the Actions tab's run logs.
