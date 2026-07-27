#!/usr/bin/env python3
"""
meApp — Zephyr Automation Coverage (Approved) → Slack DM.

Pulls Zephyr Scale project MA test cases, scopes to Approved status, and reports
the automation-coverage buckets, then posts the snapshot to a Slack webhook.

Env vars (set as GitHub Actions secrets):
  ZEPHYR_API_TOKEN  - Zephyr Scale REST API bearer token (JWT)
  SLACK_WEBHOOK_URL - Slack incoming webhook pointed at your own DM
  ZEPHYR_API_BASE   - optional, defaults to the v2 API base

The token is never printed. Fails loudly (non-zero exit) on any error so the
GitHub Actions run turns red instead of silently posting nothing.
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

API_BASE = os.environ.get("ZEPHYR_API_BASE", "https://api.zephyrscale.smartbear.com/v2").rstrip("/")
PROJECT_KEY = "MA"
APPROVED_STATUS_ID = 11771320  # MA TEST_CASE "Approved"


def die(msg):
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(1)


def get_json(url, token):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            # Zephyr embeds raw control chars that break strict JSON parsing.
            return json.loads(resp.read().decode(), strict=False)
    except urllib.error.HTTPError as e:
        die(f"Zephyr API {e.code} for {url.split('?')[0]}: {e.read().decode()[:200]}")
    except Exception as e:  # noqa: BLE001 - surface any transport error as a red run
        die(f"Could not reach Zephyr API ({url.split('?')[0]}): {e}")


def collect_metrics(token):
    seen = set()
    start = 0
    approved = 0
    yes = no = not_feasible = blank = 0
    no_task_empty = no_task_filled = 0
    while True:
        url = f"{API_BASE}/testcases?projectKey={PROJECT_KEY}&maxResults=100&startAt={start}"
        data = get_json(url, token)
        values = data.get("values", [])
        for tc in values:
            tid = tc.get("id")
            if tid in seen:  # pagination is not a snapshot -> dedupe by id
                continue
            seen.add(tid)
            if (tc.get("status") or {}).get("id") != APPROVED_STATUS_ID:
                continue
            approved += 1
            cf = tc.get("customFields") or {}
            status = (cf.get("Automation Status") or "blank")
            if status == "Yes":
                yes += 1
            elif status == "No":
                no += 1
                if (cf.get("Automation Task") or "").strip():
                    no_task_filled += 1
                else:
                    no_task_empty += 1
            elif status == "Not Feasible":
                not_feasible += 1
            else:
                blank += 1
        if data.get("isLast"):
            break
        start += 100
    if approved == 0:
        die("Zephyr returned 0 approved cases — refusing to post a misleading snapshot.")
    return {
        "approved": approved,
        "automatable": yes + no,
        "yes": yes,
        "no": no,
        "no_task_empty": no_task_empty,
        "no_task_filled": no_task_filled,
        "not_feasible": not_feasible,
        "blank": blank,
    }


def build_message(m):
    ist = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(ist).strftime("%a %d %b %Y")
    pct = (100 * m["yes"] / m["automatable"]) if m["automatable"] else 0

    def row(label, val):
        return f"{label:.<40}{val:>7,}"

    table = "\n".join([
        row("Total approved test cases", m["approved"]),
        row("Automatable (Yes + No)", m["automatable"]),
        row("Already automated (Yes)", m["yes"]),
        row("Pending to automate (No)", m["no"]),
        row("  - Pending to create tasks", m["no_task_empty"]),
        row("  - Already have a task", m["no_task_filled"]),
    ])
    text = (
        f"*meApp — Zephyr Automation Coverage (Approved)*  _{today}_\n"
        f"```\n{table}\n```\n"
        f"*{pct:.1f}%* of automatable-approved cases are automated.\n"
        f"_Excluded: Not Feasible {m['not_feasible']:,} · blank {m['blank']:,}_"
    )
    return text


def post_to_slack(text, webhook):
    payload = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        webhook, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            if body.strip() != "ok":
                die(f"Slack webhook did not return ok: {body[:200]}")
    except urllib.error.HTTPError as e:
        die(f"Slack webhook {e.code}: {e.read().decode()[:200]}")
    except Exception as e:  # noqa: BLE001
        die(f"Could not reach Slack webhook: {e}")


def main():
    token = os.environ.get("ZEPHYR_API_TOKEN")
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not token:
        die("ZEPHYR_API_TOKEN is not set.")
    if not webhook:
        die("SLACK_WEBHOOK_URL is not set.")
    metrics = collect_metrics(token)
    message = build_message(metrics)
    print("Computed snapshot:")
    print(message)
    post_to_slack(message, webhook)
    print("Posted to Slack DM successfully.")


if __name__ == "__main__":
    main()
