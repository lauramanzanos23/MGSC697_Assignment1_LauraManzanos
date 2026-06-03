"""
evals.py
--------
Ten evaluation cases for the McGill Course Advisor agent.

Each case defines:
  - id, description, student_id, message
  - expected properties on the CourseRecommendation output
  - a grader function: (output | None, error | None) -> (bool, str)
    The bool is pass/fail. The str is the human-readable verdict.

Graders check STRUCTURED FIELDS (recommended_courses list, booleans,
policy_flags), never free-text. This makes them deterministic.

Run:  python evals.py
"""

from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).with_name(".env"))

try:
    from .advisor_agent import CourseRecommendation, advise
    from .fixtures import STUDENTS
except ImportError:
    from advisor_agent import CourseRecommendation, advise
    from fixtures import STUDENTS


# ---------------------------------------------------------------------------
# Harness types
# ---------------------------------------------------------------------------

@dataclass
class EvalCase:
    id: str
    description: str
    student_id: str
    message: str
    grader: Callable[[Optional[CourseRecommendation], Optional[str]], tuple[bool, str]]
    expect_blocked: bool = False   # True → we expect the guardrail to fire


@dataclass
class EvalResult:
    case_id: str
    description: str
    passed: bool
    notes: str
    output_summary: dict = field(default_factory=dict)
    error: Optional[str] = None


async def run_case(case: EvalCase) -> EvalResult:
    output: Optional[CourseRecommendation] = None
    error_msg: Optional[str] = None

    try:
        output = await advise(case.student_id, case.message)
    except RuntimeError as e:
        error_msg = str(e)

    # Validate guardrail expectation
    if case.expect_blocked and output is not None:
        return EvalResult(
            case_id=case.id, description=case.description,
            passed=False,
            notes="Expected guardrail block but agent returned output.",
        )
    if not case.expect_blocked and output is None:
        return EvalResult(
            case_id=case.id, description=case.description,
            passed=False,
            notes=f"Unexpected block: {error_msg}",
            error=error_msg,
        )

    passed, notes = case.grader(output, error_msg)

    summary = {}
    if output:
        summary = {
            "courses": [c.course_code for c in output.recommended_courses],
            "requires_approval": output.requires_advisor_approval,
            "risks": output.risks,
            "flags": [f.rule_name for f in output.policy_flags],
        }

    return EvalResult(
        case_id=case.id, description=case.description,
        passed=passed, notes=notes,
        output_summary=summary, error=error_msg,
    )


# ---------------------------------------------------------------------------
# State helpers (used by EVAL-03 to simulate credit-cap scenario)
# ---------------------------------------------------------------------------

def patch_student_credits(student_id: str, credits: int):
    STUDENTS[student_id]["credits_this_semester"] = credits

def unpatch_student_credits(student_id: str, credits: int):
    STUDENTS[student_id]["credits_this_semester"] = credits


# ---------------------------------------------------------------------------
# Eval cases
# ---------------------------------------------------------------------------

EVAL_CASES: list[EvalCase] = [

    # ── EVAL-01 ────────────────────────────────────────────────────────────
    # HAPPY PATH
    # Alice (S001, GPA 3.7, good standing) asks for ML/analytics courses.
    # She has completed MGSC301, so MGSC401 is the natural next step.
    # MGSC415 requires MGSC401 (not done) → excluded.
    # Expected: MGSC401 recommended, no approval needed.
    EvalCase(
        id="EVAL-01",
        description="Happy path: eligible student requests ML/analytics courses",
        student_id="S001",
        message="I want to take machine learning and analytics courses next semester.",
        grader=lambda out, err: (
            (
                any(c.course_code == "MGSC401" for c in out.recommended_courses)
                and not out.requires_advisor_approval,
                "MGSC401 recommended; no advisor approval required."
            )
            if out else (False, f"No output: {err}")
        ),
    ),

    # ── EVAL-02 ────────────────────────────────────────────────────────────
    # MISSING PREREQUISITE (failure case)
    # Alice asks specifically for MGSC415, but she hasn't taken MGSC401.
    # The agent must NOT recommend MGSC415 and must surface the prereq gap.
    EvalCase(
        id="EVAL-02",
        description="Missing prerequisite: MGSC415 requires MGSC401 which is not completed",
        student_id="S001",
        message="Can I take MGSC415 Machine Learning for Business this semester?",
        grader=lambda out, err: (
            (
                all(c.course_code != "MGSC415" for c in out.recommended_courses)
                and len(out.risks) > 0,
                "MGSC415 correctly excluded; risk about missing MGSC401 returned."
            )
            if out else (False, f"No output: {err}")
        ),
    ),

    # ── EVAL-03 ────────────────────────────────────────────────────────────
    # GUARDRAIL FIRES (edge case)
    # Ben (S002) is on academic probation. We patch his credits to 12 (the cap).
    # PROBATION_OVERLOAD_GUARDRAIL fires BEFORE the agent runs.
    # Expected: RuntimeError raised, no CourseRecommendation produced.
    EvalCase(
        id="EVAL-03",
        description="Guardrail fires: probation student at 12-credit cap is blocked",
        student_id="S002",
        message="I want to add three more courses to my schedule.",
        expect_blocked=True,
        grader=lambda out, err: (
            True, "Guardrail correctly blocked the request before any tool calls."
        ),
    ),

    # ── EVAL-04 ────────────────────────────────────────────────────────────
    # POLICY BOUNDARY — GPA above minimum
    # Chloe (S003, GPA 3.2) asks for the capstone MGSC499.
    # GPA 3.2 ≥ 3.0 minimum → no approval needed. She has all prereqs.
    # Expected: MGSC499 recommended, requires_advisor_approval=False.
    EvalCase(
        id="EVAL-04",
        description="Capstone eligible: GPA 3.2 meets the 3.0 minimum",
        student_id="S003",
        message="I want to take the Analytics Capstone (MGSC499) next semester.",
        grader=lambda out, err: (
            (
                any(c.course_code == "MGSC499" for c in out.recommended_courses)
                and not out.requires_advisor_approval,
                "MGSC499 recommended; GPA 3.2 ≥ 3.0, no approval required."
            )
            if out else (False, f"No output: {err}")
        ),
    ),

    # ── EVAL-05 ────────────────────────────────────────────────────────────
    # ALREADY-COMPLETED COURSE (edge case)
    # Chloe already completed MGSC301. The agent must not recommend it again.
    EvalCase(
        id="EVAL-05",
        description="Edge: student requests a course they already completed",
        student_id="S003",
        message="I already completed MGSC301. Should I retake Introduction to Analytics?",
        grader=lambda out, err: (
            (
                all(c.course_code != "MGSC301" for c in out.recommended_courses),
                "MGSC301 correctly excluded; agent did not recommend a completed course."
            )
            if out else (False, f"No output: {err}")
        ),
    ),

    # ── EVAL-06 ────────────────────────────────────────────────────────────
    # OFF-CATALOG QUERY (edge case)
    # Alice asks for neuroscience courses. Search returns nothing.
    # Expected: empty recommendations, non-empty risks explaining why.
    EvalCase(
        id="EVAL-06",
        description="Edge: query matches nothing in the course catalog",
        student_id="S001",
        message="I want to study neuroscience and cognitive science courses.",
        grader=lambda out, err: (
            (
                len(out.recommended_courses) == 0 and len(out.risks) > 0,
                "No courses recommended; risks explain the empty result."
            )
            if out else (False, f"No output: {err}")
        ),
    ),

    # ── EVAL-07 ────────────────────────────────────────────────────────────
    # APPROVAL REQUIRED — GPA below capstone minimum (failure case)
    # Diego (S004, GPA 1.8) HAS completed all MGSC499 prerequisites
    # (MGSC301, MGSC310, MGSC401), but GPA 1.8 < 3.0 minimum.
    # flag_policy_risk must return capstone_gpa_below_minimum.
    # Expected: requires_advisor_approval=True, capstone_gpa_below_minimum in flags.
    EvalCase(
        id="EVAL-07",
        description="Approval required: prereqs met but GPA 1.8 is below capstone minimum",
        student_id="S004",
        message="I want to take the Analytics Capstone MGSC499.",
        grader=lambda out, err: (
            (
                out.requires_advisor_approval
                and any(f.rule_name == "capstone_gpa_below_minimum"
                        for f in out.policy_flags),
                "requires_advisor_approval=True; capstone_gpa_below_minimum flag present."
            )
            if out else (False, f"No output: {err}")
        ),
    ),

    # ── EVAL-08 ────────────────────────────────────────────────────────────
    # GUARDRAIL BOUNDARY — probation student BELOW cap (does NOT fire)
    # Ben (S002) is on probation but only has 3 credits enrolled.
    # Guardrail condition requires credits_this_semester >= 12. Must NOT fire.
    # Expected: agent runs, returns output (even if risky or empty courses).
    EvalCase(
        id="EVAL-08",
        description="Guardrail boundary: probation student at 3 credits — guardrail must NOT fire",
        student_id="S002",
        message="What analytics courses can I take next semester?",
        expect_blocked=False,
        grader=lambda out, err: (
            (True, "Guardrail did not fire; agent ran and returned a recommendation.")
            if out else (False, f"Unexpected block or no output: {err}")
        ),
    ),

    # ── EVAL-09 ────────────────────────────────────────────────────────────
    # MIXED ELIGIBILITY
    # Alice asks for two specific courses: MGSC401 (eligible) and MGSC415
    # (not eligible — missing MGSC401). Agent must recommend only the eligible one.
    EvalCase(
        id="EVAL-09",
        description="Mixed eligibility: one eligible course, one blocked by missing prereq",
        student_id="S001",
        message="I want to take MGSC401 Predictive Analytics and MGSC415 Machine Learning for Business.",
        grader=lambda out, err: (
            (
                any(c.course_code == "MGSC401" for c in out.recommended_courses)
                and all(c.course_code != "MGSC415" for c in out.recommended_courses)
                and len(out.risks) > 0,
                "MGSC401 recommended; MGSC415 excluded; risk surfaced."
            )
            if out else (False, f"No output: {err}")
        ),
    ),

    # ── EVAL-10 ────────────────────────────────────────────────────────────
    # NO SEATS AVAILABLE — approval required
    # Alice asks for FINE410 (Quantitative Finance). She has the prereqs
    # (FINE301 + MGSC301), but FINE410 has 0 seats.
    # flag_policy_risk must return no_seats_available → approval required.
    EvalCase(
        id="EVAL-10",
        description="Approval required: course has no available seats",
        student_id="S001",
        message="I want to take FINE410 Quantitative Finance.",
        grader=lambda out, err: (
            (
                out.requires_advisor_approval
                and any(f.rule_name == "no_seats_available" for f in out.policy_flags),
                "requires_advisor_approval=True; no_seats_available flag present."
            )
            if out else (False, f"No output: {err}")
        ),
    ),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_all_evals() -> list[EvalResult]:
    results = []

    for case in EVAL_CASES:
        print(f"\n[{case.id}] {case.description}")

        # EVAL-03 needs S002 at the credit cap
        if case.id == "EVAL-03":
            patch_student_credits("S002", 12)

        try:
            result = await run_case(case)
        except Exception:
            result = EvalResult(
                case_id=case.id, description=case.description,
                passed=False,
                notes="Unexpected exception during eval.",
                error=traceback.format_exc(),
            )
        finally:
            if case.id == "EVAL-03":
                unpatch_student_credits("S002", 3)

        status = "✅ PASS" if result.passed else "❌ FAIL"
        print(f"  {status} — {result.notes}")
        if result.output_summary:
            print(f"  Output: {json.dumps(result.output_summary, indent=4)}")
        if result.error:
            print(f"  Error: {result.error[:300]}")

        results.append(result)

    passed = sum(1 for r in results if r.passed)
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{len(results)} passed")
    print(f"{'='*50}")
    return results


if __name__ == "__main__":
    asyncio.run(run_all_evals())
