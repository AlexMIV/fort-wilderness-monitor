#!/usr/bin/env python3
"""
Fort Wilderness campsite availability monitor.

This uses the same internal JSON endpoints used by disneyworld.disney.go.com.
They are not a public/supported Disney API and can change without notice.
"""
from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterable

import requests

BASE_URL = "https://disneyworld.disney.go.com"
RESORTS_URL = f"{BASE_URL}/wdpr-resorts-list-api/api/v1/resorts"
AVAILABILITY_URL = f"{BASE_URL}/wdpr-resorts-list-api/api/v1/resort-availability"
BOOKING_URL = (
    "https://disneyworld.disney.go.com/resorts/"
    "campsites-at-fort-wilderness-resort/rates-rooms/"
)

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "config.json"))
STATE_PATH = Path(os.getenv("STATE_PATH", "state/availability.json"))

HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "cache-control": "no-cache",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
}


@dataclass(frozen=True)
class Match:
    target: str
    matched_name: str
    details: str

    @property
    def key(self) -> str:
        return normalize(self.target)


def normalize(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("–", "-").replace("—", "-").replace("‑", "-")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def get_config() -> dict[str, Any]:
    cfg = load_json(CONFIG_PATH, None)
    if not cfg:
        raise RuntimeError(f"Missing or empty configuration: {CONFIG_PATH}")
    return cfg


def http_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def get_resorts(session: requests.Session) -> dict[str, Any]:
    r = session.get(
        RESORTS_URL,
        params={"storeId": "wdw", "resortGroup": "CORE", "region": "us"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or "resorts" not in data:
        raise RuntimeError("Disney resorts response did not contain expected 'resorts' data.")
    return data


def get_availability(session: requests.Session, cfg: dict[str, Any]) -> dict[str, Any]:
    body = {
        "storeId": "wdw",
        "checkInDate": cfg["check_in"],
        "checkOutDate": cfg["check_out"],
        "partyMix": {
            "adultCount": int(cfg["adults"]),
            "childCount": int(cfg.get("children", 0)),
            "nonAdultAges": cfg.get("child_ages", []),
        },
        "accessible": bool(cfg.get("accessible", False)),
        "region": "us",
        "resortGroup": "CORE",
        "affiliations": ["STD_GST"],
    }
    r = session.post(AVAILABILITY_URL, json=body, timeout=45)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or "resorts" not in data:
        raise RuntimeError(
            "Disney availability response did not contain expected 'resorts' data. "
            "The internal endpoint may have changed."
        )
    return data


def find_fort_wilderness_id(resorts: dict[str, Any], cfg: dict[str, Any]) -> str:
    needle = normalize(cfg["resort_name_contains"])
    for resort_id, resort in resorts.get("resorts", {}).items():
        name = normalize((resort or {}).get("name", ""))
        if needle in name:
            return str(resort_id)
    available_names = [
        (r or {}).get("name", "") for r in resorts.get("resorts", {}).values()
        if isinstance(r, dict)
    ]
    raise RuntimeError(
        "Could not identify Fort Wilderness in Disney's resort list. "
        f"Looked for: {cfg['resort_name_contains']!r}. "
        f"Resorts returned: {available_names[:10]}"
    )


def compact(obj: Any, limit: int = 750) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        text = str(obj)
    return text[:limit] + ("…" if len(text) > limit else "")


def walk(obj: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    yield path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk(v, path + (str(k),))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk(v, path + (str(i),))


def object_text(obj: Any) -> str:
    if isinstance(obj, dict):
        vals = []
        for key, value in obj.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                vals.append(f"{key}={value}")
        return " | ".join(vals)
    return str(obj)


def target_aliases(target: str) -> set[str]:
    n = normalize(target)
    aliases = {n}
    aliases.add(n.replace("campsite", "campsites"))
    aliases.add(n.replace("campsites", "campsite"))
    # Disney sometimes varies "Full Hook-Up" punctuation.
    aliases.add(n.replace("full hook up", "full hookup"))
    aliases.add(n.replace("full hookup", "full hook up"))
    return {a for a in aliases if a}


def contains_alias(text: str, target: str) -> bool:
    ntext = normalize(text)
    return any(alias in ntext for alias in target_aliases(target))


def has_offer_context(path: tuple[str, ...], obj: Any) -> bool:
    """
    Avoid matching static room metadata. A room name is considered bookable only
    when it appears in/near offer, rate, price, availability, package, product,
    inventory, or booking data returned for the requested dates.
    """
    context_words = {
        "offer", "offers", "rate", "rates", "price", "prices", "availability",
        "available", "package", "packages", "product", "products", "inventory",
        "booking", "bookable", "room", "rooms", "accommodation", "accommodations",
    }
    path_words = {normalize(p) for p in path}
    if any(any(c in p for c in context_words) for p in path_words):
        return True

    if isinstance(obj, dict):
        keys = {normalize(k) for k in obj.keys()}
        return any(any(c in k for c in context_words) for k in keys)
    return False


def find_target_matches(resort_availability: Any, targets: list[str]) -> list[Match]:
    """
    Find requested campsite types inside the Fort Wilderness availability payload.

    The endpoint is unofficial and its schema has changed before, so this searches
    the Fort Wilderness subtree rather than depending on one brittle JSON path.
    """
    found: dict[str, Match] = {}

    # Pass 1: dictionaries are best because they preserve the room name together
    # with rate/price/offer fields.
    for path, obj in walk(resort_availability):
        if not isinstance(obj, dict):
            continue
        blob = compact(obj, 5000)
        for target in targets:
            if contains_alias(blob, target) and has_offer_context(path, obj):
                found.setdefault(
                    normalize(target),
                    Match(
                        target=target,
                        matched_name=target,
                        details=object_text(obj)[:1000] or compact(obj, 1000),
                    ),
                )

    # Pass 2: if Disney puts a room name directly under an offers/rates branch.
    for path, obj in walk(resort_availability):
        if not isinstance(obj, str) or not has_offer_context(path, obj):
            continue
        for target in targets:
            if contains_alias(obj, target):
                found.setdefault(
                    normalize(target),
                    Match(target=target, matched_name=obj, details=" / ".join(path)),
                )

    return list(found.values())


def send_email(subject: str, body: str) -> None:
    host = os.environ["SMTP_HOST"]
    port = int(os.getenv("SMTP_PORT", "465"))
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]
    email_from = os.getenv("ALERT_FROM", username)
    email_to = os.environ["ALERT_TO"]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.set_content(body)

    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(username, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(username, password)
            smtp.send_message(msg)


def require_email_config() -> None:
    missing = [
        name for name in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "ALERT_TO")
        if not os.getenv(name)
    ]
    if missing:
        raise RuntimeError(
            "Missing email configuration: " + ", ".join(missing) +
            ". Add these as GitHub Actions repository secrets."
        )


def main() -> int:
    cfg = get_config()
    targets = list(cfg["campsite_types"])
    print(
        f"Checking Fort Wilderness {cfg['check_in']} -> {cfg['check_out']} "
        f"for {cfg['adults']} adult(s): {', '.join(targets)}"
    )

    session = http_session()
    resorts = get_resorts(session)
    resort_id = find_fort_wilderness_id(resorts, cfg)
    availability = get_availability(session, cfg)

    resort_tree = availability.get("resorts", {}).get(resort_id)
    if resort_tree is None:
        # Some versions may key availability differently; do a conservative
        # fallback search for the resort ID/string in the resorts map.
        for rid, candidate in availability.get("resorts", {}).items():
            if str(rid) == str(resort_id):
                resort_tree = candidate
                break

    if resort_tree is None:
        print("Fort Wilderness has no availability entry for these dates.")
        current_keys: set[str] = set()
        matches: list[Match] = []
    else:
        matches = find_target_matches(resort_tree, targets)
        current_keys = {m.key for m in matches}

    old_state = load_json(STATE_PATH, {"available": []})
    previous_keys = set(old_state.get("available", []))

    newly_available = current_keys - previous_keys
    newly_unavailable = previous_keys - current_keys

    print("Currently matched:", sorted(current_keys) if current_keys else "none")
    if newly_available:
        print("Newly available:", sorted(newly_available))
    if newly_unavailable:
        print("No longer available:", sorted(newly_unavailable))

    # Only email when something transitions from unavailable -> available.
    if newly_available:
        require_email_config()
        new_matches = [m for m in matches if m.key in newly_available]
        lines = [
            "FORT WILDERNESS CAMPSITE AVAILABILITY FOUND",
            "",
            f"Check-in: {cfg['check_in']}",
            f"Check-out: {cfg['check_out']}",
            f"Guests: {cfg['adults']} adults",
            "",
            "Newly available campsite type(s):",
        ]
        for m in new_matches:
            lines.append(f"- {m.target}")
        lines += [
            "",
            "Book/check immediately:",
            BOOKING_URL,
            "",
            "Availability can disappear quickly. Re-run the same dates and guest count on Disney's site.",
        ]
        send_email(
            "🚨 Fort Wilderness campsite available — Dec 30 to Jan 1",
            "\n".join(lines),
        )
        print("Alert email sent.")

    new_state = {
        "available": sorted(current_keys),
        "last_changed_utc": datetime.now(timezone.utc).isoformat(),
        "search": {
            "check_in": cfg["check_in"],
            "check_out": cfg["check_out"],
            "adults": cfg["adults"],
            "campsite_types": targets,
        },
    }

    # Preserve last_changed_utc when state has not changed so GitHub doesn't
    # create a commit every 5 minutes.
    if current_keys == previous_keys and STATE_PATH.exists():
        print("No availability-state change. State file left untouched.")
    else:
        save_json(STATE_PATH, new_state)
        print(f"State updated: {STATE_PATH}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except requests.RequestException as exc:
        print(f"DISNEY HTTP ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:
        print(f"MONITOR ERROR: {exc}", file=sys.stderr)
        raise SystemExit(3)
