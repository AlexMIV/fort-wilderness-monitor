#!/usr/bin/env python3

import json
import os
import smtplib
import ssl
import sys
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import requests


# ---------------------------------------------------------
# YOUR SEARCH
# ---------------------------------------------------------

CHECK_IN = "2026-12-30"
CHECK_OUT = "2027-01-01"
ADULTS = 2
CHILDREN = 0

RESORT_SLUG = "campsites-at-fort-wilderness-resort"

BOOKING_PAGE = (
    "https://disneyworld.disney.go.com/resorts/"
    "campsites-at-fort-wilderness-resort/rates-rooms/"
)

AVAILABILITY_URL = (
    "https://disneyworld.disney.go.com/"
    "wdw-resorts-details-api/api/v1/resort/"
    "campsites-at-fort-wilderness-resort/"
    "availability-and-prices/?storeId=wdw"
)

STATE_PATH = Path("state/availability.json")


# ---------------------------------------------------------
# EXACT DISNEY ROOM IDs
# ---------------------------------------------------------

TARGET_ROOMS = {
    "412223951": "Full Hook-Up Campsite",
    "412224545": "Premium Campsite",
    "412224546": "Premium Meadow Campsite",
}


# ---------------------------------------------------------
# EMAIL
# ---------------------------------------------------------

def send_email(subject, body):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "465"))
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]
    email_from = os.environ.get("ALERT_FROM", username)
    email_to = os.environ["ALERT_TO"]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.set_content(body)

    if port == 465:
        with smtplib.SMTP_SSL(
            host,
            port,
            context=ssl.create_default_context(),
            timeout=30,
        ) as smtp:
            smtp.login(username, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(username, password)
            smtp.send_message(msg)


# ---------------------------------------------------------
# STATE / DUPLICATE ALERT PROTECTION
# ---------------------------------------------------------

def load_state():
    if not STATE_PATH.exists():
        return {"available": []}

    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"available": []}


def save_state(available_ids):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    state = {
        "available": sorted(available_ids),
        "last_changed_utc": datetime.now(timezone.utc).isoformat(),
        "search": {
            "check_in": CHECK_IN,
            "check_out": CHECK_OUT,
            "adults": ADULTS,
            "children": CHILDREN,
        },
    }

    STATE_PATH.write_text(
        json.dumps(state, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------
# DISNEY REQUEST
# ---------------------------------------------------------

def disney_session():
    session = requests.Session()

    session.headers.update({
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-us",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
    })

    return session


def get_disney_availability():
    session = disney_session()

    print("Opening Fort Wilderness page to establish Disney session...")

    try:
        page = session.get(
            BOOKING_PAGE,
            timeout=30,
        )
        print(
            f"Disney page warm-up returned HTTP "
            f"{page.status_code}. Continuing..."
        )
    except requests.RequestException as exc:
        print(
            f"Disney page warm-up failed: "
            f"{exc}. Continuing anyway..."
        )

    conversation_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    personalization_id = str(uuid.uuid4())
    availability_id = str(uuid.uuid4())

    session.cookies.set(
        "currentOffer_jar",
        '{"currentOffer":"room-only"}',
        domain="disneyworld.disney.go.com",
    )

    session.cookies.set(
        "personalization_jar",
        json.dumps({"id": personalization_id}),
        domain="disneyworld.disney.go.com",
    )

    session.cookies.set(
        "roomForm_jar",
        json.dumps({
            "accessible": "0",
            "checkInDate": CHECK_IN,
            "checkOutDate": CHECK_OUT,
            "numberOfAdults": str(ADULTS),
            "numberOfChildren": str(CHILDREN),
            "resort": RESORT_SLUG,
        }),
        domain="disneyworld.disney.go.com",
    )

    headers = {
        "content-type": "application/json",
        "origin": "https://disneyworld.disney.go.com",
        "referer": BOOKING_PAGE,

        "deltapackages": "true",

        "x-conversation-id": conversation_id,
        "x-correlation-id": correlation_id,

        "x-disney-internal-core-api-mods-checkout": "true",
        "x-disney-internal-core-api-quote-checkout": "true",
        "x-disney-internal-core-api-quote-checkout-mods": "true",
        "x-disney-internal-core-api-quote-checkout-ta": "true",
        "x-disney-internal-core-api-quote-checkout-ta-mods": "true",
        "x-disney-internal-core-api-reservation-va": "true",
        "x-disney-internal-core-api-reservation-va-ta": "true",
        "x-disney-internal-core-api-resort-package": "true",
        "x-disney-internal-default-ticket-availability": "true",
        "x-disney-internal-dynamic-price-override-enabled": "true",
        "x-disney-internal-useroneidtoken": "true",

        "x-enable-peach-lodging": "true",
        "x-enable-uplift": "true",
    }

    party_mix = {
        "adultCount": ADULTS,
        "childCount": CHILDREN,
        "nonAdultAges": [],
    }

    payload = {
        "checkInDate": CHECK_IN,
        "checkOutDate": CHECK_OUT,

        "partyMix": party_mix,

        "region": "US",
        "accessible": False,

        "ccrm": {
            "marketingOfferId": "room-only",
            "checkInDate": CHECK_IN,
            "checkOutDate": CHECK_OUT,
            "partyMix": party_mix,
            "preferredResort": RESORT_SLUG,
        },

        "personalizationId": personalization_id,
        "sendOffersCarousel": True,
        "marketingOfferId": "room-only",
        "availabilityId": availability_id,

        "affiliations": [
            "STD_GST",
            "FL_RESIDENT",
        ],

        "postalCode": "34230",
    }

    print("Requesting current Disney availability...")

    response = session.post(
        AVAILABILITY_URL,
        headers=headers,
        json=payload,
        timeout=45,
    )

    if response.status_code != 200:
        print(
            f"Disney response: HTTP {response.status_code}",
            file=sys.stderr,
        )

        print(
            response.text[:1500],
            file=sys.stderr,
        )

        response.raise_for_status()

    return response.json()


# ---------------------------------------------------------
# INTERPRET RESPONSE
# ---------------------------------------------------------

def check_target_rooms(data):
    lookup = data.get("roomPriceLookup")

    if not isinstance(lookup, dict):
        raise RuntimeError(
            "Disney response does not contain roomPriceLookup. "
            "Disney may have changed its API."
        )

    available = {}
    statuses = {}

    for room_id, room_name in TARGET_ROOMS.items():

        room = lookup.get(room_id)

        if room is None:
            raise RuntimeError(
                f"Disney response did not contain expected room ID "
                f"{room_id} ({room_name})."
            )

        reason = room.get("reasonUnavailable")

        # Disney currently returns reasonUnavailable when the
        # room cannot be booked.
        #
        # If reasonUnavailable is absent, the room contains
        # pricing/offer information and is considered available.
        is_available = not reason

        statuses[room_id] = {
            "name": room_name,
            "available": is_available,
            "reason": reason,
            "data": room,
        }

        if is_available:
            available[room_id] = room_name

    return available, statuses


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():
    print()
    print("FORT WILDERNESS AVAILABILITY CHECK")
    print("----------------------------------")
    print(f"Check-in:  {CHECK_IN}")
    print(f"Check-out: {CHECK_OUT}")
    print(f"Guests:    {ADULTS} adults")
    print()

    data = get_disney_availability()

    available, statuses = check_target_rooms(data)

    print()
    print("Disney results:")
    print()

    for room_id, info in statuses.items():
        if info["available"]:
            print(
                f"AVAILABLE: {info['name']} "
                f"({room_id})"
            )
        else:
            print(
                f"Unavailable: {info['name']} "
                f"({room_id}) — {info['reason']}"
            )

    previous_state = load_state()
    previous_available = set(previous_state.get("available", []))
    current_available = set(available.keys())

    newly_available = current_available - previous_available
    disappeared = previous_available - current_available

    print()
    print(
        "Currently available target IDs:",
        sorted(current_available) if current_available else "none",
    )

    if newly_available:
        print()
        print("NEW AVAILABILITY DETECTED!")

        lines = [
            "FORT WILDERNESS CAMPSITE AVAILABLE",
            "",
            f"Check-in: December 30, 2026",
            f"Check-out: January 1, 2027",
            f"Guests: 2 adults",
            "",
            "Newly available:",
        ]

        for room_id in sorted(newly_available):
            lines.append(
                f"- {TARGET_ROOMS[room_id]}"
            )

        lines.extend([
            "",
            "Disney availability can disappear quickly.",
            "",
            "Check/book here:",
            BOOKING_PAGE,
        ])

        send_email(
            "🚨 Fort Wilderness campsite available — Dec 30 to Jan 1",
            "\n".join(lines),
        )

        print("Alert email sent.")

    if disappeared:
        print()
        print("Previously available inventory is no longer available:")

        for room_id in disappeared:
            print(
                f"- {TARGET_ROOMS.get(room_id, room_id)}"
            )

    # Only rewrite the state file when availability actually changes.
    # This prevents GitHub from committing a file every 5 minutes.
    if current_available != previous_available:
        save_state(current_available)
        print()
        print("Availability state updated.")
    else:
        print()
        print("No availability-state change.")

    print()
    print("Check completed successfully.")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())

    except requests.RequestException as exc:
        print(
            f"DISNEY HTTP ERROR: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    except Exception as exc:
        print(
            f"MONITOR ERROR: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(3)
