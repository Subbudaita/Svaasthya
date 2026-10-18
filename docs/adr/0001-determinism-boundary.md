# ADR 0001 — The determinism boundary: LLMs perceive and judge, code computes

- **Status:** Accepted
- **Date:** 2026-10-18
- **Phase:** 0 (Foundation) — recorded now because it shapes the schema

## Context

The product exists because of one complaint about a previous coach: *no
scientific reasoning, no proper explanations*. A recommendation is only worth
explaining if it can be traced back to something verifiable. When an LLM produces
a number, whether calories, a correlation, a target, or a threshold, nothing
verifiable stands behind it. The number can change between runs, nobody can
audit it, and it can be wrong without anyone noticing.

At the same time, some of the work genuinely needs an LLM: recognising "two
rotis, ~150 g rice, dal, curd" in a photo; reading a lab PDF; proposing a
physiological mechanism; writing a letter a person wants to read.

## Decision

Draw a hard line through the system:

| LLM side (perception, judgment, language) | Code side (numbers, rules, gates) |
|---|---|
| Identify plate items and estimate portions | Macros and micronutrients from INDB / IFCT tables |
| Extract analyte names and values from a lab report | Reference ranges, flags, re-test due dates from `data/` |
| Propose a causal hypothesis and a falsifiable prediction | Lagged Spearman, effect sizes, minimum-N gates |
| Personalise wording *within* the Program Contract | Generate the Contract (rule engine over templates + profile) |
| Classify an inbound message as a possible red flag | Escalation rules, output guard, contraindication gating |

Concretely:

1. **An LLM never emits a number that is stored or shown as fact.** It emits
   structured observations such as item names, portion estimates, and extracted
   values. Deterministic engines turn those into quantities.
2. **Engines live in `packages/engines/` and never import a model client.** Every
   engine is unit-testable without an LLM call (non-functional requirement:
   Testability).
3. **Global numbers live in `data/`, not in code.** Examples are anthropometric
   cutoffs, nutrition tables, lab reference ranges, and phase templates. Each file
   records its source and version. Phase 0 ships the first instance,
   `data/anthropometry/south_asian.yaml`, read by
   `swasthya_engines.anthropometry`. A test proves that changing the file changes
   the result.
4. **The schema separates the two sides.** For example, `meals.items` holds what
   perception saw and `meals.nutrients` holds what the tables computed.
   `hypotheses.statement`/`prediction` holds the LLM's proposal and
   `hypotheses.evidence` holds the statistics.
5. **Safety gates sit on the code side, after the LLM.** The output guard is
   deterministic and fails closed.

## Consequences

**Good**
- Every figure the user sees can be expanded to its source: a table row, a
  formula, or a data-file version. This is the "every number is tappable" UX
  in §7.
- Regression tests on engines are cheap and exact. LLM evals stay small and
  focus on perception quality.
- Swapping or upgrading a model cannot silently change a computed target.

**Costs**
- More plumbing: every perception output needs a schema and a mapping step
  (e.g. dish name → INDB id). Unmapped items must degrade gracefully ("I can't
  price this dish yet") rather than be guessed.
- Vision errors propagate into correct-looking arithmetic. Mitigation: the Plate
  Card's "fix this" button. Corrections become stored facts.
- Some tempting features, such as LLM-estimated calories for an unknown dish,
  are refused outright.

## What would make us revisit this

A perception task where the deterministic side has no table to compute from
*and* the user is better served by a clearly-labelled estimate than by "I don't
know". That would be a new ADR, scoped to that one task, never a blanket
relaxation.
