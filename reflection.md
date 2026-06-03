# Reflection: McGill Course Advisor Agent

## What I set out to build

A single-agent course advisor that takes a student's natural-language request, grounds every decision in deterministic fixture data, and returns a typed `CourseRecommendation` object — including an honest risk list and a machine-readable escalation flag when human approval is required. The design principle was: narrow scope, done correctly, with evidence of failure.

---

## What worked

**Deterministic tools eliminate a whole class of errors.** The most important architectural decision was making every tool return facts from fixtures rather than letting the model reason about facts from memory. The model can hallucinate course codes and prerequisites in prose; it cannot hallucinate what `check_prerequisites` returns. Once tool outputs are deterministic, the only remaining failure modes are: wrong tool called, tool called in wrong order, or output mis-synthesized. These are narrower and more fixable than open-ended factual hallucination.

**Structured output as a contract, not a format.** Defining `CourseRecommendation` as a Pydantic model before writing any agent logic forced every design decision to be explicit. What does "approval required" mean? It means `requires_advisor_approval: bool = True` and `approval_reason: str` is populated. What is a "risk"? It is a `str` in `risks: list[str]`. Making these structural rather than narrative meant that graders, callers, and the evidence packet could all read the same fields without parsing text.

**Input guardrail placement.** Placing `PROBATION_OVERLOAD_GUARDRAIL` as an `@input_guardrail` — before the agent loop — rather than as a post-hoc check was the right call for two reasons. First, enrollment is irreversible within a semester; blocking early prevents wasted work. Second, an at-cap probation student who receives a polished recommendation and then gets rejected by the registrar has been harmed by the system, not helped. The guardrail fires at zero tool calls and zero API cost.

**10/10 eval cases with deterministic graders.** Every grader checks structured fields — `recommended_courses`, `requires_advisor_approval`, `policy_flags` — not free text. This means the evals catch regressions reliably. If the model changes its summary wording, the grader does not care. If it incorrectly recommends MGSC415, EVAL-02 catches it regardless of what the summary says.

**Post-output validation made the final object safer.** After the model returns a typed `CourseRecommendation`, the app validates it against the fixture catalog before returning it to the caller. This catches cases where the model's shortlist or narrative is slightly too broad — for example, recommending a course that was not relevant to the student's request — and recomputes policy flags and total credits from deterministic fixture data.

---

## What failed (in order of occurrence)

**1. `MaxTurnsExceeded` on the first real run (default limit: 10 turns).**  
The agent's workflow requires at minimum: profile → search → up to 4 prereq checks → policy check = 7+ loop turns. The SDK default of 10 was not enough. First fix: raised `max_turns` to 30. This exposed a second problem.

**2. `MaxTurnsExceeded` again at 30 turns, caused by duplicate search calls.**  
Even with 30 turns, the agent exceeded the limit because it called `search_courses` twice — once with `level=300` and once with `level=400` — treating them as two different queries despite the "call once" instruction in the system prompt. Root cause: `gpt-4o-mini` does not follow structured multi-step instructions reliably when tool-calling overhead is high. Fix: switched to `gpt-4o`, which follows the step-by-step prompt correctly and consistently finishes in 7–10 turns. This is a model-dependent correctness issue that is not visible from the code alone.

**3. `search_courses` returned empty results for multi-word queries.**  
The initial implementation matched the entire query string as a substring: `"machine learning analytics" in course["name"].lower()`. No course name contains that exact phrase, so the tool returned `[]`. The agent then hallucinated course codes (MGSC410, MGSC422, MGSC478 — none of which exist in the catalog), called `check_prerequisites` on each, received "Course not found" errors, and returned an empty recommendation with a confusing risk message. Fix: split the query into tokens and match if *any* token appears in the name or description. This is a tool schema bug — the tool's behavior did not match its docstring.

**4. Agent recommended a course the student had already completed.**  
The first system prompt said "recommend relevant courses" without explicitly prohibiting already-completed ones. In an early run, the agent recommended MGSC301 to a student who completed it the previous year. Fix: added an explicit rule to the instructions ("Never recommend a course the student has already completed") and added EVAL-05 to catch regressions. This illustrates a recurring theme: instructions that seem obvious to a human are not implied by the model.

**5. Guardrail predicate was off by one.**  
The initial condition was `credits_this_semester > 12`. A student at exactly 12 credits would not be blocked, even though adding any course would immediately violate the 12-credit cap. Fix: changed to `>= 12`. Added EVAL-08 (probation student at 3 credits) to verify the guardrail does not over-block, and EVAL-03 (at exactly 12) to verify the correct boundary.

**6. EVAL-07 failed because the test student lacked prerequisites.**  
EVAL-07 was designed to test the GPA-below-minimum approval path: student requests MGSC499 but GPA is too low. The first version used S002 (Ben Torres, GPA 1.6), but S002 had not completed MGSC401 — a prerequisite for MGSC499. The agent correctly blocked the course at the prerequisite stage and never reached the GPA check, so `flag_policy_risk` never fired. The grader expected a GPA flag that the agent had no reason to produce. Fix: added S004 (Diego Reyes, GPA 1.8, completed MGSC301/310/401 — all MGSC499 prerequisites) so the agent reaches `flag_policy_risk` with a valid shortlist and correctly surfaces `capstone_gpa_below_minimum`.

**7. EVAL-06 grader failed on the first save_evidence run.**  
EVAL-06 tests an off-catalog query ("neuroscience"). The grader required both `len(recommended_courses) == 0` AND `len(risks) > 0`. On one run, the agent returned MGSC401 and FINE410 despite the empty search result — it hallucinated recommendations because the system prompt did not explicitly say "only recommend courses from search results." Fix: added the rule "ONLY recommend courses that were returned by `search_courses`. If search returns empty, you MUST return empty recommended_courses." This is another case where an obvious constraint was not implied.

**8. Structured output could still contain stale narrative text.**  
After adding deterministic validation, the structured fields were correct, but the model's summary sometimes still mentioned a course that validation removed. This created a contradiction: `recommended_courses` was safe, but `summary` was stale. Fix: when validation changes the course list, the app now rewrites the summary from the validated course list instead of trusting the model's original prose.

---

## What is still risky

**Tool call sequence is enforced by prompt, not by code.** The system prompt says "Step 1: call get_student_profile, Step 2: call search_courses..." but nothing in the SDK prevents the agent from skipping steps or repeating them. In a production system, the correct fix is a pre-run hook that loads the student profile into context before the agent loop starts, removing the need for Step 1 entirely and eliminating one source of non-determinism.

**Model-dependent correctness.** `gpt-4o` follows the step-by-step instruction reliably; `gpt-4o-mini` does not. This means changing the model for cost reasons would silently break the agent. A production deployment would need model-specific evals and a fallback policy. Currently, only the `gpt-4o` baseline is tested.

**Single-turn statelessness.** Each `advise()` call is independent. A real student conversation spans multiple turns ("actually, can I swap MGSC401 for MGSC320?"), and the agent re-derives everything from scratch each time. The SDK supports `SQLiteSession` for turn-level persistence — adding it is a one-line change to `Runner.run` — but it would require its own eval cases for continuation and correction, which are out of scope here.

**One hard guardrail, many soft ones.** Only the probation-plus-credit-cap scenario is hard-blocked. Suspended students, students past the registration deadline, and students with outstanding tuition balances are not in the fixture world. Each additional hard block needs its own named guardrail, its own fixture scenario, and its own eval case. The pattern is established; the coverage is not production-ready.

**Fixture size is the real ceiling.** Twelve courses and five students make evals fast and cheap. A real McGill catalog has thousands of courses, complex co-requisite chains, section-level conflicts, and waitlist rules. The agent's tool design would scale — the tools are just dict lookups — but the fixtures would need an order-of-magnitude expansion before the system is genuinely useful.

---

## What I would do next

1. Move the student profile load from a Step 1 tool call into a pre-run hook that populates `AdvisorContext` before the agent loop starts. This eliminates one source of ordering errors.
2. Add `SQLiteSession` and two multi-turn eval cases: one testing continuation ("what if I swap that course?") and one testing correction ("I forgot I already completed that").
3. Add model-specific eval baselines so a model downgrade is detected automatically in CI.
4. Add at least two more named guardrails: `SUSPENDED_STUDENT_GUARDRAIL` and `REGISTRATION_DEADLINE_GUARDRAIL`, each with their own eval case.
5. Replace the raw `result.new_items` dump in verbose mode with a clean trace formatter that shows tool name → arguments → result on one line each. The current dump is useful for debugging but not readable by a grader or auditor.
