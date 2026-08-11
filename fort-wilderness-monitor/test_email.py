#!/usr/bin/env python3
import os
import smtplib
import ssl
from email.message import EmailMessage

required = ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "ALERT_TO"]
missing = [x for x in required if not os.getenv(x)]
if missing:
    raise SystemExit("Missing: " + ", ".join(missing))

host = os.environ["SMTP_HOST"]
port = int(os.getenv("SMTP_PORT", "465"))
username = os.environ["SMTP_USERNAME"]
password = os.environ["SMTP_PASSWORD"]

msg = EmailMessage()
msg["Subject"] = "Fort Wilderness Monitor — Test Alert"
msg["From"] = os.getenv("ALERT_FROM", username)
msg["To"] = os.environ["ALERT_TO"]
msg.set_content(
    "Your Fort Wilderness GitHub monitor email is configured correctly.\n\n"
    "Search: Dec 30, 2026 to Jan 1, 2027 — 2 adults."
)

if port == 465:
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as s:
        s.login(username, password)
        s.send_message(msg)
else:
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(username, password)
        s.send_message(msg)

print("Test email sent.")
