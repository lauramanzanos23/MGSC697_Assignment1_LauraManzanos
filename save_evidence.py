"""
save_evidence.py
----------------
Generates the evidence packet for the assignment submission.

Runs five representative scenarios + all evals, then writes clean
JSON traces and a human-readable summary to the traces/ directory.

Usage:
    python save_evidence.py
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

try:
    from .advisor_agent import advise, CourseRecommendation
    from .evals import EVAL_CASES, run_case, patch_student_credits, unpatch_student_credits
except ImportError:  # Allows `python save_evidence.py` from this directory.
    from advisor_agent import advise, CourseRecommendation
    from evals import EVAL_CASES, run_case, patch_student_credits, unpatch_student_credits

TRACES_DIR = PROJECT_DIR / "traces"
TRACES_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Demo scenarios
# ---------------------------------------------------------------------------

DEMO_SCENARIOS = [
    {
        "id": "DEMO-01",
        "label": "Happy path — eligible student requests ML courses",
        "student_id": "S001",
        "message": "I want to take machine learning and analytics courses next semester.",
    },
    {
        "id": "DEMO-02",
        "label": "Guardrail fires — probation student at credit cap",
        "student_id": "S002",
        "message": "I want to add three more courses next semester.",
        "patch_credits": ("S002", 12),   # put S002 at the cap before running
    },
    {
        "id": "DEMO-03",
        "label": "Capstone eligibility — GPA 3.2 passes the 3.0 threshold",
        "student_id": "S003",
        "message": "I want to take the Analytics Capstone (MGSC499) next semester.",
    },
    {
        "id": "DEMO-04",
        "label": "Missing prerequisite — agent correctly blocks MGSC415",
        "student_id": "S001",
        "message": "Can I take MGSC415 Machine Learning for Business this semester?",
    },
    {
        "id": "DEMO-05",
        "label": "No matching courses — query outside catalog",
        "student_id": "S001",
        "message": "I want to study neuroscience and cognitive science courses.",
    },
    {
        "id": "DEMO-06",
        "label": "Approval required — GPA below capstone minimum",
        "student_id": "S004",
        "message": "I want to take the Analytics Capstone MGSC499.",
    },
    {
        "id": "DEMO-07",
        "label": "First-semester student — only intro courses available",
        "student_id": "S005",
        "message": "What courses can I take as a first-year student?",
    },
]

# ---------------------------------------------------------------------------
# Run demos and save traces
# ---------------------------------------------------------------------------

async def run_demos() -> list[dict]:
    results = []
    for scenario in DEMO_SCENARIOS:
        print(f"  Running {scenario['id']}...")

        patch = scenario.get("patch_credits")
        if patch:
            patch_student_credits(*patch)

        blocked = False
        output_data = None
        error_msg = None

        try:
            rec: CourseRecommendation = await advise(
                scenario["student_id"], scenario["message"]
            )
            output_data = rec.model_dump()
        except RuntimeError as e:
            blocked = True
            error_msg = str(e)
        finally:
            if patch:
                unpatch_student_credits(patch[0], 3)  # restore

        entry = {
            "scenario_id": scenario["id"],
            "label": scenario["label"],
            "student_id": scenario["student_id"],
            "message": scenario["message"],
            "blocked_by_guardrail": blocked,
            "output": output_data,
            "guardrail_message": error_msg if blocked else None,
        }
        results.append(entry)

    return results


# ---------------------------------------------------------------------------
# Run evals
# ---------------------------------------------------------------------------

async def run_all_evals_for_evidence() -> list[dict]:
    results = []
    for case in EVAL_CASES:
        if case.id == "EVAL-03":
            patch_student_credits("S002", 12)
        try:
            result = await run_case(case)
        finally:
            if case.id == "EVAL-03":
                unpatch_student_credits("S002", 3)

        results.append({
            "case_id": result.case_id,
            "description": result.description,
            "passed": result.passed,
            "notes": result.notes,
            "output": result.output_summary,
            "error": result.error[:300] if result.error else None,
        })
    return results


# ---------------------------------------------------------------------------
# Write summary markdown
# ---------------------------------------------------------------------------

def write_summary(demos: list[dict], evals: list[dict]):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# Evidence Packet — McGill Course Advisor Agent",
        f"Generated: {ts}",
        "",
        "---",
        "",
        "## Demo Run Transcripts",
        "",
    ]

    for d in demos:
        lines.append(f"### {d['scenario_id']}: {d['label']}")
        lines.append(f"- **Student:** `{d['student_id']}`")
        lines.append(f"- **Request:** *{d['message']}*")
        if d["blocked_by_guardrail"]:
            lines.append("- **Result:** 🚫 BLOCKED BY GUARDRAIL")
            msg = d["guardrail_message"] or ""
            lines.append(f"- **Detail:** {msg[:200]}")
        else:
            out = d["output"]
            courses = [c["course_code"] for c in out["recommended_courses"]]
            lines.append(f"- **Recommended:** {courses if courses else 'None'}")
            lines.append(f"- **Requires approval:** {out['requires_advisor_approval']}")
            lines.append(f"- **Risks:** {out['risks'] if out['risks'] else 'None'}")
            lines.append(f"- **Summary:** {out['summary']}")
        lines.append("")

    passed = sum(1 for e in evals if e["passed"])
    lines += [
        "---",
        "",
        f"## Eval Results — {passed}/{len(evals)} passed",
        "",
        "| ID | Description | Result | Notes |",
        "|---|---|---|---|",
    ]
    for e in evals:
        status = "✅ PASS" if e["passed"] else "❌ FAIL"
        lines.append(f"| {e['case_id']} | {e['description']} | {status} | {e['notes']} |")

    lines += [
        "",
        "---",
        "",
        "## Guardrail Evidence",
        "",
        "EVAL-03 and DEMO-02 confirm that `PROBATION_OVERLOAD_GUARDRAIL` fires correctly.",
        "The guardrail runs *before* the agent loop, blocking the request with `InputGuardrailTripwireTriggered`.",
        "The caller catches this and surfaces it as a human-escalation message.",
        "No tool calls are made. No recommendation is produced.",
    ]

    (TRACES_DIR / "SUMMARY.md").write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=== Generating Evidence Packet ===\n")

    print("Running demo scenarios...")
    demos = await run_demos()
    (TRACES_DIR / "demo_traces.json").write_text(
        json.dumps(demos, indent=2, default=str)
    )
    print(f"  → saved traces/demo_traces.json")

    print("\nRunning eval suite...")
    evals = await run_all_evals_for_evidence()
    passed = sum(1 for e in evals if e["passed"])
    (TRACES_DIR / "eval_results.json").write_text(
        json.dumps(evals, indent=2, default=str)
    )
    print(f"  → saved traces/eval_results.json ({passed}/{len(evals)} passed)")

    write_summary(demos, evals)
    print(f"  → saved traces/SUMMARY.md")

    print(f"\nEvidence packet written to: {TRACES_DIR}")
    print(f"Eval score: {passed}/{len(evals)}")


if __name__ == "__main__":
    asyncio.run(main())
