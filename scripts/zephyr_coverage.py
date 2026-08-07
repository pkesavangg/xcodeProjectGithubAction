#!/usr/bin/env python3
"""
meApp — Zephyr Automation Coverage (Approved) → Slack DM.

Pulls Zephyr Scale project MA test cases, scopes to Approved status, and reports:
  1. the automation-coverage buckets (base table),
  2. [optional] a per-assignee breakdown (needs Jira creds),
  3. [optional] daily throughput — how many cases became automated since the last
     run, split by assignee (needs Jira creds + a persisted state file).

Zephyr exposes no field-change history, so "automated per day" is derived by
diffing today's set of automated (Yes) case keys against the previous run's set
(persisted between runs by the workflow on a dedicated state branch).

Env vars (set as GitHub Actions secrets / workflow env):
  ZEPHYR_API_TOKEN  - Zephyr Scale REST API bearer token (JWT)   [required]
  SLACK_WEBHOOK_URL - Slack incoming webhook pointed at your DM  [required]
  ZEPHYR_API_BASE   - optional, defaults to the v2 API base
  JIRA_EMAIL        - Atlassian account email    [optional; enables assignee/delta split]
  JIRA_API_TOKEN    - Atlassian API token        [optional; enables assignee/delta split]
  JIRA_BASE_URL     - optional, defaults to https://greatergoods.atlassian.net
  ARTIFACT_URL      - optional, published pending-automation page; adds the link + call-to-action
  STATE_IN          - path to the previous snapshot JSON (default .state/prior.json)
  STATE_OUT         - path to write today's snapshot JSON  (default .state/new.json)

Core Zephyr/Slack failures exit non-zero (red run, no misleading post). The Jira
and delta blocks are additive: missing creds or a failed lookup degrade to a
still-useful post; the run never fails because of them.
"""
import base64
import json
import os
import sys
import urllib.request
import urllib.error
from collections import Counter
from datetime import datetime, timezone, timedelta

API_BASE = os.environ.get("ZEPHYR_API_BASE", "https://api.zephyrscale.smartbear.com/v2").rstrip("/")
JIRA_BASE = os.environ.get("JIRA_BASE_URL", "https://greatergoods.atlassian.net").rstrip("/")
PROJECT_KEY = "MA"
APPROVED_STATUS_ID = 11771320  # MA TEST_CASE "Approved"
IST = timezone(timedelta(hours=5, minutes=30))


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
    yes_case_task = {}    # Yes test-case key (MA-Txxx) -> its Automation Task key ("" if none)
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
                yes_case_task[tc.get("key")] = task
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
                    else:
                        no_task_filled += 1
                elif status == "Yes":
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
        "yes_case_task": yes_case_task,
        "empty_yes": empty_yes,
        "empty_no": empty_no,
    }


def resolve_assignees(keys):
    """Jira lookup -> (task key -> assignee first-name, task key -> status name).

    Both maps are empty if creds are absent or the lookup fails — callers degrade.
    """
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if not (email and token) or not keys:
        return {}, {}
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    out = {}
    statuses = {}
    try:
        for i in range(0, len(keys), 90):
            batch = keys[i:i + 90]
            jql = "key in (" + ",".join(batch) + ")"
            body = json.dumps({"jql": jql, "fields": ["assignee", "status"], "maxResults": 100}).encode()
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
                fields = issue.get("fields") or {}
                a = fields.get("assignee")
                name = a.get("displayName") if a else "Unassigned"
                out[issue["key"]] = (name or "Unassigned").split()[0]  # first name, compact
                statuses[issue["key"]] = ((fields.get("status") or {}).get("name") or "")
    except Exception as e:  # noqa: BLE001 - additive, never fail the run
        warn(f"Jira assignee lookup failed: {e}")
        return {}, {}
    return out, statuses


def dotpad(label, width, val, extra=""):
    return f"{label + ' ':.<{width}}{val:>5,}{extra}"


def build_assignee_block(m, k2a):
    if not k2a:
        return ""
    per = {}
    for key, n in m["task_cases"].items():
        name = k2a.get(key, "Unassigned")
        yes = m["task_cases_yes"].get(key, 0)
        agg = per.setdefault(name, [0, 0, 0])
        agg[0] += n
        agg[1] += yes
        agg[2] += n - yes
    rows = sorted(per.items(), key=lambda kv: -kv[1][0])
    lines = [dotpad(name, 20, t, f"  (Y{y:,} / N{no:,})") for name, (t, y, no) in rows]
    lines.append(dotpad("No task (untracked)", 20, m["empty_yes"] + m["empty_no"],
                        f"  (Y{m['empty_yes']:,} / N{m['empty_no']:,})"))
    return "\n*By automation-task assignee (Approved):*\n```\n" + "\n".join(lines) + "\n```"


def build_delta_block(m, k2a, prior):
    """Daily throughput: newly-automated cases since the last snapshot, by assignee."""
    today_keys = set(m["yes_case_task"].keys())
    if not prior or "yes_keys" not in prior:
        return f"\n_Baseline captured: {len(today_keys):,} automated. Daily deltas start next run._"
    prev_keys = set(prior.get("yes_keys", []))
    prev_date = prior.get("date", "last run")
    new_keys = today_keys - prev_keys
    reverted = prev_keys - today_keys
    header = f"\n*Automated since last run ({prev_date}):* +{len(new_keys):,}"
    if not new_keys:
        tail = f" — none new." + (f" (⚠️ {len(reverted)} reverted from Yes)" if reverted else "")
        return header + tail
    if not k2a:
        return header + "\n_(assignee split needs the Jira token)_"
    agg = Counter()
    for key in new_keys:
        task = m["yes_case_task"].get(key, "")
        agg[k2a.get(task, "Unassigned") if task else "No task"] += 1
    lines = [dotpad(name, 18, cnt) for name, cnt in agg.most_common()]
    block = header + "\n```\n" + "\n".join(lines) + "\n```"
    if reverted:
        block += f"_⚠️ {len(reverted)} case(s) reverted from Yes._"
    return block


CLOSED_STATUSES = {"Done", "Cancelled"}


def count_no_open_task(m, k2s):
    """Pending (No) cases whose automation task is Done/Cancelled, plus those with no task.

    These are the ones nobody is scheduled to write — a closed ticket cannot carry them.
    Returns None when task statuses are unavailable, so the block is simply omitted.
    """
    if not k2s:
        return None
    stranded = sum(
        n - m["task_cases_yes"].get(key, 0)
        for key, n in m["task_cases"].items()
        if k2s.get(key) in CLOSED_STATUSES
    )
    return stranded + m["no_task_empty"]


def build_artifact_block(m, k2s):
    url = os.environ.get("ARTIFACT_URL", "").strip()
    if not url:
        return ""
    stranded = count_no_open_task(m, k2s)
    detail = (
        f"\n*{stranded:,}* of the {m['no']:,} pending cases sit under a Jira task that is already "
        "*Done or Cancelled* (or have no task at all) — nothing is scheduled to write them."
        if stranded is not None else ""
    )
    return (
        f"\n\n<{url}|Pending automation — full case list>"
        "\n_Every pending case with its Zephyr folder, case owner, linked Jira automation task "
        "and that task's status/assignee — searchable and filterable by person._"
        f"{detail}"
        "\n\n*What to do:*"
        "\n• Automation already written and running — set `Automation Status` to *Yes* on the Zephyr case."
        "\n• Case can't move to Yes yet — create a Jira task and fill the `Automation Task` field "
        "on the Zephyr case, so it stays tracked."
    )


def build_message(m, k2a, prior, k2s=None):
    today = datetime.now(IST).strftime("%a %d %b %Y")
    pct = (100 * m["yes"] / m["automatable"]) if m["automatable"] else 0
    table = "\n".join([
        f"{'Total approved test cases':.<40}{m['approved']:>7,}",
        f"{'Automatable (Yes + No)':.<40}{m['automatable']:>7,}",
        f"{'Already automated (Yes)':.<40}{m['yes']:>7,}",
        f"{'Pending to automate (No)':.<40}{m['no']:>7,}",
        f"{'  - Pending to create tasks':.<40}{m['no_task_empty']:>7,}",
        f"{'  - Already have a task':.<40}{m['no_task_filled']:>7,}",
    ])
    return (
        f"*meApp — Zephyr Automation Coverage (Approved)*  _{today}_\n"
        f"```\n{table}\n```\n"
        f"*{pct:.1f}%* of automatable-approved cases are automated.\n"
        f"_Excluded: Not Feasible {m['not_feasible']:,} · blank {m['blank']:,}_"
        + build_assignee_block(m, k2a)
        + build_delta_block(m, k2a, prior)
        + build_artifact_block(m, k2s or {})
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


def load_prior():
    path = os.environ.get("STATE_IN", ".state/prior.json")
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_state(m):
    path = os.environ.get("STATE_OUT", ".state/new.json")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    snapshot = {
        "date": datetime.now(IST).strftime("%a %d %b %Y"),
        "yes_keys": sorted(m["yes_case_task"].keys()),
    }
    with open(path, "w") as f:
        json.dump(snapshot, f)
    print(f"Wrote state snapshot ({len(snapshot['yes_keys'])} keys) to {path}")


def main():
    token = os.environ.get("ZEPHYR_API_TOKEN")
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not token:
        die("ZEPHYR_API_TOKEN is not set.")
    if not webhook:
        die("SLACK_WEBHOOK_URL is not set.")
    metrics = collect_metrics(token)
    k2a, k2s = resolve_assignees(sorted(metrics["task_cases"]))
    prior = load_prior()
    message = build_message(metrics, k2a, prior, k2s)
    print("Computed snapshot:")
    print(message)
    post_to_slack(message, webhook)
    print("Posted to Slack DM successfully.")
    write_state(metrics)  # only advance the baseline after a successful post


if __name__ == "__main__":
    main()
