# data/

Globally true, versioned reference data. **Nothing user-specific lives here** —
personal values are database rows written by onboarding.

| Path | What | Phase |
|---|---|---|
| `anthropometry/south_asian.yaml` | BMI / waist cutoffs for South Asian adults (not WHO defaults) | 0 |
| `pillars.json` | The six consistency pillars — **provisional names** | 0 |
| `nutrition/` | INDB + IFCT 2017 tables | 1 |
| `phase_templates/` | Reset / Rebuild / Sustain contract templates | 2 |
| `lab_reference_ranges/` | Indian lab panel reference ranges | 5 |

Every file states its source and a version. Code reads these files; code never
restates their numbers.
