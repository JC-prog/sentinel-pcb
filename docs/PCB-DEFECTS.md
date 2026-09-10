# PCB Defects & Fabrication Notes

Background reference for the Sentinel-PCB project. Covers the six defect classes the
model detects, what causes each one in real manufacturing, and how the boards in our
dataset are actually made.

- **Dataset:** *PCB-Defect* — 230 annotated high-resolution images of single-layer
  (bare, no solder mask) FR4 boards, 1,704 annotations (~7.4 per image), scanned at
  1600 DPI (image sizes 800×600 up to 6000×4000 px).
- **Source:** Islamic University of Technology, Gazipur, Bangladesh. Mendeley Data
  DOI [10.17632/vdj74sngvn.1](https://doi.org/10.17632/vdj74sngvn.1).
- **Paper:** <https://pmc.ncbi.nlm.nih.gov/articles/PMC12756537/>
- **Class list:** missing hole (a.k.a. *missing pad* in this dataset), mouse bite,
  open circuit, short (short circuit), spur, spurious copper.

> Note: these six are the same classes used by the older Peking University HRIPCB /
> PKU-Market-PCB benchmark, so models and tooling transfer between the two.

---

## The two families of defect

| Family | Classes | Usual root cause |
|---|---|---|
| **Missing copper** (material lost) | missing hole, mouse bite, open circuit | Over-etching, resist pinholes/dust blocking exposure, handling damage |
| **Excess copper** (material left behind) | short, spur, spurious copper | Under-etching, contamination acting as an etch resist, phototool/toner spots |

---

## Missing hole
**What it is:** A drilled hole (via, through-hole, or mounting hole) that the design
calls for but is absent, plugged, or closed off. In the PCB-Defect dataset this class
is labelled *missing pad* — a pad/land that has been erased from the copper.

**Causes:**
- Drill bit breakage that goes undetected during CNC drilling
- NC drill-program errors — missing tool assignment, wrong drill file, drill data
  misregistered to the artwork
- Via closed by over-plating (copper or solder mask bridging a small hole)
- Resin smear or debris plugging the hole; incomplete hole formation
- CAM / aperture data errors
- (dataset) pad deliberately erased from the copper artwork before etching

## Mouse bite
**What it is:** Small semicircular notches or "nibbles" along the edge of a trace or
pad, giving a scalloped, bitten look. The conductor is locally narrowed.

**Causes:**
- Over-etching / etchant undercutting the conductor edge
- Pinholes or poor adhesion in the photoresist — dust, particles, air bubbles in
  dry-film lamination
- Poor resist development; scratches or specks on the phototool
- Rough copper surface or weak lamination
- Abrasion and handling damage

## Open circuit
**What it is:** A break or gap in a conductor that should be continuous, so the
electrical path is severed.

**Causes:**
- Over-etching that eats through a thin or necked section of trace
- Dust on the phototool (or a void in toner transfer) blocking resist, so the copper
  there is etched away completely
- Scratches, gouges, or drill hits that cut the trace during handling/processing
- Traces designed too narrow relative to process tolerance
- Poor plating adhesion leading to copper flaking or lifting; delamination
- Cracking from mechanical or thermal stress

## Short (short circuit)
**What it is:** Unwanted copper that bridges two conductors meant to be electrically
isolated.

**Causes:**
- Under-etching — exhausted etchant, insufficient etch time, low bath temperature —
  leaving copper between traces
- Opaque spots or particles on the phototool where copper should have been cleared
- Resist over-development, or extra resist from contamination, protecting unwanted
  copper
- Trace spacing designed too tight, or layer-to-layer misregistration
- Electroplating bridging between close features

## Spur
**What it is:** A thin unwanted spike or protrusion of copper sticking out from a
trace or pad into the clearance area, but *not* touching another conductor (if it
touched, it would be a short). Essentially the inverse of a mouse bite.

**Causes:**
- Under-etching / incomplete etching
- Contamination, debris, or scratches with lodged particles on the phototool or dry
  film
- Resist residue or slivers left by poor development
- Ragged film edges or defects in the artwork / toner transfer

## Spurious copper
**What it is:** Isolated islands, blobs, or patches of leftover copper in regions that
should be entirely clear of copper — not part of any intended conductor.

**Causes:**
- Under-etching (weak or depleted etchant, short etch time)
- Surface contamination — fingerprints, oils, dust — acting as an unintended etch
  resist
- Undeveloped photoresist / dry-film residue not fully rinsed off
- Particles or spots on the phototool blocking UV exposure
- Inadequate panel cleaning before lamination or etching

---

## Common root causes at a glance

| Mechanism | Tends to produce |
|---|---|
| Over-etching, etchant undercut | mouse bite, open circuit, missing hole |
| Under-etching, depleted bath | short, spur, spurious copper |
| Dust / particles blocking resist exposure | open circuit, mouse bite |
| Contamination acting as resist | spurious copper, short, spur |
| Phototool spots / toner-transfer voids | spur, short, spurious copper, open circuit |
| Drilling faults / program errors | missing hole |
| Handling: scratches, abrasion, stress | open circuit, mouse bite |

---

## Is a PCB made "one by one"?

**No — the copper pattern is formed all at once, not feature by feature.** A PCB is a
*batch / parallel* process. The entire copper layer of a board (every trace, pad, and
clearance) is patterned in a single etch step: wherever copper is left unprotected by
resist, it dissolves simultaneously. You do not "draw" traces one at a time.

What *is* done sequentially:

- **Drilling** — holes are made one at a time by a CNC spindle (or a laser, per hole).
- **Panels** — a fab runs many boards on one large panel, then routes/scores them
  apart at the end.
- **Layers** — a multilayer board images and etches each copper layer separately, then
  laminates them together (our dataset is single-layer, so this doesn't apply).

**For this dataset specifically:** the six defects were added *one by one, by hand* in
the vector artwork (Inkscape) — discontinuities for opens, bridges for shorts,
erasures for missing pads, notches for mouse bites, extensions for spurs, extra
shapes for spurious copper. But once the artwork is printed and transferred, the
board is etched in **one pass**, so all defects on a board are "realized" at the same
moment. They are real etched copper features, not drawn-on or Photoshopped.

---

## Step by step: how the dataset boards are made

This is the **toner-transfer + ferric-chloride** process described in the paper (a
low-cost method that produces genuine etching artifacts), not an industrial
photolithography line.

### A. Design

1. **Draw the circuit** in ECAD software with fixed design rules: 20 mil minimum
   track width and clearance, 70 mil via diameter, etc.
2. **Export the copper layer to SVG** to keep true vector geometry and real-world
   dimensions.
3. **Inject defects** in Inkscape — manually edit copper features to add the six
   fault types (opens, shorts, missing pads, mouse bites, spurs, spurious copper).
   Keep a clean "golden" version for reference/annotation.
4. **Print the layout** onto A4 photo paper at 600 DPI with a laser printer (toner =
   the etch mask).

### B. Board preparation

5. **Cut** a single-layer FR4 copper-clad sheet to size.
6. **Abrade** the copper (fine abrasive) to remove oxide and give the toner a key.
7. **Clean** with isopropyl alcohol to remove oils, dust, and fingerprints — skipping
   this is itself a cause of spurious copper.

### C. Pattern transfer

8. **Lay the printout face-down** on the clean copper.
9. **Heat-press / laminate** — roughly 25 passes through a heated laminator (with a
   cold press step) so the toner melts and bonds to the copper as an etch-resist mask.
10. **Soak and peel** the paper away in water, leaving toner on the copper. Touch up
    any pinholes if doing a clean board (defect boards are left as-is).

### D. Etching

11. **Submerge in ferric chloride (FeCl₃)** etchant. Unmasked copper dissolves;
    toner-covered copper survives. Agitate; warm etchant works faster.
12. **Rinse** in water as soon as the exposed copper is gone, to stop over-etching.

### E. Finishing

13. **Strip the toner** with acetone/alcohol, exposing the bare copper pattern.
14. **Clean off** residual ink and etchant chemicals; dry.
15. **Drill** any through-holes / vias (CNC or drill press), one hole at a time.
16. **Protective coat** — a clear epoxy resin over the copper (this dataset has **no
    green solder mask**, which is why traces appear bare in the images).

### F. Dataset capture

17. **Scan** each finished board on an HP ScanJet Pro 3600 f1 flatbed at 1600 DPI.
18. **Annotate** defects in Roboflow (one dedicated annotator), then **validate** with
    two independent expert reviewers.
19. **Publish** — 230 images, 1,704 boxes, released on Mendeley Data.

### How an industrial fab differs

The pipeline is the same idea but with tighter process control: photoresist +
UV-exposed phototool (or laser direct imaging) instead of toner transfer; cupric
chloride or ammoniacal etchant on a conveyorized line; electroless + electrolytic
copper plating for plated through-holes; solder mask and surface finish (ENIG, HASL);
automated optical inspection (AOI) and electrical (flying-probe / bed-of-nails)
testing. Sentinel-PCB's model plays the role of that AOI step.
