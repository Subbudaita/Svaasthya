"""Guard: nothing personal is encoded in code (docs/PLAN.md, hard constraints).

Personal values live in database rows written by onboarding. This is a cheap
tripwire for the most likely leak — the design persona's details being pasted
into code while building for user #1. It scans code only; ``docs/`` (where the
persona is described) and ``data/`` (global tables) are exempt.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_DIRS = ("apps", "packages")
SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".sql", ".yaml", ".yml", ".json"}
SKIP_PARTS = {"node_modules", ".next", "__pycache__", ".venv", "tests"}

# Persona details from docs/PLAN.md §1 that must never become constants.
FORBIDDEN = [r"\bsubbu\b", r"pylori", r"peptic", r"\bfitello\b"]


def test_no_persona_values_in_code():
    pattern = re.compile("|".join(FORBIDDEN), re.IGNORECASE)
    hits = []
    for top in CODE_DIRS:
        for path in (ROOT / top).rglob("*"):
            if path.suffix not in SUFFIXES or SKIP_PARTS & set(path.parts):
                continue
            if path.name == "package-lock.json":
                continue
            for n, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not hits, "personal values in code:\n" + "\n".join(hits)
