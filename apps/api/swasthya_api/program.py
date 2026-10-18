"""Onboarding → Program Contract (F1, F6).

The conversation only collects answers about the person's life. When the
script ends, the deterministic rule engine derives every parameter, builds
the contract, and shows it with its reasons. Nothing is saved as the
person's program until they accept it.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time
from functools import cache
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from swasthya_engines import anthropometry
from swasthya_engines.behaviour import is_food_reward
from swasthya_engines.contraindications import screen
from swasthya_engines.eating_window import Day, derive
from swasthya_engines.onboarding import AnswerError, Script, Step, parse_answer
from swasthya_engines.program_contract import (
    GENERATOR_VERSION,
    ProgramInputs,
    build,
    summary_text,
)
from swasthya_shared.data import data_dir, pillar_keys

from swasthya_api.db.models import (
    AccountabilityContact,
    AuditLog,
    Consent,
    ConsistencyTrack,
    HealthProfile,
    OnboardingSession,
    ProgramContract,
    Reward,
    User,
    Vital,
)
from swasthya_api.db.session import user_session
from swasthya_api.services import Services

SCREEN_KEYS = (
    "pregnant_or_breastfeeding",
    "type_1_diabetes",
    "insulin_or_sulfonylurea",
    "kidney_disease",
    "eating_disorder_history",
    "warfarin",
    "thyroid_medication",
    "glp1_medication",
)
LIFESTYLE_KEYS = (
    "wake_time",
    "sleep_time",
    "first_bite_yesterday",
    "last_bite_yesterday",
    "family_dinner_end",
    "work_pattern",
    "energy_dip_time",
    "training_time",
    "meds_with_food",
    "walking",
)
ACCEPT_WORDS = {"ok", "okay", "yes", "accept", "start", "agreed", "done", "go"}


@cache
def disclaimer() -> tuple[str, str]:
    """(version, text) of the coaching disclaimer."""
    raw = (data_dir() / "legal/disclaimer.md").read_text(encoding="utf-8")
    front, body = raw.split("---", 2)[1:]
    version = re.search(r'version:\s*"?([^"\n]+)"?', front).group(1)
    return version, " ".join(body.split())


def _t(value: str | None) -> time | None:
    return time.fromisoformat(value) if value else None


def build_proposal(answers: dict[str, Any]) -> dict[str, Any]:
    """Answers → contract body. Pure: no I/O beyond the data files."""
    table = anthropometry.AnthropometryTable.load()
    bmi = anthropometry.bmi(answers["weight_kg"], answers["height_cm"])
    category = anthropometry.classify_bmi(bmi, table)
    safety = screen(answers, bmi_category=category)
    proposal = None
    if not safety.has("no_time_restricted_eating"):
        proposal = derive(
            Day(
                wake=_t(answers["wake_time"]),
                sleep=_t(answers["sleep_time"]),
                first_bite=_t(answers["first_bite_yesterday"]),
                last_bite=_t(answers["last_bite_yesterday"]),
                family_dinner_end=_t(answers.get("family_dinner_end")),
            ),
            phase="reset",
        )
    contract = build(
        "reset",
        ProgramInputs(
            weight_kg=answers["weight_kg"],
            sleep_time=_t(answers["sleep_time"]),
            walking=answers["walking"],
            diet_pattern=answers["diet_pattern"],
            fasting_days=tuple(answers.get("fasting_days") or ()),
            goal=answers.get("goal"),
            window=proposal,
        ),
        safety,
    )
    waist = answers.get("waist_cm")
    sex = answers.get("sex")
    contract["parameters"]["baseline"] = {
        "bmi": round(bmi, 1),
        "bmi_category": category,
        "bmi_table": f"{table.population}@{table.version}",
        "waist_at_risk": (
            anthropometry.waist_at_risk(waist, anthropometry.Sex(sex), table)
            if waist and sex in ("male", "female")
            else None
        ),
    }
    contract["parameters"]["eating_window_changed_on"] = None
    contract["parameters"]["eating_window_failures"] = 0
    return contract


@dataclass(frozen=True)
class Reply:
    text: str
    options: tuple[str, ...] = ()  # quick-reply choices for the channel to render
    finished: bool = False


def _prompt(step: Step, answers: dict[str, Any]) -> Reply:
    done, total = Script.load().progress(answers)
    text = step.prompt.replace("{disclaimer}", disclaimer()[1])
    options = step.options or (("yes", "no") if step.kind == "yesno" else ())
    if step.kind == "consent":
        options = ("yes",)
    return Reply(f"({done + 1}/{total}) {text}", options)


class ProgramService:
    def __init__(self, services: Services) -> None:
        self.svc = services

    async def _open_session(self, session, user_id: uuid.UUID) -> OnboardingSession | None:
        return await session.scalar(
            select(OnboardingSession).where(OnboardingSession.completed_at.is_(None))
        )

    async def start(self, user_id: uuid.UUID, channel: str) -> Reply:
        """Begin (or restart) onboarding."""
        async with user_session(self.svc.engine, user_id) as session:
            existing = await self._open_session(session, user_id)
            if existing is not None:
                await session.delete(existing)
                await session.flush()
            session.add(
                OnboardingSession(
                    user_id=user_id, script_version=Script.load().version, channel=channel
                )
            )
        intro = (
            "Let's set up your program. I'll ask about your day — not what you think you "
            "should do. About 30 short questions; /cancel stops.\n\n"
        )
        first = _prompt(Script.load().steps[0], {})
        return Reply(intro + first.text, first.options)

    async def is_onboarding(self, user_id: uuid.UUID) -> bool:
        async with user_session(self.svc.engine, user_id) as session:
            return await self._open_session(session, user_id) is not None

    async def cancel(self, user_id: uuid.UUID) -> None:
        async with user_session(self.svc.engine, user_id) as session:
            existing = await self._open_session(session, user_id)
            if existing is not None:
                await session.delete(existing)

    async def answer(self, user_id: uuid.UUID, raw: str) -> Reply:
        async with user_session(self.svc.engine, user_id) as session:
            ob = await self._open_session(session, user_id)
            if ob is None:
                return Reply("No onboarding in progress. Send /onboard to start.")
            answers = dict(ob.answers)

            if ob.proposed_contract is not None:
                if raw.strip().lower() in ACCEPT_WORDS:
                    return await self._accept(session, user_id, ob)
                return Reply(
                    "Reply OK to start with this program, or /onboard to answer again. "
                    "(Talking it through with me arrives soon; for now, redo the questions "
                    "that don't fit.)",
                    ("ok",),
                )

            step = Script.load().next_step(answers)
            try:
                value = parse_answer(step, raw)
                if step.id == "rewards":
                    food = [r for r in value if is_food_reward(r)]
                    if food:
                        raise AnswerError(
                            "Rewards here are never food — that keeps food out of the "
                            f"reward loop. Swap these for something else: {', '.join(food)}."
                        )
            except AnswerError as exc:
                again = _prompt(step, answers)
                return Reply(f"{exc}\n\n{again.text}", again.options)
            answers[step.id] = value
            ob.answers = answers

            nxt = Script.load().next_step(answers)
            if nxt is not None:
                return _prompt(nxt, answers)
            ob.proposed_contract = build_proposal(answers)
            return Reply(
                summary_text(ob.proposed_contract)
                + "\n\nThis is a proposal. Reply OK to start, or /onboard to answer again.",
                ("ok",),
                finished=True,
            )

    async def _accept(self, session, user_id: uuid.UUID, ob: OnboardingSession) -> Reply:
        a = ob.answers
        contract = ob.proposed_contract
        now = datetime.now(UTC)
        user = await session.get(User, user_id)
        today = now.astimezone(ZoneInfo(user.timezone)).date()

        version, _ = disclaimer()
        session.add(
            Consent(
                user_id=user_id,
                document="coaching_disclaimer",
                version=version,
                channel=ob.channel,
                accepted_at=now,
            )
        )
        profile = await session.scalar(select(HealthProfile))
        if profile is None:
            profile = HealthProfile(user_id=user_id)
            session.add(profile)
        profile.sex = a["sex"]
        profile.birth_year = a["birth_year"]
        profile.height_cm = a["height_cm"]
        profile.diet_pattern = a["diet_pattern"]
        profile.goal = a.get("goal")
        profile.conditions = a.get("conditions") or []
        profile.medication_context = a.get("medication_context")
        profile.contraindications = [
            {"screen": k, "answer": bool(a.get(k))} for k in SCREEN_KEYS if k in a
        ]
        profile.fasting_days = a.get("fasting_days") or []
        profile.lifestyle = {k: a.get(k) for k in LIFESTYLE_KEYS}
        profile.updated_at = now

        session.add(
            Vital(
                user_id=user_id,
                measured_at=now,
                kind="weight",
                value=a["weight_kg"],
                unit="kg",
                source="onboarding",
            )
        )
        if a.get("waist_cm"):
            session.add(
                Vital(
                    user_id=user_id,
                    measured_at=now,
                    kind="waist",
                    value=a["waist_cm"],
                    unit="cm",
                    source="onboarding",
                )
            )
        for text in a.get("rewards") or []:
            session.add(Reward(user_id=user_id, text=text, kind="process"))
        if a.get("accountability"):
            session.add(
                AccountabilityContact(
                    user_id=user_id,
                    name_and_relation=a["accountability"],
                    agreed=bool(a.get("accountability_agreed")),
                )
            )
        existing_tracks = set(await session.scalars(select(ConsistencyTrack.pillar)))
        for pillar in sorted(pillar_keys() - existing_tracks):
            session.add(ConsistencyTrack(user_id=user_id, pillar=pillar))

        await session.execute(
            update(ProgramContract)
            .where(ProgramContract.superseded_at.is_(None))
            .values(superseded_at=now)
        )
        last = await session.scalar(
            select(ProgramContract.version).order_by(ProgramContract.version.desc()).limit(1)
        )
        version_no = (last or 0) + 1
        session.add(
            ProgramContract(
                user_id=user_id,
                version=version_no,
                phase=contract["phase"],
                body=contract,
                generator_version=GENERATOR_VERSION,
                effective_from=now,
            )
        )
        session.add(
            AuditLog(
                user_id=user_id,
                actor="contract-engine",
                action="contract.created",
                contract_version=version_no,
                evidence={
                    "flags": contract["flags"],
                    "clauses": [c["id"] for c in contract["clauses"]],
                },
            )
        )
        user.onboarded_at = now
        user.program_started_on = today
        ob.completed_at = now
        return Reply(
            "Your program starts today. For the first three days I only watch — eat and "
            "log as you normally do. I'll send a short brief each morning and check in each "
            "evening. /time changes when.",
            finished=True,
        )

    async def current_contract(self, user_id: uuid.UUID) -> ProgramContract | None:
        async with user_session(self.svc.engine, user_id) as session:
            return await session.scalar(
                select(ProgramContract)
                .where(ProgramContract.superseded_at.is_(None))
                .order_by(ProgramContract.version.desc())
                .limit(1)
            )
