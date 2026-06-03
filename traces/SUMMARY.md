# Evidence Packet — McGill Course Advisor Agent
Generated: 2026-06-03 04:13

---

## Demo Run Transcripts

### DEMO-01: Happy path — eligible student requests ML courses
- **Student:** `S001`
- **Request:** *I want to take machine learning and analytics courses next semester.*
- **Recommended:** ['MGSC401', 'MGSC430']
- **Requires approval:** False
- **Risks:** ['MGSC415 cannot be taken due to missing prerequisite MGSC401.', 'MGSC499 cannot be taken due to missing prerequisite MGSC401.']
- **Summary:** Alice Chen can enroll in MGSC401 and MGSC430 as they meet her prerequisites and are relevant to her interest in machine learning and analytics.

### DEMO-02: Guardrail fires — probation student at credit cap
- **Student:** `S002`
- **Request:** *I want to add three more courses next semester.*
- **Result:** 🚫 BLOCKED BY GUARDRAIL
- **Detail:** [PROBATION_OVERLOAD_GUARDRAIL TRIGGERED] This request was blocked. The student must speak with a human advisor before enrolling in additional courses. Detail: Guardrail InputGuardrail triggered tripwi

### DEMO-03: Capstone eligibility — GPA 3.2 passes the 3.0 threshold
- **Student:** `S003`
- **Request:** *I want to take the Analytics Capstone (MGSC499) next semester.*
- **Recommended:** ['MGSC499']
- **Requires approval:** False
- **Risks:** ["MGSC430 removed because it was not relevant to the student's request."]
- **Summary:** Recommended eligible course(s): MGSC499 (Capstone: Analytics Project).

### DEMO-04: Missing prerequisite — agent correctly blocks MGSC415
- **Student:** `S001`
- **Request:** *Can I take MGSC415 Machine Learning for Business this semester?*
- **Recommended:** None
- **Requires approval:** False
- **Risks:** ['You have not completed the prerequisite MGSC401 for MGSC415.', 'You have not completed the prerequisite MGSC401 for MGSC499.', "MGSC430 removed because it was not relevant to the student's request.", 'MGSC415 not recommended because prerequisites are missing: MGSC401.']
- **Summary:** No eligible courses could be recommended from the fixture catalog for this request.

### DEMO-05: No matching courses — query outside catalog
- **Student:** `S001`
- **Request:** *I want to study neuroscience and cognitive science courses.*
- **Recommended:** None
- **Requires approval:** False
- **Risks:** ["No matching courses found for 'neuroscience' or 'cognitive science'.", 'Course codes could not be verified due to catalog mismatches.']
- **Summary:** No eligible courses could be recommended from the fixture catalog for this request.

### DEMO-06: Approval required — GPA below capstone minimum
- **Student:** `S004`
- **Request:** *I want to take the Analytics Capstone MGSC499.*
- **Recommended:** ['MGSC499']
- **Requires approval:** True
- **Risks:** ['Your GPA is below the required minimum for enrollment in MGSC499', "MGSC430 removed because it was not relevant to the student's request."]
- **Summary:** Recommended eligible course(s): MGSC499 (Capstone: Analytics Project). Human advisor approval is required before enrollment.

### DEMO-07: First-semester student — only intro courses available
- **Student:** `S005`
- **Request:** *What courses can I take as a first-year student?*
- **Recommended:** ['MGSC301']
- **Requires approval:** False
- **Risks:** None
- **Summary:** Emma Wilson is recommended to take MGSC301: Introduction to Analytics. This course is suitable for a first-year student and meets all eligibility requirements.

---

## Eval Results — 10/10 passed

| ID | Description | Result | Notes |
|---|---|---|---|
| EVAL-01 | Happy path: eligible student requests ML/analytics courses | ✅ PASS | MGSC401 recommended; no advisor approval required. |
| EVAL-02 | Missing prerequisite: MGSC415 requires MGSC401 which is not completed | ✅ PASS | MGSC415 correctly excluded; risk about missing MGSC401 returned. |
| EVAL-03 | Guardrail fires: probation student at 12-credit cap is blocked | ✅ PASS | Guardrail correctly blocked the request before any tool calls. |
| EVAL-04 | Capstone eligible: GPA 3.2 meets the 3.0 minimum | ✅ PASS | MGSC499 recommended; GPA 3.2 ≥ 3.0, no approval required. |
| EVAL-05 | Edge: student requests a course they already completed | ✅ PASS | MGSC301 correctly excluded; agent did not recommend a completed course. |
| EVAL-06 | Edge: query matches nothing in the course catalog | ✅ PASS | No courses recommended; risks explain the empty result. |
| EVAL-07 | Approval required: prereqs met but GPA 1.8 is below capstone minimum | ✅ PASS | requires_advisor_approval=True; capstone_gpa_below_minimum flag present. |
| EVAL-08 | Guardrail boundary: probation student at 3 credits — guardrail must NOT fire | ✅ PASS | Guardrail did not fire; agent ran and returned a recommendation. |
| EVAL-09 | Mixed eligibility: one eligible course, one blocked by missing prereq | ✅ PASS | MGSC401 recommended; MGSC415 excluded; risk surfaced. |
| EVAL-10 | Approval required: course has no available seats | ✅ PASS | requires_advisor_approval=True; no_seats_available flag present. |

---

## Guardrail Evidence

EVAL-03 and DEMO-02 confirm that `PROBATION_OVERLOAD_GUARDRAIL` fires correctly.
The guardrail runs *before* the agent loop, blocking the request with `InputGuardrailTripwireTriggered`.
The caller catches this and surfaces it as a human-escalation message.
No tool calls are made. No recommendation is produced.