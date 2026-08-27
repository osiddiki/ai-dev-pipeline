import argparse
import asyncio

from orchestrator.pipeline import ReleaseArcOrchestrator


DEFAULT_DESCRIPTION = (
    "Implement the requested change, add or update tests as needed, "
    "and verify the result passes the project's test suite."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a GATE release arc against a target repository.")
    parser.add_argument("--repo", default=".", help="Target repository path")
    parser.add_argument("--issue-id", default="task", help="Release arc issue id")
    parser.add_argument(
        "--description",
        default=DEFAULT_DESCRIPTION,
        help="Issue description to send to the orchestrator",
    )
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    orchestrator = ReleaseArcOrchestrator(target_repo=args.repo)
    await orchestrator.process_issue(args.issue_id, args.description)


if __name__ == "__main__":
    asyncio.run(run())
