# SentinelChat - Demo Script

A step-by-step script for presenting SentinelChat (about 20 minutes): what to set up, exactly what to
type and click, what you should see, and what to say. Companion to [`USER_GUIDE.md`](USER_GUIDE.md)
(the full reference) and [`REVIEW_DRIFT_RETRAINING.md`](REVIEW_DRIFT_RETRAINING.md) (how it works).

> **Rehearsed.** Every step below was run against the real stack on 2026-10-05 (local `gemma4:12b`
> as the chat model). Numbers marked *(rehearsal)* came from that run - your run of the same data
> should be very close, but **rehearse once on your own machine first** and treat the exact numbers as
> "about", not as a script to read out. The assistant's *wording* differs every time; the tool it
> calls and the cards do not.

---

## The story to tell

> *AOI machines flag boards that might have defects, but humans still have to decide, and the AI
> models need correcting when they're wrong. SentinelChat puts an assistant and a review workflow
> around the models so that **every AI call can be checked, every human correction is captured, and
> corrections flow back into retraining - with a human approving at each step.***

Three things to point out as you go (lecturers tend to like these):

1. **Human in the loop, enforced in code.** Anything that saves a record is *propose -> you confirm
   in a later message*. If the model tries to do both in one turn, the backend refuses it.
2. **Agents with narrow jobs.** Inspection, relabelling, review, sample lookup and monitoring are
   separate agents with their own tools and permissions (QA vs Admin), and the AI can only call the
   tools it was given.
3. **A closed loop.** Operator corrections -> drift numbers -> retraining tickets -> a retraining plan
   -> an Admin approves it in the Models tab.

**Be upfront about the models.** The bundled classifiers are low-confidence on the sample data (the
region model is 40-58% sure, and even calls a "Body" image "Text"). Frame it as: *the point isn't the
classifier's accuracy, it's the workflow that catches and corrects its mistakes* - and low
confidence is exactly what triggers the review flow.

---

## Before you start

### 1. Start everything

From the repo root (see [`ONBOARDING.md`](../ONBOARDING.md) for the one-time setup):

```bash
bash infra/development/scripts/unix/start-dev.sh                    # macOS/Linux
powershell -File infra\development\scripts\windows\start-dev.ps1    # Windows
```

You need these running: Postgres, Qdrant, LiteLLM (Docker), the backend (`:8000`), the **inference
service (`:8001`)**, the UI (`http://localhost:4200`), and **Ollama** with a tool-calling model if you
use the local LLM (`OLLAMA_MODEL`, default `gemma4:12b`). For OpenAI instead, set
`LITELLM_OPENAI_API_KEY` in `.env` (see ONBOARDING).

Quick checks: `curl localhost:8000/health`, `curl localhost:8001/health` (should list four models),
and `http://localhost:6333/dashboard` (Qdrant).

### 2. Lower the confidence thresholds (important)

At the default 0.70 thresholds none of the bundled images get a confident region, so chat inspections
stop at *"review required, no defect model run"* and there is **no defect label to relabel**. For the
full demo set, in `.env`:

```
ADC_REGION_CONFIDENCE_THRESHOLD=0.4
ADC_DEFECT_CONFIDENCE_THRESHOLD=0.3
```

then **restart the backend** (it reads `.env` at start). Keep a second run at the defaults if you
want to show the "uncertain -> review" path (Act 2b).

The Work tab has its own per-run threshold boxes (set in the UI, Act 4).

### 3. Accounts

Register an account in the UI. **The first account ever registered is Admin** - use it so you can
approve retraining in Act 8. (To show the QA/Admin difference, register a second account afterwards;
it will be QA.)

### 4. Pick the demo files

| Use | File |
| --- | --- |
| Work tab dataset | `data/workflow/Sample_data_2/Sample_data_2/dataset.csv` (11 samples) |
| Work tab inspection XML | `data/workflow/Sample_data_2/Sample_data_2/7017-0203-07T-PVT-JIG.2026-09-30.17.24.52.i.xml` |
| Work tab image folder | `data/workflow/Sample_data_2/Sample_data_2` (the folder itself) |
| Chat image | `data/workflow/Sample_data_2/Sample_data_2/14-756603-AAA-RV1/Body/SolderInsuffcient_5/Board1_C754_Body_14-756603-AAA-RV1_20260930_172812654_SolderInsuffcient_5.jpg` |
| Chat image that needs review (Act 3b) | `data/workflow/Sample_data_2/Sample_data_2/32-462597-AAA-RV1/Gap/SolderInsuffcient_5/Board1_U58_Gap20_32-462597-AAA-RV1_20260930_172812755_SolderInsuffcient_5.jpg` |

### 5. Pre-flight (do this 10 minutes before)

- [ ] Both services answer their `/health`.
- [ ] Chat provider works: send "hello" in Chat.
- [ ] Do **one full dry run** of Act 4-5 (the Work tab takes ~35 s plus Agent 2). Then click
      **Clear Log** so you start the demo from an empty Work tab.
- [ ] Decide Local LLM vs OpenAI in the chat settings. The local 12B model handled every prompt
      below correctly; a hosted model is usually faster.

---

## Act 1 - Orientation (1 min)

Show the three areas (mode toggle: **Chat | Work | Models**) and the roles. Say: *"QA does the day
to day inspection and review; Admin approves anything that changes a model."*

---

## Act 2 - Inspect an image, and a case only on request (3 min)  - **Chat**

**2a. Inspect.** Attach the chat image and type:

> **What defect does this component have? It is C754 on Board1.**

*Expect:* a result card (tool: *Image inspection*) with the region and defect, their confidences,
runner-up scores, a verdict, and **"Not saved as a case"**. With the lowered thresholds *(rehearsal)*:
region **Text 50%**, defect **Golden 61%**, verdict *Accepted*. The assistant then **asks whether you
want a case created**.

*Say:* "The AI never saves on its own. The inspection is held as a draft until I say yes."

**2b. (Optional) The uncertain path.** At the default thresholds the same prompt gives *Review
required: region confidence 0.50 is below 0.70, so no defect model was run*. Say: "Low confidence
sends it to a human instead of guessing."

**2c. Create the case.** Reply in a **new message**:

> **Yes, please create a case for it.**

*Expect:* "Case **CASE-0081xx** has been created." (tool: *Case creation*)

*Say:* "Notice it could only do that in a later turn than the inspection. Try to make it do both at
once and the backend refuses." (If you want to prove it: this is covered by an automated test.)

---

## Act 3 - Correct the AI and approve a review (3 min)  - **Chat**

**3a. Relabel** (needs the case from Act 2 with a defect label):

> **That defect label is wrong - it is actually a wrong part, because the markings do not match the golden board.**

*Expect:* a **proposal card** (model label *Golden* -> *WrongPart*) and the question "Would you like
to confirm?". **Nothing is saved yet.** Then:

> **Yes, confirm it.**

*Expect:* a **"label corrected"** card and a **retraining ticket** queued. *Say:* "That ticket is
how a human correction becomes training signal. The model's original answer is kept next to it."

*Valid labels depend on the model that ran* (here `pcb_text_defect`: *Golden*, *WrongPart*). If you
pick one the model can't output, the assistant lists the valid ones - a nice moment to show it
fails safe.

**3b. Approve a review.** Only a case that was *Review required* can be reviewed, and at the demo
thresholds the first image is *Accepted*. In a **new chat**, attach the Act 3b image from the table
above (*rehearsal:* region `Lead` 0.40, just under the 0.40 threshold, so *Review required*), ask the
same question as 2a, say **"Yes, please create a case for it."**, then:

> **I have reviewed this case and the defect is real - approve it.**
> *then:* **Yes, confirm.**

*Expect:* a proposal card, then *Case reviewed - approved*.

---

## Act 4 - Run a dataset in the Work tab (3 min)  - **Work**

1. Choose **Dataset CSV**, **Inspection XML** and **Image Folder** from the table above.
2. Set **Feature confidence threshold = 0.3** (leave the defect threshold at 0.7). *At 0.7 every
   sample stops after the region stage and has no model recorded - see the note in
   [Troubleshooting](#if-something-goes-wrong).*
3. Click **3. Run Agentic Workflow**.

*Expect (~35 s, rehearsal):* the **Workflow Summary** reads **Input 11 / Prep Ready 11 / Verified 11 /
Inference 11 / Accepted 0 / Review 11**; the log streams the planner's steps, then
`[Agent 2] Escalating 11 sample(s) for Explainability Review...` with a block per sample:

```
[Agent 2 Diagnosis for S000001]
- Agent 1 Baseline : no defect
- Agent 2 Audit    : wrong part
[Review Required] S000001: Machine/AI evidence differs (no defect vs wrong part)...
```

*Say:* "Agent 1 is the two-stage classifier. Agent 2 is a second opinion with an explanation.
When they disagree, a human decides. In my rehearsal, 7 of 11 samples were disagreements."

---

## Act 5 - The human decision (3 min)  - **Work -> Review Console**

Click **Open Review Console** (Explanation Review tab).

1. Select **S000001** (Agent 1: *no defect*, Agent 2: *wrong part*). Show the golden vs defect
   images and Agent 2's explanation.
2. **User Final Decision -> Accept AI**, notes: `text is wrong on the part`, **Confirm Decision**.
   *Expect:* "Saved S000001: wrong part"; its status becomes *Reviewed*.
3. Select **S000004** -> **Manual**, choose **Tombstone**, notes `pad lifted`, **Confirm Decision**.
4. Select **S000003** (the agents agree) -> **Accept Machine** -> Confirm. *Say:* "Even when they agree
   the operator makes the call; nothing is auto-approved."

---

## Act 6 - The Drift & Retraining tab follows your decisions (2 min)  - **Work -> Review Console**

Click the **Drift & Retraining** tab. *Expect (rehearsal):*

- the tab title shows a badge **"2 to queue"**;
- a per-model table; `pcb_text_defect`: 9 samples, **Reviewed 3 / 11**, **Corrected 2 (67%)**,
  Agent 2 disagreed 6;
- **Operator corrections:** `S000001  Golden -> wrong part (AI)` and `S000004  Golden -> Tombstone
  (MANUAL)`, both *Ready*.

*Say:* "Every decision I just made updated this immediately - and these numbers are computed on the
server from what's stored, not from the browser. A *correction* means my final answer differs from
Agent 1's."

Click **Queue 2 for retraining**. *Expect:* "Queued 2 corrections for retraining"; both rows show
*Queued*; the badge disappears. Click it again (or Refresh): nothing is filed twice.

---

## Act 7 - Chat reads the same data (3 min)  - **Chat**

Type these one at a time *(rehearsal replies in italics)*:

> **What do you know about sample S000001?**

*Run id, board `Board1`, component `C754`, Agent 1 = Golden (0.61), Agent 2 = wrong part with its
explanation, and **the operator decision you just made**.* (tool: *Sample lookup*)

> **Which samples are still waiting for review?**

*A list of the remaining flagged samples with both agents' calls.* (tool: *Review queue*)

> **How did the models do in the last run, and what did the operator correct?**

*Per-model numbers matching the Drift & Retraining tab, and the two corrections - already queued.*
(tool: *Run drift*)

> **What do you know about sample S999999?**

*A "not found" that names the latest run - it fails safe rather than inventing data.*

*Say:* "The chat agent and the Work tab read the same Qdrant collections, so there's one source of
truth."

---

## Act 8 - Close the loop: plan -> approve (2 min)  - **Work or Chat -> Models**

Queueing in Act 6 created *tickets*; the Models tab's Retraining queue lists *jobs*, so it still says
"No retraining jobs yet" (and "2 unplanned") - a good moment to explain the two steps. Now draft the
plan, either way:

- **Work tab:** in **Drift & Retraining -> Retraining plan**, click **Draft retraining plan** next to
  `pcb_text_defect - 2 tickets waiting`. *Expect:* "Plan drafted for pcb_text_defect: 2 samples,
  pending approval. An Admin approves it in the Models tab."
- **or Chat:** type **Draft a retraining plan for pcb_text_defect.** *Expect:* "I've drafted a
  retraining plan... status **pending approval**..." (tool: *Retraining plan*)

Switch to **Models -> Retraining queue** (as Admin): the job shows **2 samples**, status *pending
approval*. Click **Show flagged cases** to see `S000001` and `S000004` with their run and
observed -> expected labels. Click **Approve**.

*Say:* "Anyone on the QA side can draft a plan; only an Admin in the Models tab can approve it. The inference service's
trainer here is a stub - it simulates the job - so this shows the control flow, not a real retrain."

Optional extras if time allows:

> **Is the pcb_text_defect model drifting?** *(over saved cases)*
> **What is the model status?** *(Admin overview: live versions, open reports/tickets/queue)*
> **Write me a poem about cats.** *(politely declined - it stays on topic)*

---

## Likely questions

| Question | Short answer |
| --- | --- |
| *Why does the assistant ask before creating a case / saving a correction?* | Records feed retraining, so a person must opt in. It is enforced in the backend (a later turn, same user), not just the prompt. |
| *Can the AI approve a retraining or promote a model?* | No. Chat can draft a plan; approve/promote are Admin-only and only in the Models tab. |
| *What stops the AI from inventing data?* | It can only report what tools return; unknown samples/cases return an error; tools it isn't allowed to call aren't even given to the model. |
| *Why are some classifier results low confidence / odd?* | The bundled models are weak on this data. Low confidence is the trigger for human review - which is the point of the workflow. |
| *What's Agent 1 vs Agent 2?* | Agent 1 = two-stage classifier (region -> defect). Agent 2 = an explainability review (precedents, telemetry, optional vision) that audits Agent 1. |
| *What is a "correction"?* | The operator's final result differs from Agent 1's label (after normalising names like `WrongPart_13` -> `wrong part`). |
| *Where is the data stored?* | Cases, users, tickets, plans: Postgres. Work-tab runs, Agent 2 reviews and decisions: Qdrant. Long-term chat memory: Qdrant. |
| *Does retraining really run?* | The trainer is a stub that simulates a job; the approval flow and bookkeeping are real. |

---

## If something goes wrong

| Symptom | Fix |
| --- | --- |
| Chat inspection says region uncertain, no defect | Thresholds are at 0.70. Lower them in `.env` and **restart the backend** (Before you start, step 2). |
| Work-tab run: Verified 0 or all samples "FEATURE_CLASSIFICATION_UNCERTAIN", corrections say *No model recorded* | Feature threshold too high. Re-run with **0.3**. |
| Verification fails / images not found | Choose the **folder** `Sample_data_2/Sample_data_2` as the Image Folder (not a parent folder). |
| Drift tab says the stored run could not be read | Qdrant is down: `docker compose -f infra/development/docker-compose.yml --env-file .env up -d qdrant`, then **Refresh**. |
| Assistant doesn't call a tool | Rephrase closer to the prompts above; with a local model, be explicit ("Yes, confirm it."). A hosted model follows more reliably. |
| *"cannot tell which version ... to retrain from"* when drafting a plan | Open the **Models** tab once - it syncs versions from the inference service - then try again. |
| Retraining queue is empty after queueing | Expected: queueing makes *tickets*. Draft the plan (Drift & Retraining tab, or chat) and the job appears. |
| Models tab "inference unreachable" | Start the inference service (port 8001). |
| Weird leftovers from rehearsal | Use **Clear Log** in the Work tab; start a **new chat** for each act you want clean. Old cases/tickets from rehearsal stay in the database (they are harmless). |
