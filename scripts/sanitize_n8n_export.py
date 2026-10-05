#!/usr/bin/env python3
"""Sanitise raw n8n workflow exports so they are safe to commit.

Usage:
    python scripts/sanitize_n8n_export.py <raw_export.json> [...] [--out n8n/workflows]

What it does
  * removes instance-specific identifiers (meta.instanceId, workflow id,
    versionId, per-node webhookId, credential ids) -- credential *names* are
    kept so n8n/README.md can tell you which credentials to create
  * replaces any e-mail address found in node parameters with a placeholder
    (REPLACE_WITH_APPROVED_SENDER_EMAIL for `fromEmail`,
    REPLACE_WITH_ALERT_RECIPIENT_EMAIL everywhere else)
  * sets "active" to false so an import never starts a schedule by surprise
  * drops pinData / staticData (execution data can contain real vendor data)
  * exits non-zero if something that looks like a hard-coded secret is left

Raw exports should live in n8n/raw/ (git-ignored); only the sanitised output
in n8n/workflows/ is committed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
SENDER = "REPLACE_WITH_APPROVED_SENDER_EMAIL"
RECIPIENT = "REPLACE_WITH_ALERT_RECIPIENT_EMAIL"
SECRET_PATTERNS = [
    re.compile(r"x-internal-api-key\W{1,6}[A-Za-z0-9_\-]{12,}", re.I),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"postgres(?:ql)?://[^\s\"']+:[^\s\"']+@"),
]


def scrub(value, key=None):
    if isinstance(value, dict):
        return {k: scrub(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, key) for v in value]
    if isinstance(value, str) and EMAIL_RE.search(value):
        return EMAIL_RE.sub(SENDER if key == "fromEmail" else RECIPIENT, value)
    return value


def sanitise(workflow: dict) -> dict:
    wf = dict(workflow)
    for k in ("id", "versionId", "pinData", "staticData", "shared", "triggerCount"):
        wf.pop(k, None)
    wf["active"] = False
    wf["meta"] = {k: v for k, v in (wf.get("meta") or {}).items() if k != "instanceId"}
    nodes = []
    for node in wf.get("nodes", []):
        node = dict(node)
        node.pop("webhookId", None)
        if node.get("credentials"):
            node["credentials"] = {
                ctype: {"name": ref.get("name")} for ctype, ref in node["credentials"].items()
            }
        node["parameters"] = scrub(node.get("parameters", {}))
        nodes.append(node)
    wf["nodes"] = nodes
    return wf


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "workflow"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("n8n/workflows"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    failed = False
    for path in args.files:
        clean = sanitise(json.loads(path.read_text(encoding="utf-8")))
        text = json.dumps(clean, indent=2, ensure_ascii=False) + "\n"
        hits = [p.pattern for p in SECRET_PATTERNS if p.search(text)]
        if hits:
            print(f"REFUSING {path.name}: possible hard-coded secret ({hits})", file=sys.stderr)
            failed = True
            continue
        target = args.out / f"{slug(clean.get('name', path.stem))}.json"
        target.write_text(text, encoding="utf-8", newline="\n")
        print(f"{path.name} -> {target}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
