# SentinelChat - User Guide

How to use SentinelChat: what each part of the app is for, **what to type or click to trigger each
behaviour**, and what you should see. For a ready-made walkthrough to present the app, see
[`DEMO.md`](DEMO.md). For how it is built, see [`ARCHITECTURE.md`](ARCHITECTURE.md) and
[`REVIEW_DRIFT_RETRAINING.md`](REVIEW_DRIFT_RETRAINING.md).

> **Verified.** The prompts and flows below were run against the real app (backend, inference
> service, Qdrant, Postgres) with the local `gemma4:12b` model on 2026-10-05. The assistant's
> *wording* varies from run to run; which tool it calls, the numbers on the cards and the
> confirm-before-saving behaviour do not.

---

## 1. The app at a glance

SentinelChat helps QA engineers inspect PCB (printed circuit board) defects found by an AOI
(automated optical inspection) machine, check the AI's calls, and feed corrections back into model
retraining. A mode toggle at the top switches between three areas:

| Area | What it is for | Typical user |
| --- | --- | --- |
| **Chat** | Ask the assistant in plain English. It inspects an image you attach, looks up Work-tab samples, records corrections and reviews, and reports model health. | QA, Admin |
| **Work** | Bulk workflow: upload a dataset (CSV + inspection XML + images), run it through the two-stage classifier, let a second agent (Agent 2) explain each flagged sample, then make the final call in the **Review Console**. | QA, Admin |
| **Models** | The model registry: live versions, drift reports, the retraining queue, and the Admin-only approve / promote / roll-back actions. | QA (read), Admin (act) |

### Roles

| Role | Can do |
| --- | --- |
| **QA** | Everything in Chat and Work; read-only view of Models. |
| **Admin** | Everything QA can do, plus the model-status overview in chat and **approve / cancel / promote / roll back** in the Models tab. |

The **first account ever registered becomes Admin automatically**. A tool the assistant says is
"not permitted for your role" is a role gate, not a bug.

### Two ideas that explain most behaviour

1. **Agents propose, you confirm.** Anything that *saves* something - creating a case, relabelling,
   approving a review - is **two steps**: the assistant first says what it *would* record (and saves
   nothing), then you reply "yes" **in your next message**. This is enforced in code, not just in the
   prompt: if the assistant tries to do both in one turn it is refused.
2. **A sample is not a case.** A **sample** is one row of a Work-tab dataset (`S000001`). A **case**
   (`CASE-000123`) is something *you* ask the assistant to create after inspecting an image in chat,
   so you can relabel or review it. Inspecting an image never creates a case by itself.

---

## 2. Chat: what to type, and what it triggers

You never pick a tool. You write normally and the assistant chooses. The table is the quick
reference; sections 3-8 explain each row.

| You want to... | Attach | Say something like | Tool(s) it calls | Saves anything? |
| --- | --- | --- | --- | --- |
| Inspect a component image | image (+ optional inspection XML) | "What defect does this component have? It is C754 on Board1." | `inspect_image` | **No** |
| Turn that inspection into a case | nothing | "Yes, please create a case for it." *(after the assistant asks)* | `create_case` | Yes - a case |
| Correct a wrong defect label | nothing | "That label is wrong - it's actually a wrong part, because the markings don't match." then "Yes, confirm it." | `relabel_case`, then `confirm_relabel` | Yes, after "yes" |
| Approve or override a flagged case | nothing | "I've reviewed this and the defect is real - approve it." then "Yes." | `review_case`, then `confirm_review` | Yes, after "yes" |
| Look up a Work-tab sample | nothing | "What do you know about sample S000001?" | `get_sample` | No |
| See what is waiting for review | nothing | "Which samples are still waiting for review?" | `list_review_cases` | No |
| See how the models did in a Work-tab run | nothing | "How did the models do in the last run, and what did the operator correct?" | `get_run_drift` | No |
| Check whether a model is drifting (saved cases) | nothing | "Is the pcb_text_defect model drifting?" | `get_drift_summary` | No |
| File a drift report | nothing | "Report that - lots of false Golden calls since Monday." | `report_model_drift` | Yes |
| Plan a retraining | nothing | "Draft a retraining plan for pcb_text_defect." (or the **Draft retraining plan** button in the Work tab) | `draft_retraining_plan` | Yes - a plan *awaiting Admin approval* |
| Model overview (Admin) | nothing | "What is the model status?" | `monitoring_status` | No |

**Model.** The assistant runs on **OpenAI**, through the server's key; there is nothing to choose
in the chat settings.

**If a prompt does nothing useful**
- *"Inspect this board"* with **no image attached** - the inspect tool is only offered when an image
  is attached, so the assistant will ask you to attach one.
- A **sample id** (`S000001`) is looked up with `get_sample`; a **case number** (`CASE-000123`) is
  used by relabel / review. Don't mix them up.
- Off-topic requests ("write me a poem") are politely declined - the assistant stays on PCB
  inspection and this app.

---

## 3. Inspecting an image

**Trigger:** attach an image (paperclip, or drag it onto the chat), then ask about it.

> *"What defect does this component have? It is C754 on Board1."*

Optionally attach the AOI machine's **inspection XML** too; its measurements are validated as part of
the same check. Mention the board, component and what looked wrong if you know them - none is required.

**What you get:** a result card and an explanation.
- the **verdict** - *Accepted*, or *Review required* with the reasons,
- the **region** the model found (Body / Lead / Text) and the **defect** it classified, each with
  its confidence, and the runner-up scores,
- the XML measurement check, if you attached one,
- a line saying **"Not saved as a case"**.

Two thresholds decide the verdict (both default to **0.70**): if the region model's confidence is
below the region threshold, no defect model is run and the result is *Review required*; if the
defect confidence is below the defect threshold it is *Review required* too.

> **With the bundled sample images** the region model is only 40-58% confident, so at the default
> thresholds you will see *Review required - region confidence 0.50 is below the 0.70 threshold, so
> no defect model was run*. That is a good way to show the review flow (section 5). To see a defect
> label (needed for relabelling), lower the thresholds - see [`DEMO.md`](DEMO.md#before-you-start).

**Then the assistant asks:** *"Would you like me to create a case for this result?"* A case is what
you relabel or review. Answer in your **next message**:

> *"Yes, please create a case for it."*  ->  *"Case CASE-008117 has been created."*

If you don't want a case, just carry on or say no - nothing was saved. If the inference service is
down or the file isn't a readable image you get an error and nothing is stored.

---

## 4. Correcting a wrong label (relabel)

Needs a case with a defect label (create one first, section 3). Tell the assistant what the label
should have been **and why**:

> *"That defect label is wrong - it's actually a wrong part, because the markings don't match."*

1. The assistant checks your label against the model's real classes and shows a **proposal card**
   (model label -> your label). **Nothing is saved yet.**
2. Reply **"Yes, confirm it."** Only then is the correction recorded on the case (the model's own
   label is kept alongside) and a **retraining ticket** queued.

Rules: the label must be one the model can output (the assistant lists valid ones if not;
spelling/spacing don't matter); a **reason is required**; with no case number it uses the latest case
in the conversation; a case whose region was too uncertain for a defect model has no defect label to
correct. A ticket does not retrain anything by itself - an Admin approves retraining in the Models tab.

---

## 5. Reviewing a flagged case (approve / override)

For a case marked *Review required*:

> *"I've reviewed this case and the defect is real - approve it."*  (or *"...it's a false positive - override it"*)

Again two steps: a **proposal card**, then **"Yes, confirm."** The case becomes **Approved** (the
flagged defect stands) or **Overridden** (a false positive - the signal drift watches). Only cases
awaiting review can be reviewed; an accepted or already-resolved case is refused with an explanation.

---

## 6. Looking up Work-tab samples

Everything the Work tab stores - the run, each sample's result, Agent 2's review and your final
decision - can be read from chat. These tools are **read-only**.

> *"What do you know about sample S000001?"*

Reports the board and component, the machine's call, Agent 1's verdict and confidences, Agent 2's
review and explanation, and the operator's decision if there is one. A sample id restarts every
run, so if it appears in several runs the assistant answers from the **latest** and mentions the
others; name a run to choose ("...in run 8b3bc7e5...").

> *"Which samples are still waiting for review?"* - lists the latest run's flagged samples with
> Agent 1's and Agent 2's calls and whether the operator has decided.

> *"How did the models do in the last run, and what did the operator correct?"* - per-model numbers
> (reviewed, **corrected**, Agent 2 disagreements, confidence) and the list of operator corrections,
> including which already have a retraining ticket.

An unknown sample id gets a helpful "not found" naming the latest stored run; with nothing run yet it
tells you to run a dataset in the Work tab first.

---

## 7. Model health and retraining (chat)

> *"Is the pcb_text_defect model drifting?"*

`get_drift_summary` compares the model's recent period with the one before it over **saved cases**
(from the relabel / review steps above): how many needed review, how often reviewers overrode or
**corrected** it, its confidence, by model version, with plain-language notes. (For a Work-tab
**run**, use the run question in section 6 instead - it measures the run's samples.)

If it looks wrong:

> *"Report that - lots of false Golden calls since Monday."* - files a drift report.
> *"Draft a retraining plan for pcb_text_defect."* - turns the **open retraining tickets** into a
> plan with status **pending approval**.

Tickets come from either place: a confirmed **relabel** in chat, or an operator's **queued
correction** sent from the Work tab's Retraining queue tab (section 8). **Approving a plan and promoting
a retrained model are Admin actions in the Models tab - never from chat.**

**Admin only:** *"What is the model status?"* - which version of each model is live, open drift
reports, open tickets and the retraining queue.

---

## 8. The Work tab and the Review Console

### 8.1 Running a dataset

1. **Dataset CSV** and **Inspection XML** - choose the two files. **Image Folder** (optional) - choose
   the folder holding the golden/defect crops; paths in the CSV are matched below the original
   `usi` root.
2. Set the two **confidence thresholds** (default 0.70 each). *Below the feature threshold a sample
   stops after the first (region) stage and never reaches a defect model* - with the bundled data you
   should lower it (e.g. **0.3**) so samples reach a defect model; see [`DEMO.md`](DEMO.md).
3. Optionally tick **Use real LLM Planner** (otherwise a deterministic planner is used).
4. Buttons: **1. Prepare**, **2. Prepare + Verify**, **3. Run Agentic Workflow** (the full run).

The **Workflow Summary** counts Input / Prepared / Verified / Inference / **Failed** / Accepted /
Review. **Failed** adds up samples that dropped out at preparation, verification or inference; it
turns red when above 0, the line under the status says at which stage ("76 failed verification"),
and the server's reasons are listed beneath it ("Verification GOLDEN_IMAGE_NOT_FOUND: 76 sample(s)
(e.g. S000001)"). A run that ends *Aborted* with Verified 0 almost always means the image paths in
the CSV did not resolve under the Image Folder.
The log below shows the planner's steps. When the run finishes, Agent 2 automatically reviews every
flagged sample (about 35 seconds for 11 samples) and the log prints each one:

```
[Agent 2 Diagnosis for S000001]
- Agent 1 Baseline : no defect
- Agent 2 Audit    : wrong part
- Full Explanation : ...
[Review Required] S000001: Machine/AI evidence differs (no defect vs wrong part). Open the Review Console...
```

The run and everything Agent 2 and you decide are **stored**, so the Review Console survives a
page reload or a backend restart.

### 8.2 Explanation Review - make the final call

Click **Open Review Console**. On the **Explanation Review** tab:

- the left list shows each flagged sample (item, machine call, AI call, status Pending / Reviewed),
  with a Pending / Reviewed filter;
- select one to see the **golden** and **defect** images side by side, Agent 1's and Agent 2's
  verdicts and Agent 2's explanation;
- under **User Final Decision** choose **Accept Machine** (Agent 1), **Accept AI** (Agent 2) or
  **Manual** (pick an IPC class), add notes, and click **Confirm Decision**.

Nothing is ever auto-approved - even when the two agents agree, the operator makes the call.

### 8.3 Model feedback and the Retraining queue - how the models did, and sending your labels for retraining

The Review Console has three tabs. **Review** is where you decide. **Model feedback** is a read-only
report: how the region model and the defect models did on this run, where flagged samples dropped
out, and how useful Agent 2 was. **Retraining queue** is the work list: the corrections you made,
grouped by the model they teach (the region model, and each defect model), each with its own send
button. A sample can appear twice: once for the part area you corrected and once for the defect label.

The tab opens with three steps and marks the one you are on: **1. Review flagged samples** (the
Explanation Review tab), **2. Send your corrections for retraining** (this tab), **3. An Admin
approves the plan** (the Models tab).

**Your final decision is the correct label.** Whatever you pick under *User Final Decision* - Accept
Machine, Accept AI or Manual - the saved final result is the sample's label. The tab compares it
with what the **defect model** predicted, and refreshes every time you save a decision (the tab
shows a badge such as "2 to send"). Only samples **flagged for review** are counted, so this is not
the model's overall accuracy. Each reviewed sample is one of:

| | What it means |
| --- | --- |
| **Model right** | the defect model predicted your label |
| **Model wrong** | the defect model predicted something else |
| **Couldn't decide** | the first-stage model wasn't confident enough, so no defect model ran |

**Error rate on reviewed samples** = of the samples the defect model classified (right + wrong), the
share it got wrong. It says how many flagged samples you have reviewed ("4 of 6 flagged samples
reviewed - this changes as you review"), so treat it as partial until you finish. *Couldn't decide*
samples do **not** count toward it - the model never answered - and show on their own; if the model
classified none of the reviewed samples the rate shows "-". The tab, top to bottom:

- **How accurate was the defect model on the flagged samples?** - the error rate ("67% - 2 of 3 the
  defect model classified were wrong"), one bar split into model right / model wrong / couldn't
  decide / not reviewed yet with a count for each, and a per-model table (reviewed, right, wrong,
  couldn't decide, error rate). Samples no defect model ran on sit in their own **No defect model
  ran** row. Computed on the server from the stored run, not from the browser.
- **Most common mistakes** - "model said *missing part* -> correct label *wrong part* x 2".
- **Send corrections for retraining** - every sample the model got wrong or couldn't decide, with the
  defect model it belongs to, what the model said, your label (and which decision you used, in the
  review tab's words), your notes and its status. A *couldn't decide* sample goes to the model the
  first guess points to (Body / Lead / Text -> the body / lead / text defect model), marked **(from
  first guess)**. **Nothing is ticked until you choose**: tick rows or use the select-all box, then
  **Send N corrections for retraining**. That creates the retraining tickets *and* puts them in a
  retraining plan for each model, ready for an Admin. Sending twice sends nothing twice; nothing is
  sent automatically. Sent rows show a tick instead of a checkbox.

Statuses: *Not sent yet* -> *In a retraining plan, awaiting Admin approval* -> *Retrained*.
*Sent, waiting for a retraining plan* means the plan step did not go through (usually the inference
service was down); a **Create retraining plan** prompt then appears below, counting only samples from
this run and noting any others it would also take. *Can't send: no defect model to route it to*: the
first guess has no defect model (rare). Confirmations and errors share one status line.

### 8.4 After you send corrections: the Admin approves the plan

The plan appears under **Models -> Retraining queue** as *pending approval*. An **Admin** uses
**Show flagged cases** to see what it contains (Work-tab samples show their run) and clicks
**Approve**, which sends it to the inference service (whose trainer is currently a stub that
simulates the job). QA can send corrections and create plans; only an Admin can approve one. In chat,
*"Draft a retraining plan for pcb_text_defect"* does the same plan step for any samples already in
the open tickets (including chat relabels).

---

## 9. The Models tab

- **Live models** - each model's live version, with **Roll back** (Admin) and, for other known
  versions, **Promote** (Admin).
- **Drift reports** - reports filed from chat or from the Work tab (a report filed for a Work-tab run
  carries a snapshot of the run's numbers); an Admin can **Mark resolved**.
- **Retraining queue** - jobs and their tickets. A job's *Show flagged cases* lists what it was
  drafted from (case numbers for chat tickets; sample id and run for Work-tab tickets). **Approve**
  and **Cancel** are Admin-only; QA sees the queue read-only.

The inference service holds no durable state: this app's database is the record, and it re-syncs
versions and job progress from the service when reachable.

---

## 10. End-to-end example

1. **Work tab:** run the dataset -> Agent 2 reviews every flagged sample.
2. **Review Console:** for `S000001` (Agent 1 said *no defect*, Agent 2 said *wrong part*) choose
   **Accept AI**, note *"text is wrong on the part"*, **Confirm Decision**.
3. **Retraining queue:** the correction appears (`Golden -> wrong part`); tick it and click
   **Send 1 correction for retraining**.
4. **Chat:** *"How did the models do in the last run?"* - the assistant reports the correction.
5. **Retraining queue:** sending already drafted the plan; if it could not (the leftover
   prompt appears), click **Create retraining plan** (or ask chat: *"Draft a retraining plan for
   pcb_text_defect."*) - a plan awaiting approval.
6. **Models (Admin):** the job is now in the **Retraining queue**; **Approve** it.

---

## 11. Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Inspect result is always *Review required*, no defect shown | Region confidence is below the 0.70 threshold. Lower `ADC_REGION_CONFIDENCE_THRESHOLD` (and restart the backend) - see [`DEMO.md`](DEMO.md#before-you-start). |
| Error rate shows "-" and every sample *couldn't decide* | No defect model ran (region model below the 0.7 threshold). Your labels can still be sent (by first guess); to measure the model, re-run with a lower **feature threshold** (e.g. 0.3). |
| Retraining queue shows "N unplanned" but no job | Samples are in the training data without a plan. Click **Create retraining plan** in the Corrections & Retraining tab, or ask chat for a plan. |
| "Run could not be read" in Model feedback or Retraining queue | Qdrant is unreachable. Start it (`docker compose ... up -d qdrant`) and click **Refresh**. |
| "inference service ..." errors | The inference service (port 8001) isn't running or `INFERENCE_BASE_URL` is blank. `start-dev` sets it. |
| Assistant says a tool isn't available | Your role doesn't allow it, or its kill switch is off. |
| "unknown run" / images missing in the Review Console after a redeploy | Uploaded images live on the backend's local disk; re-run the dataset. |
