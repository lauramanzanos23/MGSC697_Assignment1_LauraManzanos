"""
advisor_agent.py
----------------
Single-agent McGill course advisor built with the OpenAI Agents SDK.

Architecture decisions:
  - One focused agent (no multi-agent — as per assignment scope).
  - Four typed function tools: search_courses, check_prerequisites,
    get_student_profile, flag_policy_risk.
  - Structured output: CourseRecommendation (Pydantic model).
  - One named guardrail: PROBATION_OVERLOAD_GUARDRAIL that blocks
    requests from probation students who are already at or above the
    12-credit cap.
  - State strategy: student_id passed as context; profile fetched once
    via tool; each run is single-turn and independent;
    tool results recomputed each call from fixtures (small world,
    no caching needed).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agents import (
    Agent,
    GuardrailFunctionOutput,
    InputGuardrailTripwireTriggered,
    RunContextWrapper,
    Runner,
    TResponseInputItem,
    function_tool,
    input_guardrail,
)
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv(Path(__file__).with_name(".env"))

try:
    from .fixtures import COURSES, POLICIES, STUDENTS
except ImportError:  # Allows `python advisor_agent.py` from this directory.
    from fixtures import COURSES, POLICIES, STUDENTS

# ---------------------------------------------------------------------------
# Context object threaded through every tool call
# ---------------------------------------------------------------------------

@dataclass
class AdvisorContext:
    """Carries student_id so tools can look up the right profile."""
    student_id: str


# ---------------------------------------------------------------------------
# Structured output type
# ---------------------------------------------------------------------------

class CourseSlot(BaseModel):
    course_code: str
    course_name: str
    credits: int
    reason: str                         # why this course was recommended


class PolicyFlag(BaseModel):
    rule_name: str
    description: str
    requires_advisor_approval: bool


class CourseRecommendation(BaseModel):
    """Typed output the agent always returns — can be consumed by any caller."""
    recommended_courses: list[CourseSlot]
    risks: list[str]                    # free-text risk notes
    policy_flags: list[PolicyFlag]      # structured policy violations
    requires_advisor_approval: bool
    approval_reason: Optional[str]      # populated only when approval needed
    total_recommended_credits: int
    summary: str                        # one-paragraph narrative for the student


GENERIC_REQUEST_TOKENS = {
    "academic", "advisor", "advising", "and", "available", "best", "can",
    "class", "classes", "course", "courses", "degree", "elective", "enroll",
    "enrollment", "first", "finish", "help", "next", "option", "options",
    "program", "recommend", "science", "semester", "should", "start",
    "started", "student", "study", "take", "term", "the", "want", "what",
    "year",
}


def _message_tokens(message: str) -> set[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in message)
    return {token for token in cleaned.split() if len(token) >= 3}


def _course_matches_tokens(course: dict, tokens: set[str]) -> bool:
    searchable = " ".join([
        course["code"],
        course["name"],
        course["description"],
        course["department"],
    ]).lower()
    searchable_tokens = _message_tokens(searchable)
    return any(token in searchable_tokens for token in tokens)


def _candidate_course_codes_from_message(message: str) -> set[str]:
    """Deterministically mirror the fixture search for output validation."""
    tokens = _message_tokens(message)
    explicit_codes = {code for code in COURSES if code.lower() in tokens}
    if explicit_codes:
        return explicit_codes

    meaningful_tokens = tokens - GENERIC_REQUEST_TOKENS
    matches = {
        code for code, course in COURSES.items()
        if meaningful_tokens and _course_matches_tokens(course, meaningful_tokens)
    }
    if matches:
        return matches

    # Generic requests like "what courses can I take as a first-year student?"
    # should allow broad advising. Off-catalog subject requests should not.
    if tokens and tokens <= GENERIC_REQUEST_TOKENS:
        return set(COURSES)
    return set()


def _policy_flags_for_student(student: dict, course_codes: list[str]) -> list[dict]:
    flags = []
    proposed_credits = sum(
        COURSES[c.upper()]["credits"]
        for c in course_codes
        if c.upper() in COURSES
    )
    total_credits = student["credits_this_semester"] + proposed_credits

    if student["standing"] == "probation":
        cap = POLICIES["max_credits_on_probation"]
        if total_credits > cap:
            flags.append({
                "rule_name": "student_on_probation_and_over_credit_limit",
                "description": POLICIES["descriptions"]["student_on_probation_and_over_credit_limit"],
                "requires_advisor_approval": True,
            })

    if "MGSC499" in [c.upper() for c in course_codes]:
        min_gpa = POLICIES["min_gpa_for_capstone"]
        if student["gpa"] < min_gpa:
            flags.append({
                "rule_name": "capstone_gpa_below_minimum",
                "description": POLICIES["descriptions"]["capstone_gpa_below_minimum"],
                "requires_advisor_approval": True,
            })

    for code in course_codes:
        course = COURSES.get(code.upper())
        if course and course["seats_available"] == 0:
            flags.append({
                "rule_name": "no_seats_available",
                "description": POLICIES["descriptions"]["no_seats_available"],
                "requires_advisor_approval": True,
            })

    max_credits = POLICIES["max_credits_per_semester"]
    if total_credits > max_credits:
        flags.append({
            "rule_name": "credit_overload",
            "description": f"Maximum allowed credits per semester is {max_credits}.",
            "requires_advisor_approval": False,
        })

    return flags


def _validated_recommendation(
    student_id: str,
    message: str,
    recommendation: CourseRecommendation,
) -> CourseRecommendation:
    """
    Safety validation after the model returns structured output.
    The agent still makes the recommendation, but this prevents the final
    object from containing courses outside the fixture search space, completed
    courses, or courses with unmet prerequisites.
    """
    student = STUDENTS.get(student_id)
    if not student:
        return recommendation

    candidate_codes = _candidate_course_codes_from_message(message)
    completed = set(student["completed_courses"])
    original_codes = [slot.course_code.upper() for slot in recommendation.recommended_courses]
    validated_courses: list[CourseSlot] = []
    risks = list(recommendation.risks)

    for slot in recommendation.recommended_courses:
        code = slot.course_code.upper()
        course = COURSES.get(code)
        if not course:
            risks.append(f"{code} removed because it is not in the fixture catalog.")
            continue
        if code not in candidate_codes:
            risks.append(f"{code} removed because it was not relevant to the student's request.")
            continue
        if code in completed:
            risks.append(f"{code} removed because the student already completed it.")
            continue
        missing = [p for p in course["prerequisites"] if p not in completed]
        if missing:
            risks.append(f"{code} removed because prerequisites are missing: {', '.join(missing)}.")
            continue
        validated_courses.append(slot)

    if not validated_courses and candidate_codes:
        for code in sorted(candidate_codes):
            course = COURSES[code]
            if code in completed:
                continue
            missing = [p for p in course["prerequisites"] if p not in completed]
            if missing:
                risks.append(f"{code} not recommended because prerequisites are missing: {', '.join(missing)}.")
                continue
            validated_courses.append(CourseSlot(
                course_code=course["code"],
                course_name=course["name"],
                credits=course["credits"],
                reason="Eligible based on fixture prerequisite and catalog checks.",
            ))
            if len(validated_courses) >= 4:
                break

    if not candidate_codes:
        validated_courses = []
        if not risks:
            risks.append("No matching courses were found in the fixture catalog for this request.")

    course_codes = [slot.course_code for slot in validated_courses]
    policy_flags_data = _policy_flags_for_student(student, course_codes)
    policy_flags = [
        PolicyFlag(
            rule_name=flag["rule_name"],
            description=flag["description"],
            requires_advisor_approval=flag["requires_advisor_approval"],
        )
        for flag in policy_flags_data
    ]
    requires_approval = any(flag.requires_advisor_approval for flag in policy_flags)
    approval_reason = recommendation.approval_reason if requires_approval else None
    if requires_approval and not approval_reason:
        approval_reason = "; ".join(flag.description for flag in policy_flags)

    total_credits = sum(COURSES[slot.course_code.upper()]["credits"] for slot in validated_courses)
    if not validated_courses and not risks:
        risks.append("No eligible courses could be recommended from the fixture catalog.")

    final_codes = [slot.course_code.upper() for slot in validated_courses]
    summary = recommendation.summary
    if final_codes != original_codes:
        if validated_courses:
            course_names = ", ".join(
                f"{slot.course_code} ({slot.course_name})"
                for slot in validated_courses
            )
            summary = f"Recommended eligible course(s): {course_names}."
            if requires_approval:
                summary += " Human advisor approval is required before enrollment."
        else:
            summary = "No eligible courses could be recommended from the fixture catalog for this request."
    elif not validated_courses:
        summary = "No eligible courses could be recommended from the fixture catalog for this request."

    return CourseRecommendation(
        recommended_courses=validated_courses,
        risks=risks,
        policy_flags=policy_flags,
        requires_advisor_approval=requires_approval,
        approval_reason=approval_reason,
        total_recommended_credits=total_credits,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@function_tool
def get_student_profile(
    ctx: RunContextWrapper[AdvisorContext],
) -> dict:
    """
    Return the academic profile of the current student.
    Includes GPA, standing, completed courses, and current enrollment.
    """
    sid = ctx.context.student_id
    student = STUDENTS.get(sid)
    if not student:
        return {"error": f"Student {sid} not found."}
    return student


@function_tool
def search_courses(
    ctx: RunContextWrapper[AdvisorContext],
    query: str,
    department: Optional[str] = None,
    level: Optional[int] = None,
) -> list[dict]:
    """
    Search the course catalog by keyword, department, or level.

    Args:
        query: Keyword(s) to match against course name or description.
               Pass an empty string to list all courses.
        department: Filter by department code, e.g. 'MGSC', 'FINE', 'MGMT'.
        level: Filter by course level (300 or 400).

    Returns a list of matching course records.
    """
    # Split into tokens so "machine learning analytics" matches any word
    tokens = [t for t in query.lower().split() if t] if query else []
    results = []
    for course in COURSES.values():
        name_lower = course["name"].lower()
        desc_lower = course["description"].lower()
        code_lower = course["code"].lower()
        if not tokens:
            text_match = True
        else:
            text_match = any(
                t in name_lower or t in desc_lower or t in code_lower
                for t in tokens
            )

        dept_match = (department is None) or (course["department"] == department.upper())
        level_match = (level is None) or (course["level"] == level)

        if text_match and dept_match and level_match:
            results.append(course)

    return results


@function_tool
def check_prerequisites(
    ctx: RunContextWrapper[AdvisorContext],
    course_code: str,
) -> dict:
    """
    Check whether the current student satisfies all prerequisites for a course.

    Returns:
        eligible (bool): True if prerequisites are met.
        missing (list[str]): Prerequisite codes the student still needs.
        already_completed (bool): True if the student already passed this course.
    """
    sid = ctx.context.student_id
    student = STUDENTS.get(sid)
    if not student:
        return {"error": f"Student {sid} not found."}

    course = COURSES.get(course_code.upper())
    if not course:
        return {"error": f"Course {course_code} not found in catalog."}

    completed = set(student["completed_courses"])
    already_completed = course_code.upper() in completed

    missing = [p for p in course["prerequisites"] if p not in completed]
    eligible = len(missing) == 0 and not already_completed

    return {
        "course_code": course_code.upper(),
        "eligible": eligible,
        "missing_prerequisites": missing,
        "already_completed": already_completed,
        "prerequisites_required": course["prerequisites"],
    }


@function_tool
def flag_policy_risk(
    ctx: RunContextWrapper[AdvisorContext],
    course_codes: list[str],
) -> list[dict]:
    """
    Evaluate a proposed set of courses against McGill policy rules.
    Returns a list of policy flags (may be empty if no violations found).

    Checks:
      - Credit overload for probation students
      - GPA requirement for capstone (MGSC499)
      - Seat availability
    """
    sid = ctx.context.student_id
    student = STUDENTS.get(sid)
    if not student:
        return [{"error": f"Student {sid} not found."}]

    return _policy_flags_for_student(student, course_codes)


# ---------------------------------------------------------------------------
# Guardrail — named, blocking, explicit
# ---------------------------------------------------------------------------

class ProbationOverloadCheck(BaseModel):
    """Output type of the guardrail's internal classification."""
    is_risky: bool
    reason: str


# The guardrail fires BEFORE the agent sees the message.
# It blocks students on academic probation who are already at or above
# the 12-credit cap from even starting a new course-selection run,
# since any addition would immediately violate policy.
@input_guardrail
async def probation_credit_guardrail(
    ctx: RunContextWrapper[AdvisorContext],
    agent: Agent,
    input: str | list[TResponseInputItem],
) -> GuardrailFunctionOutput:
    """
    PROBATION_OVERLOAD_GUARDRAIL
    ----------------------------
    Blocks course-advising requests from students on academic probation
    who have already reached the 12-credit per-semester ceiling.
    These students must speak with a human advisor before any new
    enrollment can proceed.
    """
    sid = ctx.context.student_id
    student = STUDENTS.get(sid)

    if not student:
        return GuardrailFunctionOutput(
            output_info=ProbationOverloadCheck(
                is_risky=False, reason="Student not found — will let agent handle."
            ),
            tripwire_triggered=False,
        )

    on_probation = student["standing"] == "probation"
    cap = POLICIES["max_credits_on_probation"]
    already_at_cap = student["credits_this_semester"] >= cap

    triggered = on_probation and already_at_cap
    reason = (
        f"Student {sid} is on academic probation and is already enrolled in "
        f"{student['credits_this_semester']} credits (cap: {cap}). "
        "Human advisor approval required before any new enrollment."
        if triggered
        else "No probation overload issue detected."
    )

    return GuardrailFunctionOutput(
        output_info=ProbationOverloadCheck(is_risky=triggered, reason=reason),
        tripwire_triggered=triggered,
    )


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

SYSTEM_INSTRUCTIONS = """\
You are an academic advisor for McGill University. Help the student choose courses.

STRICT TOOL CALL ORDER — follow exactly, no repetition:
Step 1: Call get_student_profile (once only).
Step 2: Call search_courses (once only) to find relevant courses.
Step 3: Call check_prerequisites for each course you are considering (one call per course, max 4 courses total).
Step 4: Call flag_policy_risk once with your shortlisted course codes.
Step 5: STOP calling tools. Produce your final CourseRecommendation output immediately.

Rules:
- ONLY recommend courses that were returned by search_courses. Never recommend a course that was not in those results.
- If search_courses returns an empty list, you MUST return empty recommended_courses. Explain why in risks and summary.
- Never recommend a course the student has already completed.
- Never recommend a course whose prerequisites the student has not met (missing_prerequisites is non-empty).
- If flag_policy_risk returns any flag with requires_advisor_approval=True, set requires_advisor_approval=True in output.
- risks list must be non-empty if any issue was found (no matching courses, missing prereqs, policy violations).
- After step 4, output your final answer. Do not call any tool again.
"""

advisor_agent = Agent(
    name="McGill Course Advisor",
    instructions=SYSTEM_INSTRUCTIONS,
    model="gpt-4o",               # better structured output compliance than gpt-4o-mini
    output_type=CourseRecommendation,
    tools=[
        get_student_profile,
        search_courses,
        check_prerequisites,
        flag_policy_risk,
    ],
    input_guardrails=[probation_credit_guardrail],
)


# ---------------------------------------------------------------------------
# Public run helper
# ---------------------------------------------------------------------------

async def advise(
    student_id: str,
    message: str,
    *,
    verbose: bool = False,
) -> CourseRecommendation:
    """
    Run one advisory turn for a student.

    State strategy:
      - student_id lives in AdvisorContext, passed once per run.
      - Tool results are recomputed from in-memory fixtures (no stale state).
      - No cross-turn session is used here; each call is an independent turn.
        If you want multi-turn conversation, pass a `session` to Runner.run.
    """
    context = AdvisorContext(student_id=student_id)
    try:
        result = await Runner.run(
            advisor_agent,
            message,
            context=context,
            max_turns=30,
        )
    except InputGuardrailTripwireTriggered as exc:
        # Surface the guardrail block as a structured recommendation
        raise RuntimeError(
            f"[PROBATION_OVERLOAD_GUARDRAIL TRIGGERED] "
            f"This request was blocked. The student must speak with a human advisor "
            f"before enrolling in additional courses. Detail: {exc}"
        ) from exc

    if verbose:
        print("\n=== Raw tool calls ===")
        for item in result.new_items:
            print(item)

    return _validated_recommendation(student_id, message, result.final_output)


# ---------------------------------------------------------------------------
# Quick interactive demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    student_id = sys.argv[1] if len(sys.argv) > 1 else "S001"
    message = (
        sys.argv[2]
        if len(sys.argv) > 2
        else "I want to take machine learning and analytics courses next semester."
    )

    async def main():
        print(f"\n=== McGill Course Advisor | Student: {student_id} ===")
        print(f"Request: {message}\n")
        try:
            rec = await advise(student_id, message, verbose=True)
            print("\n=== Recommendation ===")
            print(json.dumps(rec.model_dump(), indent=2))
        except RuntimeError as e:
            print(f"\n[BLOCKED] {e}")

    asyncio.run(main())
