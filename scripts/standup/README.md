# MeApp daily standup board

`render_standup.py` turns a gathered `standup.json` into a self-contained,
filterable HTML page. The 08:30 IST cloud routine
(`MeApp Daily Standup → board link`) gathers from Jira and GitHub, runs this
script, republishes the result to a fixed Artifact URL, and posts one Slack
message with the link.

```bash
python3 render_standup.py standup.json standup.html
```

The routine only **gathers and dumps raw JSON**. Every derivation — logged
hours, the `Original Estimate else Story Points × 2h` sizing rule, Jira vs
GitHub status drift, grouping, and the HTML itself — happens in this script.
That split is deliberate: the Slack format regressed in July when a long prompt
spec was ignored mid-run, and a script cannot regress that way.

The input contract is documented at the top of the script.
`sample-input.json` is synthetic (this repo is public) but matches the real
payload's shape and field names exactly.

This lives here rather than in a product repo because the routine sandbox can
only read repositories configured as its own sources, and nothing about the
standup belongs in meApp / SageApp / meAppTest.
