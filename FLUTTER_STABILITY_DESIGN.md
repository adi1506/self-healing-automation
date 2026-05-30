# Flutter Replay Stability — Design

Status: draft, awaiting review.
Date: 2026-05-30.
Scope: replay path for Flutter web apps — locator derivation (`core/capture/inject.js`, `core/recorder.py`), fast-path resolution and healing (`core/replay.py`, `core/replay_healer.py`). Recorder/scorer changes only; no app-source changes.

---

## 1. Problem

Replaying recordings against Flutter web apps is both **slow** and **flaky**, and the two are separate failures with a shared root.

### 1.1 Confirmed threat model

The user identified the two dominant failures (others explicitly deprioritized):

1. **Flaky picks on an unchanged app** — replay grabs the wrong radio / tile / icon among elements that are all present. A disambiguation problem.
2. **Relabel / move across app builds** — a new build renamed a label or moved a field; the element must be re-identified. The core self-healing case.

Explicitly **not** in scope this iteration: environment drift (viewport/zoom/DPR), timing/flakiness, and app-source instrumentation (`Semantics(label:/button:)`, `flutter_driver`, `appium-flutter-driver`) — the apps are vendor-owned and cannot be changed.

### 1.2 Hard constraints

- **Replay-side only.** No changes to the Flutter app under test.
- **No regression on normal (non-Flutter) sites.** The existing `T1–T18` static-HTML battery must stay green.
- **A wrong fast match is worse than a clean heal miss.** Any new locator that resolves to the wrong element produces a confident wrong action, which is unrecoverable in an unattended run. Correctness gates speed.

### 1.3 Root causes (verified in code)

**Every Flutter field heals — structurally (the speed problem).**
`find_element_by_fingerprint` builds `[primary, *fallbacks]`, then `_is_flutter_ordinal_locator` strips every locator that references the render-order ordinal ([core/replay.py:273-284](core/replay.py#L273-L284), [core/replay.py:102-117](core/replay.py#L102-L117)). For a Flutter control:

- `id` = `flt-semantic-node-N` → stripped
- `css` = `flt-semantics#flt-semantic-node-N` → stripped
- `xpath` = `//*[@id='flt-semantic-node-N']` or `…/flt-semantics[3]/…` → stripped
- `name` = empty

`candidates` becomes empty, the polling `deadline` collapses to *now*, and control falls straight into `attempt_heal` — a full `scanAll()` (fingerprint every interactive element + every `flt-semantics`, serialize to Python, score each), plus a settle wait after navigation. This happens on **every Flutter field, every run.** The per-run heal cache ([core/replay.py:289-298](core/replay.py#L289-L298)) only helps when the same `fp.id` recurs; each distinct field still pays full heal cost once.

**Textless controls have no text identity (the flaky/relabel problem).**
Flutter splits a control and its label into adjacent `flt-semantics` siblings. A `role=radio` node carries empty `text_content`, and `nearestLabelText` ([core/capture/inject.js:110-121](core/capture/inject.js#L110-L121)) only walks `<label for>` / ancestor `<label>` — neither exists in Flutter. So the radio's fingerprint has `text_content=""`, `nearest_label_text=""`, `aria_label=""`, and the scorer collapses onto `tag_type + role + bbox`. Adjacent radios differ only by ~80px of bbox (flaky picks), and bbox is the only signal that survives a move (relabel/move failure).

---

## 2. Goals

One enabling primitive, two payoffs:

- **Goal A — Speed.** Give Flutter elements a durable, non-ordinal **primary locator** so the fast path resolves and heal does **not** fire on unchanged fields.
- **Goal B — Accuracy.** When a field genuinely changed and the fast path correctly misses, the same label association feeds the **scorer** so heal picks the right element instead of guessing on bbox.

Shared primitive: **anchor/sibling-text association** — for a textless `flt-semantics` control, find the nearby label-bearing node by a directional reading-order rule.

Non-goal (explicitly dropped at user request): vision / image-based fallback for truly-anonymous icon nodes. Those remain on the heal path.

---

## 3. Step 0 — Flutter regression battery (mandatory, first)

The existing `T1–T18` suite ([tests/dogfood/run_tests.py](tests/dogfood/run_tests.py)) runs entirely against static Netlify HTML fixtures. It covers the **normal-site no-regression axis** well, but has **zero Flutter coverage** and **never measures heal count** — the exact metric the speed work must move.

Build a Flutter battery before touching production code, or the speed claim is unmeasurable and correctness regressions go unseen.

### 3.1 Fixture

A static HTML fixture that mimics Flutter's `flt-semantics` overlay structure — sibling-split label/control, ordinal `flt-semantic-node-N` ids, viewport-spanning root node — modeled on the existing `tests/fixtures/ant_style_*.html` pattern so it runs in CI **without** the live HDB site. Variants:

- `flutter_v1.html` — baseline (radios with sibling labels, labeled inputs, a textless icon button).
- `flutter_v2_relabel.html` — labels rephrased, fields reordered (axis 2).
- `flutter_v1.html` replayed against itself — disambiguation under no change (axis 1).

### 3.2 Metrics the harness must report (new)

- **heal_count** per run — the headline speed metric. Today's behavior: ~= number of Flutter steps. Target after Goal A: near zero on the unchanged-replay run.
- **disambiguation accuracy** — on `v1`-vs-`v1`, did each radio/tile resolve to the correct element?
- **relabel/move heal rate** — on `v1`-vs-`v2`, what fraction of moved/renamed elements healed correctly (and zero wrong heals)?
- **normal-site regression** — `T1–T18` still green, heal counts unchanged.

---

## 4. Goal A — Durable non-ordinal primary locator (speed)

At record time, derive a primary locator that does **not** reference the ordinal, chosen by what the element actually carries. The resolution machinery already exists in the post-heal ladder (`get_by_role(name=)`, `get_by_text`, `[aria-label=…]`, `_find_flutter_by_visible_text` at [core/replay.py:762-835](core/replay.py#L762-L835)); this promotes those concepts to **first-class, record-time locator strategies** so they run on the fast path instead of only after a heal miss.

### 4.1 Locator tiers (record-time selection, in order)

1. **Own aria-label** — element has a non-empty `aria-label` (e.g. text inputs: `aria-label="Username *"`). Locator: `flt-semantics[aria-label="…"]`. Fastest, most stable.
2. **Own visible text** — element carries its own `text_content` (tiles/buttons: "Create Application"). Locator: a text strategy (`get_by_text` exact).
3. **Relative selector (textless-but-labeled controls — the radios)** — element has no own text/aria but a nearby label node does. Locator: a Playwright **relative/layout selector** anchored to that label, e.g. `flt-semantics[role=radio]:near(:text("Individual"))` or text-anchored XPath. This is the established "relative locators / friendly locators" mechanism (Selenium 4 `RelativeBy`; Playwright `:near()/:right-of()/:below()`), resolved natively by Playwright — no custom resolution code, and it self-heals positionally because the anchor moves with the control.

If none apply (truly-anonymous icon nodes: logo, chevrons, password-eye) → store no durable locator; the element falls to the heal path as today.

### 4.2 Record-time uniqueness guard (correctness gate)

A tier-1/2/3 locator is stored as primary **only if it resolves to exactly one element at record time.** If it matches zero or more than one, discard it and fall back to the next tier, then to heal. This is the guard that prevents a fast match from being a *wrong* match (the §1.2 correctness constraint).

### 4.3 New strategies in `_locator_for`

`_locator_for` ([core/replay.py:179-192](core/replay.py#L179-L192)) gains strategies that map to native Playwright resolution:

- `flutter-aria` → `page.locator('flt-semantics[aria-label="…"]')`
- `flutter-text` → `page.get_by_text(value, exact=True)` (or `flt-semantics`-scoped text scan)
- `flutter-relative` → a relative/layout selector string anchored to the label

`_is_flutter_ordinal_locator` is unchanged — ordinal locators stay stripped; these new strategies are what survive.

### 4.4 New strategies are NOT ordinal

By construction these locators carry no `flt-semantic-node-N`, so they survive the `_ordered_locators` strip and the fast path resolves them — heal is skipped on unchanged fields.

---

## 5. Goal B — Sibling-text association into the scorer (accuracy)

The same anchor/sibling-text association that builds the tier-3 locator also fills `nearest_label_text` in the fingerprint at **both** record time and heal-scan time (symmetrically). Effect: the scorer's second-highest feature (weight 0.20, [core/replay_healer.py:66-78](core/replay_healer.py#L66-L78)) becomes present for textless controls, normalization shifts weight off bbox, and a genuinely moved/renamed radio heals on its label instead of its position.

### 5.1 Association rule (shared with §4.1 tier 3)

For a textless `flt-semantics` control, find the label by a **directional reading-order rule**: the nearest label-bearing node in the same row / immediately adjacent in spatial + document order, within a tight radius. The rule is the whole ballgame — see §7.

This is implemented once in `inject.js` (the capture side), gated behind `flt-semantics-host`, and consumed by both the locator derivation (§4) and `buildFingerprint`'s `nearest_label_text`.

---

## 6. Regression safety (don't break normal sites)

The mechanisms that keep this Flutter-only by construction — preserve them:

- **Gating.** All new association/locator logic is gated behind `document.querySelector("flt-semantics-host")`, matching the existing Flutter paths ([core/capture/inject.js:286](core/capture/inject.js#L286), [core/capture/inject.js:609](core/capture/inject.js#L609)). Non-Flutter pages never enter it.
- **Additive-on-present-features.** Scorer changes only populate a feature that was empty before; they never lower a global threshold or reweight existing features. A well-labeled `<input name=…>` still wins on its high-weight features.
- **Invariant:** any new Flutter heuristic must stay gated behind `flt-semantics-host` **or** be additive-on-present-features — never lower a global threshold to rescue a Flutter case.

---

## 7. Risks and explicit non-decisions

- **Sibling mis-association is the primary risk.** Grab the wrong adjacent word → a confident wrong tier-3 locator → wrong action. Mitigations: the directional reading-order rule (§5.1) + the record-time uniqueness guard (§4.2). The Flutter battery must hammer this case specifically. [Certain — this is the make-or-break]
- **Flutter geometry gotcha.** Relative/layout selectors use bounding boxes. In Flutter the label text is sometimes in a *parent* semantics node whose box spans a whole region (observed: a node carrying 156 chars of concatenated nav text). The anchor must be the *specific small node* carrying just the label, or the layout math is garbage. [Likely]
- **Relative selectors are slower and historically quirkier** than direct locators — correct as tier 3, wrong as a default. [Likely]
- **Heal count drops substantially, not to zero.** Truly-anonymous nodes (logo `role=img` with empty text/aria — confirmed in the captured DOM dump; chevrons; password-eye) have no own identity and no nearby unique label, so they keep healing. [Likely]
- **Text/relative locators break when the text changes** — and that is correct: heal is exactly for the changed case. The win is that *unchanged* fields stop healing. [Certain]
- **Anchor uniqueness.** A relative selector whose anchor text isn't unique (two "Amount" labels) resolves ambiguously — caught by the §4.2 uniqueness guard, which drops it to heal. [Certain]

---

## 8. Implementation order

1. **Step 0** — Flutter fixtures + battery with heal_count / disambiguation / relabel metrics (§3). Establish baseline numbers on current code.
2. **Association primitive** — directional sibling-text association in `inject.js`, gated behind `flt-semantics-host` (§5.1). Unit tests against the fixture DOM. Shared dependency for both goals.
3. **Goal A (speed)** — record-time locator tiers (§4.1) + uniqueness guard (§4.2) + new `_locator_for` strategies (§4.3). Measure heal_count drop on unchanged replay.
4. **Goal B (accuracy)** — feed association into `buildFingerprint.nearest_label_text` (symmetric at record + scan). Measure disambiguation + relabel heal rate; confirm no normal-site regression.
5. **Re-baseline** the battery; confirm zero wrong heals introduced.

Order matches the approved plan: speed first (Step 3), accuracy second (Step 4). Once the association primitive (Step 2) exists, the two goals are independent, so this ordering is a preference, not a hard dependency.

---

## 9. Test plan

- **Unit (no browser):** association rule against fixture DOM snapshots — correct label for each radio; correct rejection when no unique nearby label exists.
- **Disambiguation:** `flutter_v1` vs `flutter_v1` — every radio/tile resolves to the correct element; assert heal_count near zero after Goal A.
- **Relabel/move:** `flutter_v1` vs `flutter_v2_relabel` — moved/renamed elements heal correctly; assert **zero** wrong heals.
- **Uniqueness guard:** a fixture with duplicate labels — assert the non-unique relative locator is discarded and the step falls to heal rather than mis-resolving.
- **Regression:** `T1–T18` green, heal counts unchanged.
