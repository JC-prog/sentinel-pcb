# SentinelChat - Chat Demo Script

A chat-only demo (about 10-12 minutes): what to set up, exactly what to type, what you should see,
and what to say. For the full end-to-end story (Work tab, Review Console, Models tab) use
[`DEMO.md`](DEMO.md); for every chat prompt and what it triggers see
[`USER_GUIDE.md`](USER_GUIDE.md#2-chat-what-to-type-and-what-it-triggers).

> **Numbers are from the [`DEMO.md`](DEMO.md) rehearsal** (2026-10-05, local `gemma4:12b`). Rehearse
> once on your own machine and treat them as "about". The assistant's *wording* changes every time;
> the tool it calls and the cards it shows do not. Each tool call shows its label (*Image
> inspection*, *Relabel proposal*, ...) in the chat - point at it.

---

## The story to tell

> *You talk to the assistant in plain English. Behind it is a supervisor agent that can only call
> the tools your role allows - inspect an image, correct a label, review a case, look up Work-tab
> results, check model health. **It never saves anything on its own**: every write is a proposal you
> confirm in your next message, and that rule is enforced in the backend, not the prompt.*

Three things to point out as you go:

1. **You never pick a tool.** The model picks from the tools it was given for this request - the
   inspect tool isn't even offered without an image attached, and Admin-only tools aren't offered
   to QA.
2. **Propose, then confirm - in a later turn.** Creating a case, relabelling and reviewing are all
   two steps. If the model tries to do both in one turn the backend refuses.
3. **The LLM explains; fixed code decides.** In an inspection the model orders the steps and writes
   the summary, but the verdict (Accepted / Review required) comes from plain rules over the
   classifier's confidences.

---

## Before you start

### 1. Start everything

```bash
bash infra/development/scripts/unix/start-dev.sh                    # macOS/Linux
powershell -File infra\development\scripts\windows\start-dev.ps1    # Windows
```

Needed: Postgres and Qdrant (Docker), the backend (`:8000`), the **inference service
(`:8001`)**, the UI (`http://localhost:4200`), and `OPENAI_API_KEY` set in `.env`.

Quick checks: `curl localhost:8000/health` and `curl localhost:8001/health` (should list four models).

### 2. Lower the confidence thresholds

At the default 0.70 the bundled images never get a confident region, so there is no defect label to
relabel. In `.env`:

```
ADC_REGION_CONFIDENCE_THRESHOLD=0.4
ADC_DEFECT_CONFIDENCE_THRESHOLD=0.3
```

then **restart the backend**.

### 3. Accounts

The **first account ever registered is Admin** - use it (Act 6 has an Admin-only prompt). Register a
second account afterwards if you want to show a QA user being refused it.

### 4. Demo files

| Use | File (under `data/workflow/Sample_data_2/Sample_data_2/`) |
| --- | --- |
| Image that is *Accepted* (Acts 2-3) | `14-756603-AAA-RV1/Body/SolderInsuffcient_5/Board1_C754_Body_14-756603-AAA-RV1_20260930_172812654_SolderInsuffcient_5.jpg` |
| Image that needs review (Act 4) | `32-462597-AAA-RV1/Gap/SolderInsuffcient_5/Board1_U58_Gap20_32-462597-AAA-RV1_20260930_172812755_SolderInsuffcient_5.jpg` |
| Optional inspection XML | `7017-0203-07T-PVT-JIG.2026-09-30.17.24.52.i.xml` |

### 5. Seed Work-tab data (only for Act 5)

The sample-lookup prompts read what the Work tab stored. Before the demo, run the dataset once in the
Work tab (DEMO.md [Act 4](DEMO.md#act-4---run-a-dataset-in-the-work-tab-3-min----work), feature
threshold **0.3**) and make a decision or two in the Review Console. Skip Act 5 if you don't.

### 6. Pre-flight

- [ ] Both services answer `/health`.
- [ ] Send "hello" in a new chat - the provider answers.
- [ ] Start each act in a **new chat** if you want it clean (the sidebar keeps the old ones).

---

## Act 1 - Hello, and what it won't do (1 min)

> **What can you help me with?**

*Expect:* a short list - inspecting component images, correcting labels, reviewing cases, looking up
Work-tab samples, model health. No tool is called.

> **Write me a poem about cats.**

*Expect:* a polite refusal - it stays on PCB inspection and this app. *Say:* "There's an input
guardrail in front of the model, and the prompt keeps it on topic."

---

## Act 2 - Inspect an image; a case only on request (3 min)

**2a.** Attach the *Accepted* image (paperclip or drag-and-drop) and type:

> **What defect does this component have? It is C754 on Board1.**

*Expect:* a result card (tool: *Image inspection*) with the region and defect, their confidences and
runner-up scores, the verdict, and **"Not saved as a case"** *(rehearsal: region Text 50%, defect
Golden 61%, Accepted)*. The assistant then asks whether you want a case.

*Say:* "The model chose the order of the checks and wrote that explanation - but the verdict is
fixed rules over the confidences, and nothing has been saved. It's a draft until I say yes."

*(Optional)* attach the inspection XML alongside the image to show the measurement check on the card.

**2b.** In a **new message**:

> **Yes, please create a case for it.**

*Expect:* "Case **CASE-0081xx** has been created." (tool: *Case creation*)

*Say:* "That could only happen in a later turn than the inspection. The model never supplies the
data for the case either - it comes from the stored draft."

---

## Act 3 - Correct the AI (2 min)

In the same chat:

> **That defect label is wrong - it is actually a wrong part, because the markings do not match the golden board.**

*Expect:* a **proposal card** (tool: *Relabel proposal*) - model label *Golden* -> *WrongPart* - and
"Would you like to confirm?". **Nothing is saved yet.**

> **Yes, confirm it.**

*Expect:* a **label corrected** card (tool: *Relabel*) with a **retraining ticket** queued.

*Say:* "The model's original answer is kept next to the correction. The ticket is how a human
correction becomes training signal - but it retrains nothing by itself; an Admin approves that."

**Fail-safe moment (optional):** ask for a label the model can't output, e.g. *"It's actually a
tombstone."* The assistant lists the valid labels for this model (`pcb_text_defect`: *Golden*,
*WrongPart*) instead of saving it.

---

## Act 4 - Review a flagged case (2 min)

Only a *Review required* case can be reviewed. In a **new chat**, attach the *needs review* image and
ask the same question as 2a. *Expect (rehearsal):* region `Lead` 0.40, just under the threshold, so
**Review required** with the reason on the card. Then:

> **Yes, please create a case for it.**

> **I have reviewed this case and the defect is real - approve it.**

*Expect:* a proposal card (tool: *Review proposal*).

> **Yes, confirm.**

*Expect:* *Case reviewed - approved* (tool: *Case review*).

*Say:* "Low confidence sends it to a person instead of guessing. 'Override' instead of 'approve'
marks it a false positive - that's one of the signals drift watches."

---

## Act 5 - Ask about the Work tab (2 min) *(needs step 5 above)*

> **What do you know about sample S000001?**

*Expect:* run id, board `Board1`, component `C754`, Agent 1's call and confidence, Agent 2's review
and explanation, and the operator's decision if one was made. (tool: *Sample lookup*)

> **Which samples are still waiting for review?**

*Expect:* the run's flagged samples with both agents' calls. (tool: *Review queue*)

> **How did the models do in the last run, and what did the operator correct?**

*Expect:* per-model numbers and the operator's corrections, with which are already queued for
retraining. (tool: *Run drift*)

> **What do you know about sample S999999?**

*Expect:* a "not found" that names the latest run - it doesn't invent data.

*Say:* "A sample is a row of a Work-tab dataset, not a case - looking it up never creates anything.
Chat reads the same store the Work tab writes."

---

## Act 6 - Model health and the line chat won't cross (2 min)

> **Is the pcb_text_defect model drifting?**

*Expect:* review / override / correction rates and confidence over saved cases, recent period vs the
one before, with plain-language notes. (tool: *Drift summary*) The relabel from Act 3 counts.

> **Draft a retraining plan for pcb_text_defect.**

*Expect:* a plan over the open tickets with status **pending approval**. (tool: *Retraining plan*)

> **Approve that plan.**

*Expect:* it declines and points you to the **Models** tab. *Say:* "Chat can draft; only an Admin in
the Models tab can approve a retrain or promote a model. There's no tool for it in chat at all."

**Admin only:**

> **What is the model status?**

*Expect:* live versions, open drift reports, tickets and the retraining queue. (tool: *Model
status*) Logged in as the QA account, the same question is refused as not permitted for the role.

---

## Act 7 - Memory (1 min, optional)

> **/remember I mostly inspect boards from the 14-756603 line.**

*Expect:* a confirmation that it was remembered. Open a **new chat** and ask:

> **Which board line do I usually work on?**

*Expect:* it recalls 14-756603. *Say:* "Conversation history is per chat; long-term facts are stored
per user and recalled across chats."

*(Needs memory enabled - `MEMORY_ENABLED` - and Qdrant up.)*

---

## Likely questions

| Question | Short answer |
| --- | --- |
| *How does it decide which tool to call?* | The model picks from the tools it's given for this request, filtered by role, kill switches and whether an image is attached. It can't call anything else. |
| *Can it save something without asking?* | No. Writes are propose -> confirm, and the confirm must come in a later turn from the same user - checked in the backend. |
| *Can the model fake the user or a file?* | No. The user, session and attached uploads are injected by the backend; the model never sees or supplies them. |
| *What if the LLM is down?* | The inspection still runs unassisted (fixed order, templated summary) - the verdict never depended on it. If the *inference service* is down, you get an error and nothing is stored. |
| *Local vs OpenAI?* | Same tools, same cards. OpenAI calls go straight to OpenAI with the server-side key; the browser never sees it. |

---

## If something goes wrong

| Symptom | Fix |
| --- | --- |
| Inspection says region uncertain, no defect model run | Thresholds at 0.70 - lower them in `.env` and **restart the backend** (Before you start, step 2). |
| "Inspect this" does nothing | No image attached - the inspect tool is only offered with one. |
| Relabel says there is no defect label | The case's region was too uncertain for a defect model. Use the *Accepted* image with lowered thresholds. |
| Review is refused | The case is *Accepted* or already resolved - only *Review required* cases can be reviewed (Act 4 image). |
| Sample lookup says nothing has been run | Run a dataset in the Work tab first (step 5). |
| *"cannot tell which version ... to retrain from"* | Open the **Models** tab once (it syncs versions from the inference service), then retry. |
| Assistant doesn't call a tool | Rephrase closer to the prompts above; be explicit ("Yes, confirm it."). A hosted model follows more reliably. |
