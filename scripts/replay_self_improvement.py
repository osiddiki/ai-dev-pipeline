import argparse
import asyncio
import json
import sqlite3
from pathlib import Path

import aiosqlite

from agents.models import VerificationResult
from agents.supervisor import TaskDefinition
from ledger.database import DB_PATH
from orchestrator.self_improvement import FailureAnalyzer, RuleMiner


async def replay(db_path: Path, arc_id: int, project_name: str) -> None:
    db = await aiosqlite.connect(db_path)
    db.row_factory = sqlite3.Row
    analyzer = FailureAnalyzer()

    async with db.execute(
        """
        SELECT v.id, v.task_id, v.reason, v.evidence, v.changed_files, v.attempt_number,
               t.description, t.target_files, t.dependencies
        FROM verification_runs v
        LEFT JOIN tasks t ON t.arc_id = v.arc_id AND t.task_id = v.task_id
        WHERE v.arc_id = ? AND v.status = 'failed'
        ORDER BY v.id
        """,
        (arc_id,),
    ) as cursor:
        rows = await cursor.fetchall()

    inserted = 0
    for row in rows:
        changed_files = _json_list(row["changed_files"])
        task = TaskDefinition(
            id=row["task_id"] or "release",
            description=row["description"] or "Historical failed verification",
            target_files=_json_list(row["target_files"]),
            dependencies=_json_list(row["dependencies"]),
        )
        result = VerificationResult(
            success=False,
            reason=row["reason"] or "Historical verification failed.",
            evidence=row["evidence"],
        )
        analysis = analyzer.analyze(
            task=task,
            attempt=row["attempt_number"] or 1,
            changed_files=changed_files,
            verifier_result=result,
        )
        await db.execute(
            """
            INSERT INTO failure_analyses (
                arc_id, task_id, attempt_number, failure_class, confidence,
                evidence, recommended_action, changed_files
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                arc_id,
                task.id,
                row["attempt_number"] or 1,
                analysis.failure_class,
                analysis.confidence,
                analysis.evidence,
                analysis.recommended_action,
                json.dumps(changed_files),
            ),
        )
        inserted += 1

    proposals = await RuleMiner().mine_arc(db, arc_id, project_name)
    await db.commit()
    await db.close()

    print(f"Replayed {inserted} failed verification(s) for arc {arc_id}.")
    print(f"Created {len(proposals)} inactive rule proposal(s).")
    for proposal in proposals:
        print(f"- #{proposal['id']} [{proposal['scope']}] {proposal['rule_text']}")


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay GATE self-improvement analysis without invoking Codex.")
    parser.add_argument("arc_id", type=int)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--project", default="global")
    args = parser.parse_args()
    asyncio.run(replay(Path(args.db), args.arc_id, args.project))


if __name__ == "__main__":
    main()

