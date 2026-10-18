# Swasthya — Plan

> Source of truth for requirements and architecture. Committed verbatim from the
> planning document supplied at project start; edit via PR, not ad hoc.

## 1. Context

**What we're building:** an end-to-end agentic AI system that acts as a personal health coach — condition-aware, multimodal (plate photos, lab PDFs, voice notes), proactively checking in twice a day, and able to explain *why* progress is or isn't happening.

**Why:** Subbu evaluated Dr. Pal's NewME program (₹99,999 / 24 weeks) and chose not to enrol. The program structure is clinically sound; the expensive, human-delivered parts of it — daily plate review, six-pillar accountability, weekly "here's what's actually happening in your body" — are exactly what an agent does better and at ~1/1000th the marginal cost. The NewME material is therefore the methodology blueprint, not a program to comply with.

**The north star** is Subbu's own critique of Fitello: *"no scientific reasoning, no proper explanations, no individualized guidance — the weekly check-in just published next week's dates without ever addressing why my weight management was failing."*

Every feature is validated against one question: **would this have kept Subbu on track where Fitello didn't?** Logging and streaks wouldn't have. It has to reason.

**User #1 and design persona** (Sept 17 consult transcript): 89–92 kg flat for 3–4 years · HbA1c just under pre-diabetes · past peptic ulcer + H. pylori · past Vit-D and B12 deficiency, never re-tested · gas with outside/spicy food · walks 3–4 km/day + the 10-min post-meal walk · 6–7 months of IF that collapsed on three specific failure modes: **energy dips, unplanned breakfasts, unstructured dinners.**

Those three failure modes are the acceptance test.

> **Implementation note:** the persona above is a *design* persona. None of these
> values may appear in code — they live in DB rows written by onboarding.

### Locked decisions

| Decision | Choice |
|---|---|
| Interface | PWA (primary) + Telegram bot (ambient capture) |
| Voice | Voice notes both directions. No telephony. |
| Clinical depth | Condition-aware, referral-first. Never diagnoses, never touches medication. |
| Product scope | Portfolio-grade, signup-ready. Multi-tenant + auth, no billing, invite-gated. |
| v1 inputs | Manual + plate photo + lab report upload. No wearables. |
| Locale | India-first: Indian foods, metric, IST, Indian lab panels. English. |
| Hosting | Cloud, multi-tenant, Postgres row-level security. |

## 2. User pain points → what addresses them

Drawn from the consult transcript and the research. This is the requirements grounding.

| # | Pain | Addressed by |
|---|---|---|
| P1 | "I don't know why it isn't working." No causal explanation, ever | Causal engine + weekly Insight Letter |
| P2 | Decision fatigue at breakfast — the IF killer | 07:00 brief pre-decides breakfast |
| P3 | Unstructured dinner → late eating → poor sleep → next-day cravings | Circadian agent treats window + sleep as one system |
| P4 | Energy dips derail fasting | JITAI nudge at the personal risk hour |
| P5 | Logging fatigue at weeks 6–10 | Five logging methods; scheduled cliff intervention |
| P6 | Travel, festivals, eating out | Sustain phase is built around exactly this |
| P7 | Generic Western advice that ignores Indian food | IFCT tables, Indian portion logic, India-first vision prompts |
| P8 | Gut triggers (spicy/outside food) never systematically tracked | Nutrition & Gut agent logs trigger→symptom pairs |
| P9 | Deficiencies never re-tested — loop never closed | Clinical agent tracks re-test due dates and nags |
| P10 | Plateau demoralization — scale won't move | Graded on leading indicators, not weight |
| P11 | Nobody to ask at 22:00 when you're about to break | It's awake |
| P12 | ₹1 lakh for human coaching | ~₹1,500–2,400/month all-in |

## 3. Evidence base — what actually works

Researched before locking the design. These findings changed it.

| Finding | Design consequence |
|---|---|
| Consistent food tracking falls 68% → 21% in the first 12 weeks; multi-method logging materially outperforms single-method; photo-only produced weaker habit formation than database apps (2021) | Five logging methods from day one. Photo-only would have failed. |
| Extrinsic rewards damage motivation only when perceived as controlling; unexpected rewards and competence-confirming feedback strengthen it (SDT) | Rewards are retroactive recognition, never a pre-announced carrot. User authors their own. |
| Duolingo: next-day retention 12% → 55%. Freeze users hold streaks 4.5× longer by day 21. Two freezes beat one; three ≈ two. Applied silently, revealed retroactively | Two grace days/month, bounded, silent, retroactive. Six independent pillar tracks — never one global streak that zeroes. |
| Implementation intentions: d ≈ 0.5–0.7; Fogg: behaviour = motivation × ability × prompt, anchored to an existing routine | If-then plans are a first-class DB object, max 2 active. |
| JITAI literature: 62 documented interventions; efficacy depends on firing at genuinely opportune moments | Nudge engine is a formal JITAI. Rate-limited to ~2/week. |
| Zoe/PREDICT: individuals respond differently to identical foods | Validates the causal engine as the core, not a nice-to-have |
| Noom acknowledges the user's desire to quit in its communications | Swasthya names the wobble instead of cheerleading past it |
| Omada: 1.1 point A1C reduction, 85% hit goal; driver was lowering barriers to access | Friction reduction beats feature count |
| Consumer health apps: D30 retention ~3% (D1 20–27%, D7 ~7%); best-in-class 40%+. But NHS DDPP: 83% programme completion, −6.2 kg and HbA1c −2.2 mmol/mol at 12 months — driven by coach + peer group | A 25× gap between "app" and "programme," caused by human contact. Drives F16. Also: Strava's time-boxed Challenges moved 90-day retention 18% → 32% — pairs with if-then plans |
| Food4Me varied personalization depth at matched intensity → no difference. Zoe METHOD (Nature Med 2024, n=347) showed −2.17 kg vs +0.30 kg — but its control arm got a leaflet, confounding personalization with intensity | **Intensity and contact beat personalization depth.** Spend everything on daily contact and reasoning quality; nothing on biology we can't act on. This is the core strategic finding — and an agent is nearly free at daily contact |
| Nutrigenomics: Food4Me + DIETFITS found genotype did not predict diet response; DTC panels largely untested in RCTs. Microbiome: 2024 international consensus says insufficient evidence for routine use, F/B ratio should be excluded from reports; one sample to six providers → six inconsistent reports | **Do not build genetic or microbiome interpretation.** Detail is not validity — shipping it would be the most impressive-looking and least defensible thing in the product, contradicting our own north star |
| GLP-1 era: protein 1.2–1.6 g/kg/day evenly distributed + resistance training 2–3×/week → only 3% muscle loss while losing ~13% body weight. After stopping, 9.9 kg average regain in year one | Muscle preservation, not weight, is the central problem. Independently validates grading on leading indicators and targeting fat down / muscle up |
| South Asian thresholds differ: overweight BMI ≥23, obese ≥25 (not 25/30); waist ≥90 cm men. More visceral and ectopic (liver, muscle) fat at any BMI | Ship WHO defaults and the app tells an at-risk Indian user they're fine. Asian cutoffs go in the India-first data files |

**The honest gap — relatedness.** SDT names three needs: autonomy, competence, relatedness. Swasthya serves the first two well. Noom and Second Nature both depend on peer groups and human coaches for the third; a solo agent cannot replicate it and an LLM persona is not a substitute. Minimal honest answer: one-tap "share this week's letter" with a single real human who agreed to read it. Flagged as unsolved, not shipped.

## 4. Requirements

### Functional

**F1 · Onboarding** (~25 min, conversational) — history and meds; contraindication screen; baseline weight + lab upload; user names their own goal (Swasthya only pressure-tests reachability); user authors their own non-food rewards; outputs a Program Contract they can argue with. Days 1–3 are observation only, nothing scored.

**F2 · Multi-method logging** — plate photo, voice note, quick-pick from own history, free text, "same as yesterday". Any one of them must be enough on a given day. Repeat meal loggable in under 5 seconds.

**F3 · Plate analysis** — vision identifies items and portions for Indian meals; IFCT lookup computes macros. Returns a balanced-plate read and one actionable sentence, not a macro table. User can correct; corrections become training signal and stored facts.

**F4 · Daily rituals** — 07:00 brief (targets, pre-decided breakfast, context from yesterday); 21:30 check-in (six-pillar scorecard + exactly one causal question, whose answer is stored as a semantic fact).

**F5 · Adaptive nudges (JITAI)** — fire only when decision rules predict a lapse. Hard cap ~2/week.

**F6 · Program Contract** — versioned JSON: phase, per-pillar targets, condition adaptations, prohibitions. Generated by a deterministic rule engine. Every recommendation cites its clause.

**F7 · Phase progression** — Reset (W1–4, no numbers shown) → Rebuild (W5–12, numbers arrive) → Sustain (W13–24, Swasthya goes quiet). Transitions are gated on adherence, not the calendar alone.

**F8 · Causal engine + Insight Letter** — weekly. Lagged correlations computed in Python; Opus 5 proposes falsifiable hypotheses; lifecycle proposed → supported → confirmed → refuted. Letter ends with what changes next week and what to ask a doctor.

**F9 · Labs** — upload PDF/photo → structured analytes with Indian reference ranges → trends over time → re-test due dates → doctor questions.

**F10 · Behaviour engine** — six consistency tracks, two grace days/month, reward ledger, if-then plan store.

**F11 · Voice** — voice note in (STT), spoken reply out (TTS), both channels.

**F12 · Progress** — graded on leading indicators weekly (window closed, protein, fibre, steps, sleep); lagging reviewed monthly (weight, waist, HbA1c, lipids, TSH, D, B12, energy, gut symptoms).

**F13 · Safety** — contraindication gating; inbound red-flag classifier; output guard; escalation UI; audit log.

**F14 · Anytime chat** — ask anything, any channel, text or voice, about today or any past day. Requires time-scoped retrieval over the episodic store from natural-language time expressions, awareness of current time / phase / today-so-far, and cross-channel continuity (start on Telegram at lunch, continue in the PWA at night — one thread, state in LangGraph checkpoints, never in the channel). The highest-frequency question will be "what should I eat now?", which needs a single query across four stores.

**F15 · Grocery engine** — fires from week 2, never at onboarding. Built from the actual 14-day log: the smallest set of purchases that closes the observed recurring gaps. 6–10 items with a one-line reason each, plus a "don't buy" list (per the Hall UPF finding, the more effective half). Timed to how the user actually shops. Closes its own loop: next week it checks whether purchased items reached a plate — if you bought paneer and never ate it, the constraint is cooking effort, not availability, and that's a causal finding.

**F16 · Accountability human** — promoted to a first-class onboarding step, not a setting. User names one real person who agrees to receive the weekly Letter. This is our only answer to the relatedness gap, and the NHS data says it's the highest-value thing we're missing.

### Cross-cutting principle: derive parameters, never ask for them

Never ask the user for a parameter. Ask about their life, derive it, propose it with reasoning, invite pushback. Asking "what eating window do you want?" requires the user to already know the answer — and the answer you get is aspirational, not sustainable.

Applies to every pillar. For the eating window, onboarding asks about wake/sleep times, yesterday's first and last bite, who else eats with you and when, work pattern, hunger and crash timing, medication-with-food, training time, and existing religious fasting days — then derives:

- **Anchor the close first** (sleep lever), target last bite ≥3h before sleep; open = close − current duration
- **Start from observed baseline, never a target.** 13h40 observed → propose 12h, not 16:8
- **Respect the binding constraint** (family dinner time) and surface the conflict explicitly
- **Progression:** hold 7–10 days → check three gates (adherence ≥80%, energy not falling, sleep not worsening) → narrow 15–30 min, one end at a time. Any gate fails → hold or widen back; never push through a dip. Two consecutive failures → review, not more pressure — the window isn't the problem
- **Floor is 12h in Reset, 10h after.** 10h is the destination, not 16:8 — evidence for extreme windows over moderate consistent ones is thin and dropout is high
- **Existing fasting days are respected** and never scored as violations
- **Hard gate:** no time-restricted eating recommendation at all for T1D, insulin/sulfonylureas, eating-disorder history, pregnancy, or underweight

`medication_context` is a field on the health profile from Phase 0 (one line now, a migration later) — a meaningful share of future users will be on or coming off GLP-1s.

### Non-functional

| | Requirement |
|---|---|
| Latency | Plate photo reply < 15s. Chat first token < 3s. |
| Availability | Rituals must fire. Missed ritual = logged incident. |
| Isolation | Postgres RLS. No cross-user leakage, enforced at DB not app layer. |
| Cost | Hard per-user daily token cap. A runaway loop must not exceed it. |
| Observability | Every agent turn traced; cost attributable per user per day. |
| Privacy | Health data encrypted at rest. No third-party analytics on health content. |
| Testability | Every deterministic engine unit-testable without an LLM call. |

## 5. Architecture

### The two ideas that make this not-a-tracker

**1. The determinism boundary — LLM for perception and judgment, code for numbers.** Vision says "two rotis, ~150g rice, dal, curd, no visible vegetable". A deterministic IFCT table computes the macros. The LLM never invents a number. Same rule in the causal engine: statistics computed in Python, LLM only interprets. This is what makes output defensible — the entire complaint about Fitello.

**2. The Program Contract.** A versioned machine-readable document of what this user's program is right now. Generated by a rule engine from NewME phase templates + profile; the LLM personalizes only within bounds the engine sets. It is simultaneously the safety mechanism, the test surface, and the cache prefix.

### Layers

```
Channels        PWA · Telegram
   ↓
Gateway         auth · rate limit · RED-FLAG CLASSIFIER (blocking, Haiku)
   ↓
Ingestion       plate photo · voice · chat · lab PDF · manual
   ↓
Orchestrator    LangGraph supervisor — routing, persona, escalation, memory
   ↓
Specialists     Nutrition & Gut · Movement · Circadian · Mind
                Clinical · Adherence · Insight
   ↓
Engines         Program Contract · Nutrition calc · Causal stats
(no LLM)        Behaviour (JITAI, tracks, grace, rewards, if-then)
                Safety guard · Escalation rules
   ↓
Output guard    fails closed
   ↓
State           Postgres + pgvector (RLS) · Storage · LangGraph checkpoints
```

### Memory model

| Type | Holds | Store |
|---|---|---|
| Episodic | Every meal, log, message, timestamped | Postgres time-series |
| Semantic | Durable facts: H. pylori, gas with spicy food, hates paneer | `memory_facts` + pgvector |
| Procedural | The current Program Contract | `program_contract`, versioned |
| Causal | Standing hypotheses about this body, with evidence | `hypotheses` |

The causal store is the one nobody else has.

### Causal engine

**Layer 1 — statistics (deterministic).** Lagged Spearman across pillar metrics and outcomes at lag 0/1/2. Minimum-N gate, effect size reported, never a bare p-value. Below threshold it says "not enough data yet, 11 more days."

**Layer 2 — hypotheses (Opus 5, weekly).** Proposes mechanisms grounded in physiology, each with a falsifiable prediction that Layer 1 then scores.

Target output for Subbu by ~week 3:

> "Your energy dips cluster on days after you close past 21:30. Over 18 days: late nights averaged 6h10 sleep vs 7h25, next-day carbs ran 28% higher. Circadian misalignment, not willpower. Prediction: close by 20:30 for ten days and the 4pm crash softens. Tracking it."

### Behaviour engine (deterministic)

**JITAI** — decision points, tailoring variables (hydration gap, sleep debt, days since lapse, personal lapse clock), decision rules, proximal outcome logging. Cap ~2/week. **Consistency tracks** — six independent, never one global streak. Two grace days/month, silent, revealed retroactively. Identity framing ("you're someone who walks after dinner"), never 14 🔥. **Reward ledger** — user-authored, non-food, process-based early and outcome-based only in Sustain, always paid retroactively. **If-then plans** — `cue_event`, `response_action`, `anchor_routine`, `execution_count`, `success_rate`. Max 2 active, retired once automatic.

### Safety architecture (load-bearing — no clinician in the loop)

- **Onboarding contraindication screen** — pregnancy, T1D, eating disorder history, CKD, insulin/warfarin/thyroid meds. Some combinations permanently disable fasting and deficit recommendations.
- **Inbound red-flag classifier** (Haiku 4.5, before anything else) — chest pain, syncope, suicidal ideation, severe hypoglycemia, rapid unintentional weight loss. On hit: coaching halts, escalation card, logged.
- **Output guard** (deterministic, post-LLM) — rejects diagnoses, medication/dosage changes, sub-floor calorie targets, fasting advice to flagged users. Fails closed.
- **Doctor-question generator** — lab trends become questions, never conclusions.
- **Audit log** — every recommendation stores its contract clause and evidence. Replayable.
- **Consent and disclaimers** recorded with timestamp and version.

## 6. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Orchestration | LangGraph (Python 3.12) | Durable checkpointed state, interrupt/resume, supervisor pattern. Right for a long-horizon scheduled agent; most portfolio-legible framework. |
| Models | Opus 5 (`claude-opus-5`) weekly causal · Sonnet 5 (`claude-sonnet-5`) chat + vision · Haiku 4.5 (`claude-haiku-4-5`) red-flag + routing | Tiering where it matters. Adaptive thinking on Opus/Sonnet. |
| API | FastAPI + Pydantic v2 | Async, typed, webhook-friendly |
| DB | Supabase Postgres + pgvector + RLS + Storage | Tenant isolation at DB layer; auth and file storage included |
| Frontend | Next.js 15 + TypeScript + Tailwind + shadcn/ui, PWA | Installable, push, camera |
| Telegram | python-telegram-bot (webhook) | Zero-friction ambient channel |
| STT | faster-whisper self-hosted → swappable to Sarvam AI | $0 marginal; Sarvam for Indian accents |
| TTS | OpenAI TTS → swappable to ElevenLabs / Sarvam | Behind a `VoiceProvider` interface |
| Nutrition data | **INDB** (Indian Nutrient Databank) primary — 1,095 food items + 1,014 cooked Indian recipes, open-access on GitHub; **IFCT 2017** underneath | IFCT alone has no composite dishes (no chapati, sambar, rajma) — it's raw ingredients. INDB closes that gap and is the difference between usable and useless plate analysis. Deterministic macros, not an LLM guess. |
| Scheduler | APScheduler, Postgres jobstore, per-user timezone | Upgradeable to Temporal if ever needed |
| Observability | LangSmith + Sentry | Agent debugging is impossible without traces |
| Deploy | Railway/Fly · Vercel · Supabase | |

Deliberately deferred: wearables, billing, multilingual, telephony, native apps. All clean later additions.

```
swasthya/
├─ apps/{api,web}
├─ packages/
│  ├─ agents/        # LangGraph supervisor + 7 specialists
│  ├─ engines/       # program_contract/ nutrition/ causal/ behaviour/ safety/
│  ├─ ingestion/     # vision/ labs/ voice/ manual/
│  ├─ memory/        # episodic, semantic, procedural, causal
│  └─ shared/
├─ data/             # IFCT, phase templates, lab ref ranges
├─ evals/
└─ infra/
```

## 7. UI/UX Direction

**Principle: calm ritual, not a casino.** Streak-anxiety gamification is why people quit. The register is a competent, unhurried clinician who already knows your history.

- **No calorie numbers during Reset.** The methodology says no tracking in month 1, and hiding the number is the point — it removes the thing that makes people lie to their food log. Numbers arrive at Rebuild.
- **The Plate Card** — photo, balance overlay, one sentence of why. Not a macro table.
- **Every number is tappable** and expands into reasoning + evidence. The direct answer to "no proper explanations."
- **Today screen** — six pillars, one glance, no scrolling.
- **The Weekly Insight Letter** — long-form, typographic, reads like a letter from someone who thought about you. The retention mechanic.
- **Dark mode is first-class** — the 21:30 check-in happens in a dark room.
- **No red/green food moralizing.** Warm neutrals, one accent. Also solves accessibility for free.
- **One signature motion** — the pillar ring filling. Everything else is still.

### Screen inventory

Two unequal surfaces: **Telegram is where you act, the PWA is where you understand.** Most days the app is never opened.

Four tabs and one action — resisting a fifth tab is most of the design work.

| Tab | Contains |
|---|---|
| Today | The "right now" card · six pillar indicators · today's plates · the active hypothesis line. Nothing below the fold. |
| Insights | Weekly Letter + archive · Trends (leading weekly, lagging monthly) · hypothesis board · Labs — analyte trends, re-test due dates, doctor questions |
| Log (centre action) | Camera · voice · quick-pick from own history · text · "same as yesterday" |
| Program | The Contract, each clause with *why this applies to you* · phase progress · six consistency tracks + grace days · reward ledger · grocery and don't-buy lists |
| Chat | Anytime, text or voice, continuous across channels (F14) |

Under the avatar: profile, FoodIdentity + fasting calendar, conditions and medications, accountability human, notification times, export and delete. Two screens interrupt rather than sit in nav: the escalation card, and onboarding.

**The three that carry the product:** the **"right now" card** (changes all day — "window closes in 2h 20m, you're 48g short on protein" — answers the question before you ask it); the **hypothesis board** ("testing: closing before 20:30 softens the 4pm crash — day 6 of 10" — nobody ships this, and it turns the app from judge into collaborator); and the **Plate Card's "fix this" button** (small, load-bearing — how vision errors get corrected and corrections become stored facts).

**Deliberately absent:** streak counter on home, badges, points, levels, confetti, leaderboards, red/green food scoring, social feed, calorie-exceeded alerts, and any notification whose purpose is to make you feel bad for not opening the app.

### How it reasons out loud

Named modes the agent uses and names: **first principles** (reduce to what's physically true, not diet-culture heuristics) · **inversion** ("what would guarantee you fail this week?") · **second-order** ("and then what?" — skipping dinner solves tonight and creates 23:00) · **base rates before anecdote** · **Occam** (you're tired because you slept 6h10, not because of your thyroid) · **falsifiability** (every causal claim states what would disprove it) · **explicit confidence** ("I'm fairly sure" / "this is a guess" / "I don't know").

The rule that makes it real: **it must be willing to say "I don't know" and "that's not what the evidence shows," including when pushed.** An agent that always has a confident answer is the Fitello failure mode wearing better clothes.

## 8. Cost

Pricing per MTok: Opus 5 $5/$25 · Sonnet 5 $2/$10 · Haiku 4.5 $1/$5. Cache reads ~0.1×, writes ~1.25×. Batch 50% off.

Per heavy active user / month:

| Item | Cost |
|---|---|
| Sonnet 5 — chat, vision, rituals (cached) | ~$3.50 |
| Haiku 4.5 — red-flag on every inbound | ~$0.60 |
| Opus 5 — weekly causal run | ~$1.40 |
| TTS (~2K chars/day) | ~$0.90 |
| STT (self-hosted) | $0 |
| **Total** | **~$6.40 (≈ ₹570)** |

Prompt caching is the biggest lever — the Contract and profile are a stable prefix on every request. Without it, LLM cost roughly doubles.

Fixed: ~$10–20/mo solo (free tiers) → ~$125/mo at 100 users.

**Bottom line:** ~$17–27/month (₹1,500–2,400) running it for yourself, against ₹16,666/month for NewME. Build cost: ~6–8 weeks of focused evenings for Phases 0–5, +2–3 for 6–7.

## 9. How this makes you an Agentic AI Architect

This is the second deliverable, and it's why the project is worth more than the app.

Most portfolio projects are a RAG chatbot, which exercises maybe three agentic primitives. A proactive, multimodal, safety-critical, long-horizon, multi-tenant personal agent exercises nearly all of them. That's not a happy accident — it's the reason to pick this project over a flashier one.

| Competency | Where you build it | Why it matters |
|---|---|---|
| Agent topology — supervisor, hierarchical, routing | Orchestrator + 7 specialists | The most common architecture interview question |
| Tool design — schemas, strict mode, error contracts | Engines exposed as strict-schema tools | |
| Durable execution — checkpointing, resume, long horizon | LangGraph checkpoints in Postgres | Rare. Most demos are stateless. |
| Memory architecture | Four distinct stores, not one vector blob | The single thing separating a chatbot from an agent |
| Context engineering — caching, compaction, budgets | Contract as stable cache prefix | Directly determines cost and quality |
| Multimodal ingestion | Plate vision, lab extraction, STT | |
| **The determinism boundary** | LLM perceives, code computes | The most senior skill here. Almost nobody teaches it. |
| Guardrails | Inbound classifier + fail-closed output guard | Required for any regulated domain |
| Evals | Vision golden set, adversarial safety set, causal backtest | What separates architects from hobbyists |
| Observability & cost attribution | LangSmith traces, per-user cost caps | |
| Proactive agents | 07:00/21:30 rituals, JITAI decision rules | Very rare — almost all agents are reactive |
| Multi-tenancy | Postgres RLS | Productionization |
| Model tiering | Opus/Sonnet/Haiku routing | |
| Human-in-the-loop | Escalation, interrupts | |
| n-of-1 causal inference | The causal engine | Genuinely differentiated |

**Convert building into demonstrable expertise** — do these while building, not after:

- **One ADR per architectural decision.** You already have a Friday ADR habit; point it at this. The ADR log is the artifact that reads as "architect" rather than "built a thing."
- **The eval suite is the headline portfolio piece** — more impressive to a hiring architect than the app. Lead with it.
- **Write the determinism-boundary essay.** "What I refuse to give the LLM" is a differentiated point of view, and you'll have earned it.
- **Phase writeups** — one short post per phase, published. Forces articulation.

## 10. Honest assessment

### What's genuinely strong

- **You are the user.** Fastest feedback loop possible; no user research needed; real data from day one.
- **Falsifiable success criteria** — weight, HbA1c, adherence. Most side projects can't be wrong.
- **Cost is trivial** relative to the alternative.
- **Unusually complete learning vehicle** — see §9.
- **The causal engine is a real differentiator,** not a wrapper around an API.

### Real risks, ranked

| # | Risk | Mitigation |
|---|---|---|
| 1 | **Abandonment at week 6.** The single most likely failure, and ironically the same cliff the product is designed to solve | Every phase is independently demoable. Phases 0–2 alone are useful. Ship, then extend. |
| 2 | **Indian food vision accuracy.** Gravies, mixed dishes, portion ambiguity — the hardest technical problem here | Golden set from Phase 1. Design to degrade gracefully: confirm/correct UI, corrections become stored facts. Never present an estimate as fact. |
| 3 | **Causal cold start.** The hero feature is empty for 3–4 weeks | Seed with physiology-prior hypotheses. Be explicit: "not enough data yet, 11 more days." Honesty here builds more trust than a fabricated insight. |
| 4 | **n-of-1 statistics are noisy,** and self-reported sleep/steps is the noisiest input feeding the most important engine | Report confidence honestly. Revisit Health Connect in v1.5 — this is the strongest argument for adding a wearable. |
| 5 | **Building it competes with doing it.** You'd be coding at night, which costs sleep, which is pillar three | Name it. Cap build sessions. If the project damages the health it's meant to improve, that's a real failure, not irony. |
| 6 | **Safety liability** once anyone else signs up | Adversarial eval at 100% + consent + disclaimers before signups open. Non-negotiable. |
| 7 | **Cost runaway** from an agent loop bug | Hard per-user daily token cap from Phase 0, not later. |
| 8 | **LLM nondeterminism** makes regression testing slow and genuinely expensive | Budget real money for eval runs. Keep golden sets small and sharp. |
| 9 | **DPDP Act** — health data is sensitive personal data under Indian law | Fine for self-use. Needs real work before public. |
| 10 | **The relatedness gap** (§3) | Share-the-letter. Acknowledged as unsolved. |

### What I'd cut if time collapses

Voice (Phase 6) and design polish (Phase 7) — in that order. Everything through Phase 5 is load-bearing.

## 10b. Procurement

**Buy before Phase 0 (~₹5,000 / $57):**

| Item | Cost | Why now |
|---|---|---|
| Baseline blood panel (Tier 1, below) | ₹3,000–5,000 | The system has no starting point without it. Also closes P9 — Vit D/B12 never re-tested after supplementation a decade ago. **Highest-value item here.** |
| Anthropic API credits | $20 | Only hard software requirement. Phase 1 vision testing burns real tokens. |
| Body-composition scale | ₹1,500–3,000 | Program targets fat% down / muscle% up. Scale weight alone is the noisy number that demoralizes people in week 5. |
| Measuring tape | ₹100 | Waist circumference is a better metabolic marker than weight, and moves when weight doesn't. |

**Free tiers — sign up, pay nothing:** Supabase (DB + auth + 1GB storage) · Vercel Hobby · Telegram Bot API · LangSmith Developer (5k traces/mo) · Sentry Developer · GitHub.

**Buy around week 5–8:** Railway/Fly backend hosting ($5/mo, once rituals need real 24/7 uptime) · domain ($12/yr, optional) · OpenAI TTS credits ($5, Phase 6 only).

**Do not buy yet: a smartwatch.** Strongest v1.5 candidate, but wait until the causal engine exists and shows which missing signal is limiting it. Buying hardware before that is guessing.

**Tier 1 panel — do now:** HbA1c · fasting insulin + fasting glucose · lipid profile (ApoB if offered) · ALT, AST, GGT · TSH · Vitamin D (25-OH) · Vitamin B12 · CBC + ferritin · creatinine/eGFR · uric acid. Plus free at home: waist circumference, body composition.

Three need justifying. **Fasting insulin** is the highest-yield test most Indian panels skip — with fasting glucose it gives HOMA-IR, which detects insulin resistance years before HbA1c moves; given an HbA1c already just under the line, it says how much runway remains. **ALT/GGT** because non-alcoholic fatty liver is strikingly common in Indians at normal BMI and is silent. **Creatinine/eGFR** is a safety gate, not a metric — the agent cannot responsibly recommend elevated protein without it. That result gates a feature.

**Tier 2, repeat at week 12:** HbA1c, lipids, ALT/GGT, Vitamin D. **Once in a lifetime:** Lp(a).

**Skip entirely:** nutrigenomic panels, microbiome sequencing, IgG "food sensitivity" testing (no validity for diagnosing intolerance), and CGM in v1 — a 25-study review found weight/BMI changes in non-diabetics too small to show clear benefit.

## 10c. Learning order

Two rules that run counter to instinct: **LangGraph comes late, and the frontend comes last.** Starting with the framework and the UI produces a pretty shell around an agent you can't debug. Telegram provides a real working interface from day one — that is precisely why the web app waits until Phase 7.

| Stage | When | Learn | Unlocks |
|---|---|---|---|
| 1 | Before any code, 1 evening | Raw Claude API — a plain message call, a hand-written tool-use loop, an image. No framework. | Skip this and LangGraph is magic you can't debug. Non-negotiable first step. |
| 2 | Phase 0, ~3 evenings | Postgres, Supabase, RLS, FastAPI, Pydantic, Alembic | Sign up and store data safely; prove RLS blocks a cross-user read |
| 3 | Phase 1 | Telegram webhook + vision + IFCT lookup | Photograph dinner, get a real answer. First magic moment, zero UI code. |
| 4 | Phase 2 | APScheduler + rule engines (plain Python, no new AI tech) | Be woken at 07:00 by your own agent |
| 5 | Phase 3–4 | LangGraph, prompt caching, LangSmith | Now you've earned the framework — you know what it replaces |
| 6 | Phase 5–7 | Evals, then Next.js / Tailwind / PWA | The eval suite is the strongest portfolio artifact; the web app is the reward for finishing the brain |

## 11. Build Phases

Each ends in something demonstrable. No phase is pure plumbing.

**Phase 0 — Foundation.** Monorepo, schema + RLS, Supabase auth, FastAPI skeleton, Next.js shell, per-user token cap, both deployed. *Done when:* you can sign up and see an empty Today screen in production.

**Phase 1 — Ingestion & multi-method logging.** Telegram bot; plate photo → Sonnet 5 vision → items + portions → IFCT lookup. All five logging methods — the evidence says this holds adherence past week 12, so it is not deferred. *Done when:* a real thali returns an accurate item list in under 15s, and a repeat meal logs in under 5s.

**Phase 2 — Contract + rituals + behaviour engine.** Onboarding (history, conditions, baseline, contraindications, user-authored goal and rewards) → rule engine generates the Reset contract. 07:00 and 21:30 rituals. Consistency tracks, grace days, reward ledger, if-then store. *Done when:* both rituals fire unprompted for 7 straight days, and a missed day is silently covered by a grace day and revealed next morning.

**Phase 3 — Agents + JITAI.** LangGraph supervisor + seven specialists, memory wiring, prompt caching in place from the start. JITAI rules with the weekly cap. *Done when:* "why am I so tired today?" returns an answer citing your own last-3-days data, and a nudge fires at a moment that mattered.

**Phase 4 — Causal engine + Insight Letter.** Statistical layer, hypothesis lifecycle, weekly Opus run, Letter UI. *Done when:* it surfaces one true, non-obvious thing about your body you didn't know. **This is the phase that justifies the project.**

**Phase 5 — Labs + clinical + safety hardening.** Lab extraction → trends → Clinical agent → doctor questions. Red-flag classifier, output guard, audit log, escalation UI, adversarial eval suite. *Done when:* the safety suite passes 100%. **Gate for any external signup.**

**Phase 6 — Voice.** Voice note in → STT → agent → TTS out, both channels.

**Phase 7 — Design polish.** Full UI/UX pass, onboarding flow, PWA install + push, dark mode, Letter typography.

## 12. Verification

**Automated (`evals/`):**

- **Vision golden set** — 60 photographed Indian meals, hand-labelled. Item-identification F1 + portion error. Gate for Phase 1.
- **Safety adversarial set** — ~80 prompts attempting to elicit diagnoses, dosages, unsafe deficits, fasting advice to contraindicated profiles, plus red-flag phrasings. **Must pass 100%.**
- **Causal backtest** — replay synthetic 90-day histories with known injected structure; assert it finds them, and finds nothing in pure noise. False-positive rate is the metric that matters.
- **Contract engine tests** — profile → expected contract, every contraindication path.
- **Behaviour engine tests** — grace-day accounting (never >2/month, silent, revealed next morning), nudge rate limiting, reward payouts (never pre-announced, never food).
- pytest backend; vitest + Playwright for the PWA.

**Manual end-to-end:** run the full loop on yourself for 14 unbroken days. Photograph every meal, take both rituals, upload a real lab report, read the Letter. The real test is the three failure modes: does it catch the unplanned breakfast, notice the unstructured dinner, and explain the energy dip?

**Definition of done for v1:** Swasthya runs Subbu's health program unassisted for 30 days, surfaces at least one genuinely non-obvious causal insight, and correctly escalates every item in the safety eval set.

## Sources

JITAIs: key components and design principles · JITAIs: where are we now · Scoping review of JITAIs · Duolingo streaks: 2× daily retention · Streak design breakdown · Habit-forming reminders UX breakdown · Self-determination theory (Ryan & Deci) · SDT applied to behaviour change technologies · Implementation intentions and habit formation · Habit strength moderates if-then effects · AI calorie photo app accuracy · Food tracking methods compared · Omada diabetes clinical outcomes · Engagement and outcomes in a digital DPP: 3-year · Evidence brief: virtual diet programs for diabetes · Temptation bundling evaluation (Milkman) · Conversational health agents: LLM-powered framework · LLM-based nutrition coach on FHIR · Stanford HAI: an AI health coach could change your mindset
