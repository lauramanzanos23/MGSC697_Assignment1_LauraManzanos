"""
fixtures.py
-----------
Self-contained world the McGill Course Advisor agent operates in.
12 courses, 5 student profiles, prerequisite table, and policy rules.
All plain Python dicts — no database needed.
"""

from __future__ import annotations
from typing import Any

# ---------------------------------------------------------------------------
# Course catalog — 12 courses across MGSC, FINE, MGMT, INFO departments
# ---------------------------------------------------------------------------

COURSES: dict[str, dict[str, Any]] = {
    # ── 200-level (intro, no prereqs) ──────────────────────────────────────
    "INFO201": {
        "code": "INFO201",
        "name": "Programming for Business",
        "credits": 3,
        "department": "INFO",
        "level": 200,
        "description": "Python programming fundamentals applied to business data tasks.",
        "prerequisites": [],
        "seats_available": 20,
    },
    # ── 300-level (foundations) ────────────────────────────────────────────
    "MGSC301": {
        "code": "MGSC301",
        "name": "Introduction to Analytics",
        "credits": 3,
        "department": "MGSC",
        "level": 300,
        "description": "Foundations of data analysis, descriptive statistics, and visualization.",
        "prerequisites": [],
        "seats_available": 12,
    },
    "MGSC310": {
        "code": "MGSC310",
        "name": "Database Management",
        "credits": 3,
        "department": "MGSC",
        "level": 300,
        "description": "Relational databases, SQL, and data modeling for business applications.",
        "prerequisites": [],
        "seats_available": 15,
    },
    "MGSC320": {
        "code": "MGSC320",
        "name": "Operations Research",
        "credits": 3,
        "department": "MGSC",
        "level": 300,
        "description": "Linear programming, network models, and optimization methods.",
        "prerequisites": [],
        "seats_available": 10,
    },
    "FINE301": {
        "code": "FINE301",
        "name": "Financial Modelling",
        "credits": 3,
        "department": "FINE",
        "level": 300,
        "description": "Spreadsheet-based financial models, DCF, and scenario analysis.",
        "prerequisites": [],
        "seats_available": 6,
    },
    "MGMT301": {
        "code": "MGMT301",
        "name": "Business Ethics and AI Governance",
        "credits": 3,
        "department": "MGMT",
        "level": 300,
        "description": "Ethical frameworks, accountability, and governance for AI deployments.",
        "prerequisites": [],
        "seats_available": 25,
    },
    # ── 400-level (advanced, have prereqs) ────────────────────────────────
    "MGSC401": {
        "code": "MGSC401",
        "name": "Predictive Analytics",
        "credits": 3,
        "department": "MGSC",
        "level": 400,
        "description": "Regression, classification, model evaluation, and cross-validation.",
        "prerequisites": ["MGSC301"],
        "seats_available": 8,
    },
    "MGSC415": {
        "code": "MGSC415",
        "name": "Machine Learning for Business",
        "credits": 3,
        "department": "MGSC",
        "level": 400,
        "description": "Supervised and unsupervised learning applied to real business problems.",
        "prerequisites": ["MGSC401"],
        "seats_available": 5,
    },
    "MGSC430": {
        "code": "MGSC430",
        "name": "Big Data and Cloud Analytics",
        "credits": 3,
        "department": "MGSC",
        "level": 400,
        "description": "Distributed computing, Spark, and cloud platforms for large-scale data.",
        "prerequisites": ["MGSC310"],
        "seats_available": 7,
    },
    "MGSC450": {
        "code": "MGSC450",
        "name": "Agentic AI Systems",
        "credits": 3,
        "department": "MGSC",
        "level": 400,
        "description": "Design and governance of autonomous AI agents and multi-agent systems.",
        "prerequisites": ["MGSC415"],
        "seats_available": 20,
    },
    "FINE410": {
        "code": "FINE410",
        "name": "Quantitative Finance",
        "credits": 3,
        "department": "FINE",
        "level": 400,
        "description": "Derivatives pricing, risk management, and stochastic models.",
        "prerequisites": ["FINE301", "MGSC301"],
        "seats_available": 0,   # NO SEATS — triggers advisor approval
    },
    # ── Capstone (6 credits, strict GPA requirement) ───────────────────────
    "MGSC499": {
        "code": "MGSC499",
        "name": "Capstone: Analytics Project",
        "credits": 6,
        "department": "MGSC",
        "level": 400,
        "description": "Independent research applying analytics to a real business problem.",
        "prerequisites": ["MGSC401", "MGSC310"],
        "seats_available": 3,
    },
}

# ---------------------------------------------------------------------------
# Student profiles — 5 students covering diverse scenarios
# ---------------------------------------------------------------------------

STUDENTS: dict[str, dict[str, Any]] = {
    # Good standing, mid-program — happy-path student
    "S001": {
        "student_id": "S001",
        "name": "Alice Chen",
        "program": "BCom - Analytics",
        "year": 3,
        "gpa": 3.7,
        "standing": "good",
        "completed_courses": ["MGSC301", "MGSC310", "FINE301", "INFO201"],
        "current_enrollment": [],
        "credits_this_semester": 0,
    },
    # Academic probation, few credits — guardrail fires when at cap
    "S002": {
        "student_id": "S002",
        "name": "Ben Torres",
        "program": "BCom - Finance",
        "year": 2,
        "gpa": 1.6,
        "standing": "probation",
        "completed_courses": ["MGSC301", "INFO201"],
        "current_enrollment": ["MGMT301"],
        "credits_this_semester": 3,
    },
    # Near graduation, good standing — capstone-eligible
    "S003": {
        "student_id": "S003",
        "name": "Chloe Park",
        "program": "BCom - Analytics",
        "year": 4,
        "gpa": 3.2,
        "standing": "good",
        "completed_courses": [
            "MGSC301", "MGSC310", "MGSC401", "MGSC415",
            "FINE301", "INFO201", "MGMT301",
        ],
        "current_enrollment": [],
        "credits_this_semester": 0,
    },
    # Probation, prereqs met for capstone, but GPA too low — EVAL-07
    "S004": {
        "student_id": "S004",
        "name": "Diego Reyes",
        "program": "BCom - Analytics",
        "year": 4,
        "gpa": 1.8,
        "standing": "probation",
        "completed_courses": ["MGSC301", "MGSC310", "MGSC401", "INFO201"],
        "current_enrollment": [],
        "credits_this_semester": 3,   # below 12-credit cap → guardrail does NOT fire
    },
    # Fresh student, just started — very few options
    "S005": {
        "student_id": "S005",
        "name": "Emma Wilson",
        "program": "BCom - General",
        "year": 1,
        "gpa": 3.5,
        "standing": "good",
        "completed_courses": [],
        "current_enrollment": [],
        "credits_this_semester": 0,
    },
}

# ---------------------------------------------------------------------------
# Policy rules
# ---------------------------------------------------------------------------

POLICIES: dict[str, Any] = {
    "max_credits_per_semester": 18,
    "max_credits_on_probation": 12,
    "min_gpa_for_capstone": 3.0,
    "advisor_approval_required_if": [
        "student_on_probation_and_over_credit_limit",
        "capstone_gpa_below_minimum",
        "no_seats_available",
    ],
    "descriptions": {
        "student_on_probation_and_over_credit_limit": (
            "Students on academic probation may not carry more than "
            "12 credits per semester without explicit advisor approval."
        ),
        "capstone_gpa_below_minimum": (
            "MGSC499 requires a cumulative GPA of 3.0. "
            "Students below this threshold need written advisor sign-off "
            "before the registrar will process the enrollment."
        ),
        "no_seats_available": (
            "The requested course has no available seats. "
            "Waitlist enrollment requires advisor approval and is not guaranteed."
        ),
    },
}
