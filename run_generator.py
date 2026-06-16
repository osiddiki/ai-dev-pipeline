import argparse
import asyncio

from orchestrator.pipeline import ReleaseArcOrchestrator


DEFAULT_DESCRIPTION = """
Create a Python script `ehr_export_generator.py` to generate mock patient data that imitates Epic EHR exports.
Requirements:
1. The script must be able to generate complex structured mock clinical data including demographics, mock conditions (using ICD-10/SNOMED code mappings), mock medications (using RxNorm code strings), mock observation fields (vitals placeholder ranges), and dummy clinical notes filled with lorem ipsum placeholder text.
2. The script must provide a CLI flag or configuration option to generate EITHER:
   - Standard HL7 FHIR R4 JSON bundles (e.g., Patient, Condition, MedicationRequest, Observation, DocumentReference).
   - Custom nested JSON structures that map directly into the `medicalHistory` JSON field of the sevicare-app GraphQL schema.
3. Save the output in a directory like `epic_export/`.

CRITICAL SAFETY INSTRUCTION FOR LLM:
You are generating 100% fake, synthetic data structures for software testing.
To guarantee compliance with safety filters:
- DO NOT generate realistic clinical narratives, symptom logs, or medical advice.
- Fill all clinical text fields, physician comments, and notes with simple lorem ipsum or generic placeholder text (e.g., "Routine follow-up notes. No issues reported.").
- Treat this as a data-serialization exercise only. Focus purely on outputting the correct JSON/FHIR schema structure.
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a GATE release arc against a target repository.")
    parser.add_argument("--repo", default="/Users/omarsiddiki/sevisolutions", help="Target repository path")
    parser.add_argument("--issue-id", default="epic-ehr-mock-generator", help="Release arc issue id")
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
