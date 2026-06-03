# McGill Course Advisor Agent

A working single-agent academic advising system built with the **OpenAI Agents SDK** (Python).  
Given a student's natural-language request, the agent looks up their profile, searches the course catalog, checks prerequisites, flags policy violations, and returns a typed structured recommendation — including an explicit signal when a human advisor must approve.

> **Assignment:** Single-Agent AI System — MGSC 697  
> **Scope:** One agent · 4 typed tools · 1 named guardrail · 10 eval cases · structured output

---

## Project structure

```
mcgill_advisor_agent/
├── fixtures.py        # 12 courses, 5 students, policy rules (the agent's world)
├── advisor_agent.py   # Agent definition, 4 tools, guardrail, CourseRecommendation type
├── evals.py           # 10 graded eval cases with deterministic pass/fail graders
├── save_evidence.py   # Runs demos + all evals, writes traces/ evidence packet
├── traces/            # Auto-generated evidence (JSON + SUMMARY.md)
├── .env               # Your OpenAI API key (gitignored)
├── .env.example       # Template
├── requirements.txt   # openai-agents, pydantic, python-dotenv
├── .gitignore
├── README.md          # This file
└── reflection.md      # 2-page failure analysis
```

---

## Setup

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure your API key
cp .env.example .env
# Edit .env and replace the placeholder with your real key:
#   OPENAI_API_KEY=sk-...
```

---

## Run

```bash
# Single demo (default: S001, ML/analytics request)
python advisor_agent.py

# Try different students
python advisor_agent.py S002 "I want to add three more courses."  # guardrail fires
python advisor_agent.py S004 "I want to take MGSC499."           # approval required
python advisor_agent.py S005 "What courses can I start with?"    # fresh student

# Run all 10 eval cases
python evals.py

# Generate full evidence packet → saves to traces/
python save_evidence.py
```

**Student IDs:**

| ID | Name | GPA | Standing | Completed |
|---|---|---|---|---|
| S001 | Alice Chen | 3.7 | Good | MGSC301, MGSC310, FINE301, INFO201 |
| S002 | Ben Torres | 1.6 | Probation | MGSC301, INFO201 |
| S003 | Chloe Park | 3.2 | Good | MGSC301–415, FINE301, INFO201, MGMT301 |
| S004 | Diego Reyes | 1.8 | Probation | MGSC301, MGSC310, MGSC401, INFO201 |
| S005 | Emma Wilson | 3.5 | Good | (none — first semester) |

---

## How it works — step by step

### The fixture world

The agent operates in a deliberately small, self-contained world defined in `fixtures.py`:

- **12 courses** across INFO (200-level), MGSC/FINE/MGMT (300-level), and MGSC/FINE (400-level). One course — FINE410 — has 0 available seats, deliberately testing the seat-availability policy path. MGSC499 (Capstone) requires a 3.0 GPA minimum.
- **5 student profiles**, each designed to exercise a different scenario: happy path, probation at cap (guardrail fires), probation below cap (guardrail doesn't fire), prereqs met but GPA too low (approval required), brand-new student (minimal options).
- **Policy rules**: 18-credit semester maximum; 12-credit cap on probation; 3.0 GPA for capstone; advisor approval required for no-seat courses.

### What happens on every request

**Step 0 — Guardrail (before any LLM call)**  
`PROBATION_OVERLOAD_GUARDRAIL` runs synchronously before the agent loop starts. It reads the student's standing and current credit load from fixtures. If `standing == "probation"` AND `credits_this_semester >= 12`, the request is rejected immediately with `InputGuardrailTripwireTriggered` — no tool calls, no API cost, no recommendation produced. The caller catches this and surfaces it as a human-escalation message.

**Step 1 — `get_student_profile`**  
Always the first tool call. Returns GPA, standing, completed courses, and current enrollment for the student in context. This is the only source of truth about who the agent is advising.

**Step 2 — `search_courses`**  
Searches the 12-course catalog by keyword, department, and/or level. Query tokens (e.g. "machine", "learning", "analytics") are matched individually against course names and descriptions — not as a phrase — so multi-word queries work correctly. The agent is only allowed to recommend courses that appear in these results; it cannot hallucinate course codes.

**Step 3 — `check_prerequisites` (one call per candidate)**  
For each course from Step 2, the agent checks whether the student's `completed_courses` list satisfies that course's `prerequisites`. Returns `eligible`, `missing_prerequisites`, and `already_completed`. Ineligible courses are excluded from the shortlist before Step 4.

**Step 4 — `flag_policy_risk`**  
Called once with the shortlisted eligible course codes. Checks three rules:
- Credit overload on probation (proposed total > 12-credit cap)
- GPA below capstone minimum (MGSC499 requires 3.0)
- No seats available (FINE410 has 0 seats)

Any flag with `requires_advisor_approval=True` propagates to the output object.

**Step 5 — Structured output**  
The agent produces a `CourseRecommendation` Pydantic object. This is always a valid, typed object — never free text. Downstream code can read `recommended_courses`, `requires_advisor_approval`, and `policy_flags` programmatically without parsing.

**Step 6 — Deterministic validation**  
Before returning the object to the caller, the app validates the model's structured output against the fixture catalog. This removes any course that was not relevant to the request, already completed, missing prerequisites, or outside the catalog, then recomputes policy flags and total credits. This keeps the final output consistent even if the model's narrative summary or shortlist is imperfect.

### Example: Alice (S001) asks for ML courses

```
Request: "I want to take machine learning and analytics courses next semester."

Step 0: Guardrail check → Alice is good standing, 0 credits → passes
Step 1: get_student_profile → GPA 3.7, completed [MGSC301, MGSC310, FINE301, INFO201]
Step 2: search_courses("machine learning analytics") → [MGSC401, MGSC415, MGSC499, ...]
Step 3: check_prerequisites
        MGSC401 → eligible ✅ (has MGSC301)
        MGSC415 → ineligible ❌ (missing MGSC401)
        MGSC499 → ineligible ❌ (missing MGSC401)
Step 4: flag_policy_risk(["MGSC401"]) → no flags
Step 5: Output:
  recommended_courses: [MGSC401]
  risks: ["MGSC415 requires MGSC401 (not yet completed)", ...]
  requires_advisor_approval: false
```

---

## Agent design

### One agent, not five

The assignment scope is one focused agent done well. Splitting into specialists (a "search agent", a "policy agent") would add coordination overhead and trace complexity without improving correctness. All tool calls are owned by a single `McGill Course Advisor` agent.

### State strategy

| What | Where | Why |
|---|---|---|
| **Student identity** | `AdvisorContext.student_id`, passed as `context=` to `Runner.run` | Persists through the run without polluting the system prompt |
| **Student profile** | Fetched by `get_student_profile` tool at the start of each run | Recomputed from fixtures every call — no stale state risk |
| **Tool results** | Returned by deterministic tools, held in the Runner's message history | The agent synthesizes them; no manual state management needed |
| **Final recommendation** | Validated by deterministic post-processing before return | Prevents stale summaries or invalid courses from reaching the caller |
| **Conversation history** | Runner in-memory session (single-turn by default) | Pass a `SQLiteSession` for multi-turn persistence |

The key principle: **identity in context, facts in tools, synthesis in the model.**

### Tools (4 typed `@function_tool`)

| Tool | Input | Output |
|---|---|---|
| `get_student_profile` | *(none — uses context)* | Student dict (GPA, standing, courses) |
| `search_courses` | `query`, `department?`, `level?` | List of matching course dicts |
| `check_prerequisites` | `course_code` | `{eligible, missing_prerequisites, already_completed}` |
| `flag_policy_risk` | `course_codes: list[str]` | List of policy flag dicts |

Every tool returns deterministic results from in-memory fixtures. The model cannot hallucinate facts that come from tools.

### Guardrail: `PROBATION_OVERLOAD_GUARDRAIL`

```python
@input_guardrail
async def probation_credit_guardrail(ctx, agent, input) -> GuardrailFunctionOutput:
    ...
```

- **Type:** `@input_guardrail` — runs before the agent loop
- **Condition:** `student.standing == "probation"` AND `credits_this_semester >= 12`
- **Effect:** `tripwire_triggered=True` → raises `InputGuardrailTripwireTriggered`
- **Why input (not output):** Enrollment is irreversible. Blocking at the input means no tool calls are made, no API cost is spent, and the student gets an immediate escalation message rather than a polished recommendation that the registrar will reject.

### Structured output: `CourseRecommendation`

```python
class CourseRecommendation(BaseModel):
    recommended_courses: list[CourseSlot]   # only eligible courses
    risks: list[str]                         # ineligibility reasons, warnings
    policy_flags: list[PolicyFlag]           # structured policy violations
    requires_advisor_approval: bool          # machine-readable escalation signal
    approval_reason: Optional[str]           # human-readable explanation
    total_recommended_credits: int
    summary: str                             # 2–3 sentence narrative
```

The output is always a valid Pydantic object. Any caller can read `requires_advisor_approval` without parsing text.

---

## Eval cases

10 cases covering the full scenario space. Graders check structured fields — never free text.

| ID | Student | Scenario | Type | Pass condition |
|---|---|---|---|---|
| EVAL-01 | S001 | Requests ML/analytics courses | Happy path | MGSC401 in output; no approval |
| EVAL-02 | S001 | Requests MGSC415 (missing prereq) | Failure | MGSC415 excluded; risks populated |
| EVAL-03 | S002 | At 12-credit cap on probation | **Guardrail fires** | Request blocked before agent runs |
| EVAL-04 | S003 | Requests capstone, GPA 3.2 ≥ 3.0 | Policy boundary | MGSC499 recommended; no approval |
| EVAL-05 | S003 | Requests already-completed MGSC301 | Edge | MGSC301 not in recommendations |
| EVAL-06 | S001 | Asks for neuroscience (off-catalog) | Edge / empty | Empty recommendations; risks explain |
| EVAL-07 | S004 | Requests capstone, GPA 1.8 < 3.0 | **Approval required** | `requires_advisor_approval=True`; `capstone_gpa_below_minimum` flag |
| EVAL-08 | S002 | On probation, only 3 credits (below cap) | **Guardrail boundary** | Guardrail does NOT fire; agent runs |
| EVAL-09 | S001 | Requests one eligible + one ineligible | Mixed eligibility | Only eligible course recommended |
| EVAL-10 | S001 | Requests FINE410 (0 seats) | **Approval required** | `requires_advisor_approval=True`; `no_seats_available` flag |

EVAL-03 and EVAL-08 together verify both sides of the guardrail boundary condition.  
EVAL-07 and EVAL-10 verify two distinct approval paths (GPA vs. seat availability).

---

## Known limitations

See `reflection.md` for full failure analysis. Short version:

- Tool call sequence enforced by system prompt, not code — a more robust fix would be a pre-run hook
- `gpt-4o-mini` exceeded 30 turns on this workflow; `gpt-4o` is required
- Single-turn only; pass `SQLiteSession` for multi-turn persistence
- One hard guardrail; suspended students and registration deadlines are soft-flagged only

---

## Cost

`gpt-4o` with ~7–10 turns per run. A full `python evals.py` (10 cases) costs approximately **$0.10–0.20**. `python save_evidence.py` (5 demos + 10 evals) costs approximately **$0.25–0.35**.
