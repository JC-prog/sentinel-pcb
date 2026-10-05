# SentinelChat - User Guide

A practical guide to what you can ask the assistant to do, what you need to attach or type to
trigger each capability, and what comes back. For how the system is built, see
[`ARCHITECTURE.md`](ARCHITECTURE.md) / [`DEVELOPMENT.md`](../DEVELOPMENT.md) - this doc is about
using the chat, not building it.

Everything below happens in the same chat window - there's no separate mode or menu to switch
into. You just attach a file if one is needed and type what you want in plain English; the
assistant figures out which capability (if any) applies and calls it for you.

---

## 1. Roles: what you can do

| Role  | Can do |
| ----- | ------ |
| **QA**    | Everything below except the model-status overview. This is the role for day-to-day inspection work: inspecting images, correcting wrong labels, checking model health. |
| **Admin** | Everything QA can do, plus the model-status overview, registering golden reference images, and approving retraining in the Models tab. |

Your role is set when your account is registered (or by an Admin) - if the assistant tells you a
tool "is not permitted for your role," that's a role gate, not a bug.

---

## 2. Inspecting a PCB image

**Trigger:** attach an image to your message (paperclip button or drag-and-drop onto the chat
window), then say what you want to know - e.g.:

> *"What defect does this have?"* (image attached)

**Optional:** also attach an inspection XML file from the AOI machine, if you have one - the
assistant will validate its measurements as part of the same check.

**What you get back:** a result card above the assistant's reply showing:
- the **case number**, e.g. `CASE-000123` (every inspection is saved as a Case),
- the **verdict** - *Accepted*, or *Review required* with the reasons why,
- the **region** the model found (Body / Lead / Text) and the **defect** it classified, each with
  its confidence,
- **what else the model considered** - the runner-up labels and their scores,
- whether the XML's **measurements** passed validation, if you attached one.

The assistant then explains the result in words. The card is the exact figures straight from the
model; the words are the assistant's summary of them.

**Mention, if you know them:** the board identifier (e.g. `BOARD-1`), the component reference
(e.g. `U7`), and what looked wrong to you. None is required - you won't be blocked for lack of a
reference designator.

**Example:**

> *"Check U7 on BOARD-1 - I think the solder joint is insufficient."* (image attached)
>
> Assistant: *"Logged as CASE-000045. The region is Body and the defect is Solder Insufficient,
> but only 62% confident - below the threshold - so it needs review."*

If the inference service is down or the file isn't a readable image, you get an error instead of a
case - nothing is saved, and you can simply try again.

---

## 3. Correcting a wrong label

If the model's defect label on a case is wrong, tell the assistant what it should have been and why:

> *"That's wrong - it's actually a golden part, the shadow just looks like a missing component."*
> *"CASE-000045 should be Tombstone, not MissingPart."*

**It always takes two messages.** First the assistant checks your label and shows what it *would*
record - a card with the model's label crossed out and yours beside it - and asks you to confirm.
**Nothing is saved yet.** Reply *"yes"* and only then is the correction recorded.

- The label must be one the model can actually output. If it isn't, the assistant tells you the valid
  ones. Spelling and spacing don't matter (*"missing part"* works for `MissingPart`).
- **A reason is required** - a bare "this is wrong" is declined.
- Without a case number it uses the latest case in the conversation.
- A confirmed correction is recorded on the case (the model's original label is kept alongside), and
  a **retraining ticket** is queued for engineering. It doesn't retrain anything by itself: an Admin
  approves retraining in the Models tab.
- A case where the region was too uncertain for a defect model to run has no defect label to
  correct.

---

## 4. Checking model health

> *"Is the defect model drifting?"*
> *"Show me the drift summary for pcb_body_defect over the last 14 days."*

Compares the model's recent period with the one before it: how many cases needed review, how often
reviewers overrode it or **corrected its label**, how confident it has been, broken down by model
version - with plain-language notes on whatever moved.

If it looks like the model really has drifted:

> *"Report that - lots of false Tombstone calls since Monday."* (files a drift report)
> *"Draft a retraining plan."* (turns the open tickets from §3 into a plan awaiting approval)

Both only record a request. **Approving a plan, and promoting a retrained model, are Admin actions
in the Models tab - never from chat.**

**Admins** can also ask *"what's the model status?"* for the overview: which version of each model
is live, open drift reports, open tickets and the retraining queue.

---

## 5. Registering golden reference images (Admin only, not chat)

Golden (known-good) reference images aren't registered through chat - an Admin uploads them one at a
time via `POST /api/admin/golden-images` (board id, component ref, package, feature, and the image
file). The inspection no longer uses them for alignment checks, so this is currently reserved for
future use.

---

## 6. End-to-end example

1. **You:** *"Check U7 on BOARD-1 - looks off to me."* (image attached)
   **Assistant:** *[card: CASE-000045, Review required, Body 91%, MissingPart 58%]* "The defect
   classifier is only 58% sure it's a missing part, so it's flagged for review."
2. **You:** *"It's actually fine - a golden part. The shadow fools it."*
   **Assistant:** *[card: relabel proposed - MissingPart → Golden]* "I'd record Golden instead and
   queue it for retraining. Confirm?"
3. **You:** *"Yes."*
   **Assistant:** *[card: label corrected, queued for retraining]* "Done - CASE-000045 is corrected
   to Golden."
4. **Later:** *"Is the defect model drifting?"* - the correction now counts toward the model's
   correction rate, and once enough accumulate you can ask for a retraining plan.

---

## 7. Quick reference

| You want to...                                   | Attach                  | Say something like |
| ------------------------------------------------ | ----------------------- | ------------------ |
| Inspect an image and see the result              | image (+ optional XML)  | "what defect does this have" |
| Correct a wrong label                            | nothing (reason required) | "that's wrong, it should be Golden because ..." then "yes" |
| Check whether a model is drifting                | nothing                 | "is the defect model drifting" |
| Report drift / draft a retraining plan           | nothing                 | "report that" / "draft a retraining plan" |
| See the model overview (Admin)                   | nothing                 | "what's the model status" |
