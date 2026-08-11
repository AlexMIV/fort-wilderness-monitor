# Fort Wilderness Availability Monitor

Checks Disney World's internal resort-availability endpoint every **5 minutes**
for this exact search:

- Check-in: **December 30, 2026**
- Check-out: **January 1, 2027**
- Guests: **2 adults**
- Resort: **The Campsites at Disney's Fort Wilderness Resort**
- Alert for:
  - **Premium Campsite**
  - **Premium Meadow Campsite**
  - **Full Hook-Up Campsite**

When one of those campsite types changes from unavailable to available, the
workflow sends one email. It does **not** send the same alert again every five
minutes. If an available type later disappears, the state resets so a future
reappearance can generate another alert.

## Important

Disney's `/wdpr-resorts-list-api/` endpoint is an internal website endpoint,
not a published/supported developer API. Disney can change it at any time.
This monitor deliberately fails loudly if the endpoint stops returning the
expected structure rather than incorrectly treating an API failure as
"no availability."

GitHub scheduled Actions can also be delayed during periods of high GitHub
load; a 5-minute cron is a requested cadence, not a guaranteed exactly-every-
five-minutes service.

## Files

- `checker.py` — Disney availability checker and duplicate-alert logic
- `test_email.py` — sends a test alert using your GitHub secrets
- `config.json` — your exact dates, party size, and campsite filters
- `state/availability.json` — remembers currently available campsite types
- `.github/workflows/check-availability.yml` — runs every 5 minutes
- `state/heartbeat.txt` — monthly keep-alive commit for a public repository
- `requirements.txt` — Python dependency


## Why the schedule starts at minute 2

The cron expression is `2-57/5 * * * *`, which means minutes 2, 7, 12, 17,
22, 27, 32, 37, 42, 47, 52, and 57 of every hour. It is still every five
minutes, but avoids minute 0 because GitHub documents that scheduled workflows
can be delayed during high-load periods, especially at the start of the hour.

## Public-repository heartbeat

For this to stay free at a five-minute cadence, a public repository is the
practical GitHub-hosted-runner choice. GitHub documents that public scheduled
workflows may be disabled after 60 days with no repository activity.

This project therefore updates `state/heartbeat.txt` once per calendar month
and commits it. That gives the repository ongoing activity even if campsite
availability never changes.

## Email configuration

The workflow uses standard SMTP. For Gmail:

- `SMTP_HOST` = `smtp.gmail.com`
- `SMTP_PORT` = `465`
- `SMTP_USERNAME` = your Gmail address
- `SMTP_PASSWORD` = a Google **App Password**, not your normal Google password
- `ALERT_FROM` = your Gmail address
- `ALERT_TO` = the email address where you want the alert

Google App Passwords generally require 2-Step Verification on the Google
account. Never put the password directly in this repository; save it only as a
GitHub Actions secret.

## Manual test

In GitHub:

1. Open **Actions**.
2. Select **Fort Wilderness Availability Monitor**.
3. Click **Run workflow**.
4. Turn on **Send a test email instead of checking Disney**.
5. Click **Run workflow**.
6. Open the run and confirm all steps are green.
7. Confirm the test email arrived.

Then manually run it once again with the test-email option **off**. The log
should show the exact dates and campsite filters being checked.

## Duplicate-alert behavior

`state/availability.json` initially contains:

```json
{
  "available": []
}
```

If Premium Meadow becomes available, it changes to approximately:

```json
{
  "available": [
    "premium meadow campsite"
  ],
  "...": "..."
}
```

The workflow commits that change to the repository. On the next 5-minute
check, the script sees Premium Meadow was already available and sends no
duplicate email.

If it disappears, the state is committed back to unavailable. If it later
appears again, a new email is sent.

## Stop monitoring

Go to **Actions → Fort Wilderness Availability Monitor → ... → Disable workflow**.

You can also delete the schedule from
`.github/workflows/check-availability.yml`.
