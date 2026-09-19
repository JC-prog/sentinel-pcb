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
| **QA**    | Everything below except viewing model/infra monitoring status. This is the role for day-to-day inspection work: submitting images, investigating cases, reviewing, flagging bad calls. |
| **Admin** | Everything QA can do, plus registering golden reference images and viewing monitoring status. |

Your role is set when your account is registered (or by an Admin) - if the assistant tells you a
tool "is not permitted for your role," that's a role gate, not a bug.

---

## 2. Everyday chat

Ask anything - general conversation, "what time is it in Singapore," "what's the weather in
Austin" - and the assistant answers directly, or calls a small built-in tool (`current_time`,
`get_weather`) if that's genuinely what's being asked. No attachment needed.

---

## 3. Submitting a PCB image for inspection

**Trigger:** attach an image to your message (paperclip button or drag-and-drop onto the chat
window), then describe what you're flagging - e.g.:

> *"Check this component, the solder looks thin."* (image attached)

**Optional:** also attach an inspection XML file from the AOI machine, if you have one - the
assistant will validate its measurements as part of the same check.

**What happens:** the assistant runs the full inspection workflow on your image:
1. Verifies the image is readable.
2. Looks up a matching golden (known-good) reference image on file, if one is registered, and
   checks your image aligns with it.
3. Classifies the component region (Body/Lead/Text) and the specific defect.
4. Validates the attached inspection XML's measurements, if you attached one.
5. Decides **ACCEPTED** or **REVIEW_REQUIRED**.
6. If **REVIEW_REQUIRED**, automatically runs a deeper diagnosis (see §4) and attaches it - you
   don't need to ask for this separately.
7. Saves everything as a **Case** with a case number, e.g. `CASE-000123`, whichever verdict it was.

**What you need to mention (in your own words - the assistant will ask if something's missing):**
- The board identifier (e.g. `BOARD-1`)
- The component reference designator (e.g. `U7`, `R131`, `C978`)
- Optionally: package type, the specific feature/pad, and what looked wrong to you

**Example:**

> *"Flag component U7 on BOARD-1, package QFN32 - I think the solder joint is insufficient."*
> (image attached)
>
> Assistant: *"This has been logged as CASE-000045. The model classified it as Body / Solder
> Insufficient with 62% confidence, which is below the confidence threshold, so it's flagged
> REVIEW_REQUIRED. I ran a deeper diagnosis: [...]"*

---

## 4. Getting a deep-dive diagnosis

Two ways to trigger the same underlying diagnosis (visual evidence + historical precedents +
IPC-A-610 standards + AOI/ICT telemetry, reasoned into a root-cause explanation):

**a) From a fresh image** - attach an image and ask for an explanation directly:

> *"Why does this look like a defect?"* (image attached, mention board id + component ref)

**b) From an existing case, by case number** - no image needed, just reference the case:

> *"Investigate CASE-000045."*
> *"Why did the model flag CASE-000123?"*

The assistant resolves the case's stored image (and inspection XML, if one was attached when the
case was created) automatically - you never need to re-upload anything for a case that already
exists.

**Follow-up questions work too**, once a diagnosis has been run in the conversation:

> *"Which past case is this most similar to?"*
> *"What does IPC-A-610 say about this?"*

---

## 5. Listing and reviewing cases

**List cases:**

> *"Show me all cases pending review."*
> *"List the last 10 cases."*

**Approve or override a REVIEW_REQUIRED case** (only cases awaiting review can be resolved this
way):

> *"Approve CASE-000045."*
> *"Override CASE-000045 - false positive, the solder actually looks fine on closer inspection."*

`approve` confirms the flagged defect stands; `override` reverses it as a false positive. Add a
note explaining your decision - it's optional but recommended, and gets saved with the case.

---

## 6. Flagging a bad model call for retraining

If you believe the model's verdict on a case was wrong (not just a one-off false positive you're
overriding, but a sign the model itself needs retraining), flag it:

> *"Flag CASE-000045 for retraining - the model keeps confusing this component's normal shadow
> for foreign material."*

**You must give a reason** - a bare "this is wrong" without an explanation will be rejected. This
only queues a ticket for engineering; it doesn't retrain anything automatically (retraining
itself happens separately, on the inference server).

---

## 7. Monitoring status (Admin only)

> *"What's the model's current status?"*

Currently a placeholder - it will tell you it's not implemented yet. Reserved for future
model/dataset performance and drift-detection reporting.

---

## 8. Registering golden reference images (Admin only, not chat)

Golden (known-good) reference images used for alignment checks (§3, step 2) aren't registered
through chat - an Admin uploads them one at a time via `POST /api/admin/golden-images` (board id,
component ref, package, feature, and the image file). Ask whoever manages your deployment if you
need one added.

---

## 9. End-to-end example

A full round trip, combining §3 and §4-6:

1. **You:** *"Check U7 on BOARD-1, package QFN32 - looks off to me."* (image attached)
   **Assistant:** *"Logged as CASE-000045, REVIEW_REQUIRED (Body confidence 0.55). Deeper
   diagnosis: likely solder insufficient - laser height measurement is well below nominal,
   consistent with a thin joint. IPC-A-610 General Workmanship Criteria: solder joint must
   exhibit positive wetting and no bridging."*
2. **You (later, or someone else on your team):** *"Investigate CASE-000045 again, and tell me
   which past case is most similar."*
   **Assistant:** *"[...] Most similar historical case: 91% match, also classified Solder
   Insufficient."*
3. **You:** *"Approve CASE-000045 - confirmed defect."*
   **Assistant:** *"CASE-000045 is now APPROVED."*

   ...or, if the verdict looked wrong on review instead:

3. **You:** *"Override CASE-000045 - false positive, re-inspected under better lighting."*
   **Assistant:** *"CASE-000045 is now OVERRIDDEN."*
4. **You:** *"Actually, flag CASE-000045 for retraining - the model seems to be misreading this
   component's normal solder profile as a defect."*
   **Assistant:** *"Retraining ticket created for CASE-000045."*

---

## 10. Quick reference

| You want to...                                  | Attach                  | Say something like |
| ------------------------------------------------ | ------------------------ | ------------------- |
| Submit an image for inspection                    | image (+ optional XML)   | "check this board, component U7" |
| Get a deep-dive diagnosis on a fresh image         | image                    | "why does this look like a defect" |
| Get a deep-dive diagnosis on an existing case       | nothing                  | "investigate CASE-000123" |
| Find similar past cases                            | nothing                  | "which case is similar to this one" |
| List cases                                         | nothing                  | "show me cases pending review" |
| Approve/override a reviewed case                   | nothing                  | "approve CASE-000123" / "override CASE-000123, ..." |
| Flag a bad model call                              | nothing (reason required) | "flag CASE-000123 for retraining, reason: ..." |
| Check monitoring status (Admin)                    | nothing                  | "what's the model status" |
