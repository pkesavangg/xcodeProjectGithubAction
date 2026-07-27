#!/usr/bin/env python3
"""
meApp — Zephyr Automation Coverage (Approved) → Slack DM.

Pulls Zephyr Scale project MA test cases, scopes to Approved status, and reports
the automation-coverage buckets, then posts the snapshot to a Slack webhook.

Optionally appends a per-assignee breakdown: each Approved test case with an
"Automation Task" (a MOB-xxxx Jira key) is attributed to that Jira task's
assignee. Cases with an empty Automation Task can't be attributed and are
reported as an "untrackable" bucket.

Env vars (set as GitHub Actions secrets):
  ZEPHYR_API_TOKEN  - Zephyr Scale REST API bearer token (JWT)   [required]
  SLACK_WEBHOOK_URL - Slack incoming webhook pointed at your DM  [required]
  ZEPHYR_API_BASE   - optional, defaults to the v2 API base
  JIRA_EMAIL        - Atlassian account email    [optional; enables assignee block]
  JIRA_API_TOKEN    - Atlassian API token        [optional; enables assignee block]
  JIRA_BASE_URL     - optional, defaults to https://greatergoods.atlassian.net

Core Zephyr/Slack failures exit non-zero (red run, no misleading post). The
optional Jira assignee block degrades gracefully: if the Jira creds are missing
or the lookup fails, the base snapshot still posts.
"""
import base64
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

API_BASE = os.environ.get("ZEPHYR_API_BASE", "https://api.zephyrscale.smartbear.com/v2").rstrip("/")
JIRA_BASE = os.environ.get("JIRA_BASE_URL", "https://greatergoods.atlassian.net").rstrip("/")
PROJECT_KEY = "MA"
APPROVED_STATUS_ID = 11771320  # MA TEST_CASE "Approved"


def die(msg):
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(1)


def warn(msg):
    print(f"::warning::{msg}", file=sys.stderr)


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
    """Single paginated pass over MA test cases, deduped by id."""
    seen = set()
    start = 0
    approved = 0
    yes = no = not_feasible = blank = 0
    no_task_empty = no_task_filled = 0
    task_cases = {}       # Automation Task key -> # approved (Yes+No) cases
    task_cases_yes = {}   # Automation Task key -> # approved Yes cases
    empty_yes = empty_no = 0
    while True:
        url = f"{API_BASE}/testcases?projectKey={PROJECT_KEY}&maxResults=100&startAt={start}"
        data = get_json(url, token)
        for tc in data.get("values", []):
            tid = tc.get("id")
            if tid in seen:  # pagination is not a snapshot -> dedupe by id
                continue
            seen.add(tid)
            if (tc.get("status") or {}).get("id") != APPROVED_STATUS_ID:
                continue
            approved += 1
            cf = tc.get("customFields") or {}
            status = (cf.get("Automation Status") or "blank")
            task = (cf.get("Automation Task") or "").strip()
            if status == "Yes":
                yes += 1
            elif status == "No":
                no += 1
            elif status == "Not Feasible":
                not_feasible += 1
            else:
                blank += 1
            if status in ("Yes", "No"):
                if task:
                    task_cases[task] = task_cases.get(task, 0) + 1
                    if status == "Yes":
                        task_cases_yes[task] = task_cases_yes.get(task, 0) + 1
                    no_task_filled += 1 if status == "No" else 0
                else:
                    if status == "Yes":
                        empty_yes += 1
                    else:
                        empty_no += 1
                        no_task_empty += 1
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
        "task_cases": task_cases,
        "task_cases_yes": task_cases_yes,
        "empty_yes": empty_yes,
        "empty_no": empty_no,
    }


def jira_assignees(keys, email, token):
    """key -> assignee display name via the Jira Cloud enhanced-search endpoint."""
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    out = {}
    for i in range(0, len(keys), 90):
        batch = keys[i:i + 90]
        jql = "key in (" + ",".join(batch) + ")"
        body = json.dumps({"jql": jql, "fields": ["assignee"], "maxResults": 100}).encode()
        req = urllib.request.Request(
            f"{JIRA_BASE}/rest/api/3/search/jql",
            data=body,
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        for issue in data.get("issues", []):
            a = (issue.get("fields") or {}).get("assignee")
            out[issue["key"]] = a.get("displayName") if a else "Unassigned"
    return out


def build_assignee_block(m):
    """Optional per-assignee block. Returns '' if Jira creds absent or lookup fails."""
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if not (email and token):
        return ""
    keys = sorted(m["task_cases"])
    if not keys:
        return ""
    try:
        k2a = jira_assignees(keys, email, token)
    except Exception as e:  # noqa: BLE001 - additive block, never fail the run
        warn(f"Assignee block skipped — Jira lookup failed: {e}")
        return ""

    per = {}
    for key, n in m["task_cases"].items():
        name = (k2a.get(key) or "Unassigned").split()[0]  # first name, compact
        yes = m["task_cases_yes"].get(key, 0)
        agg = per.setdefault(name, [0, 0, 0])  # total, yes, no
        agg[0] += n
        agg[1] += yes
        agg[2] += n - yes
    rows = sorted(per.items(), key=lambda kv: -kv[1][0])

    def line(label, total, yes, no):
        return f"{label + ' ':.<20}{total:>5,}  (Y{yes:,} / N{no:,})"

    lines = [line(name, t, y, n) for name, (t, y, n) in rows]
    lines.append(line("No task (untracked)", m["empty_yes"] + m["empty_no"], m["empty_yes"], m["empty_no"]))
    return "\n*By automation-task assignee (Approved):*\n```\n" + "\n".join(lines) + "\n```"


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
    return (
        f"*meApp — Zephyr Automation Coverage (Approved)*  _{today}_\n"
        f"```\n{table}\n```\n"
        f"*{pct:.1f}%* of automatable-approved cases are automated.\n"
        f"_Excluded: Not Feasible {m['not_feasible']:,} · blank {m['blank']:,}_"
        + build_assignee_block(m)
    )


def post_to_slack(text, webhook):
    payload = json.dumps({"text": text}).encode()
    req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"})
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
