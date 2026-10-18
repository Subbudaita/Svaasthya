"""Run the plate agent over the golden set and score it.

    uv run --env-file .env python evals/vision/run.py [--dir evals/vision/golden] [--limit N]

Spends real tokens. Uses the same agent, effort and table as production.
"""

import argparse
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import anthropic
from swasthya_engines.nutrition import FoodTable, PlateRules, PortionRules
from swasthya_engines.plate import ItemIn, analyse
from swasthya_ingestion.images import prepare_image
from swasthya_ingestion.plate_agent import PerceptionError, perceive_plate

sys.path.insert(0, str(Path(__file__).parent))
from score import gate, score_photo, summarise  # noqa: E402

HERE = Path(__file__).parent


class _Unmetered:
    def record(self, input_tokens: int, output_tokens: int) -> None:
        pass


@asynccontextmanager
async def unmetered(_max_tokens: int):
    yield _Unmetered()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=HERE / "golden")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--effort", default=os.environ.get("PLATE_AGENT_EFFORT", "low"))
    args = ap.parse_args()

    labels_path = args.dir / "labels.jsonl"
    if not labels_path.exists():
        raise SystemExit(f"no labels at {labels_path} — see evals/vision/README.md")
    labels = [json.loads(line) for line in labels_path.read_text().splitlines() if line.strip()]
    labels = labels[: args.limit] if args.limit else labels

    table, portions, rules = FoodTable.load(), PortionRules.load(), PlateRules.load()
    client = anthropic.AsyncAnthropic(timeout=60.0)
    scores, latencies, rows, tokens = [], [], [], 0
    for label in labels:
        image = prepare_image((args.dir / label["image"]).read_bytes())
        try:
            result = await perceive_plate(
                client=client, table=table, meter=unmetered, image=image, effort=args.effort
            )
        except PerceptionError as exc:
            print(f"  {label['image']}: FAILED {exc}")
            scores.append(score_photo([], label["items"]))
            rows.append({"image": label["image"], "error": str(exc)})
            continue
        analysis = analyse(
            [
                ItemIn(
                    i.name, i.food_code, i.quantity, i.unit, i.estimated_grams, i.role, i.confidence
                )
                for i in result.perception.items
            ],
            table=table,
            portions=portions,
            rules=rules,
        )
        predicted = [
            {"name": i.name, "food_code": i.food_code, "grams": i.grams} for i in analysis.items
        ]
        s = score_photo(predicted, label["items"])
        scores.append(s)
        latencies.append(result.latency_ms)
        tokens += result.input_tokens + result.output_tokens
        rows.append(
            {
                "image": label["image"],
                "predicted": predicted,
                "labelled": label["items"],
                "tp": s.tp,
                "fp": s.fp,
                "fn": s.fn,
                "latency_ms": result.latency_ms,
                "turns": result.turns,
            }
        )
        print(f"  {label['image']}: tp={s.tp} fp={s.fp} fn={s.fn} {result.latency_ms} ms")
    await client.close()

    summary = summarise(scores, latencies) | {"tokens": tokens, "effort": args.effort}
    verdict = gate(summary)
    out_dir = HERE / "reports"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps({"summary": summary, "gate": verdict, "photos": rows}, indent=1))
    print(json.dumps(summary, indent=1))
    print("gate:", "PASS" if all(verdict.values()) else f"FAIL {verdict}")
    print(f"report: {out}")


if __name__ == "__main__":
    asyncio.run(main())
