"""Scan agent trace jsonl for the ORIGINAL insufficient_candidates failure.

Finds records whose serialized JSON contains the distinctive markers from the
user's first report (insufficient_candidates / difficulty_progression_is_soft /
constraint_unsatisfied) and prints the trace chain:
  user message -> tool call args.scope_names -> generation_summary.scope_names
  -> result status/blocking_errors/warnings
so we can read the REAL requested scope of that historical failure.
"""
import json
import sys
from pathlib import Path

MARKERS = ["insufficient_candidates", "difficulty_progression_is_soft", "constraint_unsatisfied"]
FILES = [
    "logs/agent/2026-08-15.jsonl",
    "logs/agent/2026-08-16.jsonl",
    "evaluations/cases.jsonl",
    "artifacts/scope_not_found_diagnosis.md",
    "artifacts/phase2_fix_report.md",
]


def find_markers(obj):
    s = json.dumps(obj, ensure_ascii=False)
    hits = [m for m in MARKERS if m in s]
    return hits, s


def walk_scope(obj, path=""):
    """Pull scope_names / scope from nested dicts/lists."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("scope_names", "scope") and isinstance(v, (list, str)):
                out[f"{path}.{k}" if path else k] = v
            else:
                out.update(walk_scope(v, f"{path}.{k}" if path else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(walk_scope(v, f"{path}[{i}]"))
    return out


def main():
    base = Path("/Users/shengyue/Documents/Teacher_Agent/calculus_knowledge_agent")
    found = 0
    for f in FILES:
        p = base / f
        if not p.exists():
            continue
        print(f"\n##### SCANNING {f} #####")
        if p.suffix == ".md":
            text = p.read_text(encoding="utf-8", errors="ignore")
            for m in MARKERS:
                if m in text:
                    print(f"  [md] contains marker: {m}")
            continue
        for ln, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # maybe csv-ish; skip
                continue
            hits, s = find_markers(rec)
            if not hits:
                continue
            found += 1
            print(f"\n--- match line {ln} markers={hits} ---")
            # top-level keys
            if isinstance(rec, dict):
                print("  top-level keys:", list(rec.keys())[:20])
                cid = rec.get("conversation_id") or rec.get("conversationId") or rec.get("id")
                print("  conversation_id:", cid)
                role = rec.get("role") or rec.get("type")
                print("  role/type:", role)
                # dump any scope_names/scope anywhere
                scopes = walk_scope(rec)
                for k, v in scopes.items():
                    print(f"  scope field {k} = {v}")
                # if there's a nested 'result' or 'observation' with status
                for key in ("result", "observation", "output", "response", "content"):
                    if key in rec:
                        sub = rec[key]
                        if isinstance(sub, str):
                            for m in MARKERS:
                                if m in sub:
                                    # try parse json inside
                                    print(f"  {key} snippet: {sub[:600]}")
                        else:
                            subscopes = walk_scope(sub)
                            for k2, v2 in subscopes.items():
                                print(f"  {key}{k2} = {v2}")
            print("  (end match)")
    print(f"\n==== total matching records: {found} ====")


if __name__ == "__main__":
    main()
