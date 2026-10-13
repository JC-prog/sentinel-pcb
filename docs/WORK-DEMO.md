# Work Tab - Demo Script

A step-by-step script for presenting the **Work tab** (about 12 minutes): bulk-inspecting a dataset,
the human decision in the Review Console, how often the model was wrong, and sending your labels
to retraining. For the chat side see [`DEMO.md`](DEMO.md); for the full reference see
[`USER_GUIDE.md`](USER_GUIDE.md) and [`REVIEW_DRIFT_RETRAINING.md`](REVIEW_DRIFT_RETRAINING.md).

> **Rehearsed** against the real stack (backend, inference service, Qdrant, Postgres) on 2026-10-09,
> through the same API the UI calls. Numbers marked *(rehearsal)* come from that run - yours should be
> very close, but do one dry run on your own machine first and treat them as "about". Agent 2's
> wording changes from run to run; the counts and statuses do not.

---

## The story in one line

> *The machine flags defects, the model classifies them, a second agent explains them - and a person
> makes the final call. **That final call is the label.** Wherever the model didn't predict it, the
> model drifted - and those relabelled samples go back into its training data, with an Admin
> approving the retrain.*

```
Run the dataset ─► Agent 1 (the model) labels every sample, flags the uncertain ones
                   Agent 2 explains each flagged sample
Explanation Review ─► you make the final decision = the label        (Accept Machine / AI / Manual)
Model feedback ─► how often the models were wrong in this run; Retraining queue ─► send corrections
                   ─► Send the relabelled samples for retraining (+ a retraining plan)
Models tab ─► an Admin approves the plan
```

---

## Before you start

1. **Start everything** (repo root): `bash infra/development/scripts/unix/start-dev.sh` or
   `powershell -File infra\development\scripts\windows\start-dev.ps1`. If Docker was restarted, bring
   the services back first:
   `docker compose -f infra/development/docker-compose.yml --env-file .env up -d --wait db qdrant`.
2. **Check** `curl localhost:8000/health` and `curl localhost:8001/health` (the inference service must
   list four models). Open `http://localhost:4200`.
3. **Log in as an Admin** - the first account ever registered is Admin automatically. You need Admin
   for the approval at the end.
4. **Files** (all under `data/workflow/Sample_data_2/Sample_data_2/`):

   | Field | File |
   | --- | --- |
   | Dataset CSV | `dataset.csv` (11 samples) |
   | Inspection XML | `7017-0203-07T-PVT-JIG.2026-09-30.17.24.52.i.xml` |
   | Image Folder | the folder `Sample_data_2/Sample_data_2` itself |

5. **Dry run once**, then click **Clear Log** so the demo starts from an empty tab.

---

## Act 1 - Run the dataset (3 min)

1. Pick the three files above.
2. Set **Feature confidence threshold = 0.3** (leave the defect threshold at 0.7).
   *Why:* the bundled region model is only 40-58% confident on these images. At 0.7 every sample stops
   before reaching a defect model, so there is nothing to retrain (see the variant at the end).
3. Click **3. Run Agentic Workflow**.

*Expect (rehearsal, ~70 s):*
- the log streams the planner's steps (*observation -> constraint -> decision*: prepare, verify,
  inference, finalize);
- **Workflow Summary: Input 11 / Prepared 11 / Verified 11 / Inference 11 / Failed 0 / Accepted 0 / Review 11**;
- then `[Agent 2] Escalating 11 sample(s) for Explainability Review...` and a block per sample:

```
[Agent 2 Diagnosis for S000001]
- Agent 1 Baseline : no defect
- Agent 2 Audit    : wrong part
- Full Explanation : ...
[Review Required] S000001: Machine/AI evidence differs (no defect vs wrong part). ...
```

*Say:* "Agent 1 is the two-stage classifier - region first, then the matching defect model. Agent 2
re-examines every uncertain sample and explains its view. Nothing is accepted automatically: every
flagged sample waits for a person."

---

## Act 2 - The final decision is the label (3 min)  - Review Console -> Explanation Review

Click **Open Review Console**. The list shows each flagged sample with the *Machine* and *AI* calls and
Pending / Reviewed. Select one to show the golden and defect images side by side and Agent 2's
explanation.

Make three decisions:

| Sample | Decision | Notes | What it means |
| --- | --- | --- | --- |
| **S000001** (model: *no defect*, Agent 2: *wrong part*) | **Accept AI** | `text is wrong on the part` | relabelled to *wrong part* |
| **S000004** (model: *no defect*) | **Manual -> Tombstone** | `pad lifted` | relabelled to *tombstone* |
| **S000003** (Machine shows *wrong part*) | **Accept Machine** | - | agrees with the model |

Each shows "Saved S000xxx: ..." and turns *Reviewed*.

*Say:* "Whichever button I press, what I save **is** the label for that sample - I'm relabelling the
data. Where my label differs from what the model said, the model got it wrong."

---

## Act 3 - How often was the model wrong? (2 min)  - Review Console -> Model feedback and Retraining queue

Click the **Model feedback** tab for the numbers; the **Retraining queue** tab carries the badge **"2 to send"**. *Expect (rehearsal):*

- the **three steps** at the top, with step 2 marked;
- **Error rate on reviewed samples: 67%** - *2 of 3 the defect model classified were wrong*, and
  *3 of 11 flagged samples reviewed - this changes as you review*;
  the bar's legend: **Model right 1** (S000003) · **Model wrong 2** · **Couldn't decide 0** ·
  **Not reviewed yet 8**;
- per model: `pcb_text_defect` - reviewed 3 of 9, right 1, wrong 2, error rate **67%**;
- **Most common mistakes:** model said *no defect* -> correct label *wrong part* / *tombstone*.

*Say:* "My final decision is the correct label. Of the three samples the defect model classified, it
got two wrong - 67% in this run, on the samples I've reviewed so far. It's computed on the server from
what I saved and updates every time I save a decision." *(Optional: go back, change a decision,
return - the numbers move.)*

---

## Act 4 - Send your labels for retraining (1 min)

In the **Retraining queue**, the corrections are grouped by model and listed:

| Sample | Defect model | Model said | Your label | Status |
| --- | --- | --- | --- | --- |
| S000001 | pcb_text_defect | no defect | **wrong part** (Accept AI) | Not sent yet |
| S000004 | pcb_text_defect | no defect | **tombstone** (Manual) | Not sent yet |

Nothing is ticked yet. Tick **select all**, then click **Send 2 corrections for retraining**.

*Expect:* "Sent 2 corrections for retraining. The retraining plan for pcb_text_defect is waiting for
an Admin to approve it in the Models tab." Both rows now show a tick and read **In a retraining plan,
awaiting Admin approval**; the badge disappears and step 3 is marked. Sending again sends nothing
twice.

*Say:* "The samples the model got wrong, with my label, are exactly what it should learn from. One
click sends them and drafts the retraining plan - but nothing retrains until an Admin says so."

---

## Act 5 - An Admin approves (2 min)  - Models tab

Open **Models -> Retraining queue**: a job for **pcb_text_defect**, **2 samples**, status *pending
approval*. Click **Show flagged cases** - `S000001` and `S000004` with their run and model label ->
corrected label. Click **Approve** (Admin only).

*Expect (rehearsal):* status becomes *queued*, no error.

*Say:* "QA can add samples and create the plan; only an Admin can approve a retrain. The trainer in
this prototype is a stub that simulates the job, so this shows the control flow, not a real retrain."

---

## Variant - what if the model couldn't decide? (optional, 2 min)

Run the same dataset with the **default feature threshold 0.7**. *(Rehearsal: every one of the 11
samples stops at the region stage - the region model is only 40-58% confident - so no defect model
runs.)* Make a few final decisions (e.g. Accept Machine on S000001 and S000002, Accept AI on S000003
and S000004), then open Model feedback. *Expect (rehearsal):*

- **Error rate on reviewed samples: -** - *the defect model classified none of the reviewed samples*;
  **Couldn't decide 4**. It is not shown as 0% or 100%: the model never answered, so there is nothing
  to measure; the per-model table has a single **No defect model ran** row;
- the note explains: the first-stage model wasn't confident enough, so no defect model ran;
- every row reads *couldn't decide (first guess: Text area 50%)* (or *Body area 58%*), with the model
  it belongs to marked **(from first guess)** - `pcb_text_defect` for three, `pcb_body_defect` for one;
- tick **select all** and click **Send 4 corrections for retraining** -> two retraining plans, one per
  model, all four rows *In a retraining plan, awaiting Admin approval*.

*Say:* "When the model can't decide, that's not counted in the error rate - but my labels are still
valuable. They go to the defect model the first guess points to, the same routing Agent 1 would have
used."

---

## Likely questions

| Question | Short answer |
| --- | --- |
| *What exactly is "drift" here?* | For one run: of the reviewed samples the model classified, the share where it did not predict the operator's final label. Samples it couldn't decide on (no defect model ran) are counted separately, not as drift. Names are normalised (`WrongPart_13` = `wrong part`). |
| *Does pressing Accept Machine mean the model was wrong?* | Only if the saved label differs from the model's prediction. The label is what was saved, whichever button saved it. |
| *Why not add every reviewed sample to training?* | Where the model was already right there's nothing to correct; the samples it got wrong or couldn't decide are the new information. |
| *Where does a "couldn't decide" sample go?* | To the defect model the region model guessed (Body / Lead / Text -> body / lead / text defect model) - the same routing Agent 1 uses. The guess can be wrong; the label is the operator's. |
| *Can someone add the same sample twice?* | No - each run + sample can be added once; repeats are ignored. |
| *Who can retrain?* | Nobody from this tab. It drafts a plan; an Admin approves it in the Models tab. |
| *Where is this stored?* | Runs, Agent 2 reviews and decisions: Qdrant. Training-data tickets and retraining plans: Postgres. |
| *Is it the same data chat sees?* | Yes - in Chat, "How did the models do in the last run?" reads the same numbers. |

---

## If something goes wrong

| Symptom | Fix |
| --- | --- |
| Backend won't start: `Connect call failed ... 5433` | Docker services are down - run the `docker compose ... up -d --wait db qdrant` command above. |
| Every sample says *couldn't decide* and drift shows "—" | Feature threshold is 0.7, so no defect model ran - that's the Variant. For the main demo re-run with **0.3**. |
| Images not found / Verified 0 | Choose the folder `Sample_data_2/Sample_data_2` as the Image Folder. |
| Model feedback / Retraining queue: "the stored run could not be read" | Qdrant is down - start it and click **Refresh**. |
| "In training data - no plan yet" after adding | The plan step failed - usually the inference service is down (the plan asks it which model version to retrain from). Start it and click **Create retraining plan**. |
| Approve fails / "inference unreachable" | Start the inference service on port 8001. |
| Leftovers from the dry run | **Clear Log** in the Work tab starts a fresh view; old runs stay stored (harmless). |
