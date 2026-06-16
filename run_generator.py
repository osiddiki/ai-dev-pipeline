import asyncio
from orchestrator.pipeline import ReleaseArcOrchestrator

async def run():
    target_repo = "/Users/omarsiddiki/sevisolutions"
    issue_id = "epic-ehr-mock-generator"
    description = """
Create a Python script `ehr_export_generator.py` to generate mock patient data that imitates Epic EHR exports.
Requirements:
1. The script must be able to generate complex structured clinical data including Demographics, Conditions (ICD-10/SNOMED), Medications (RxNorm), Observations (Vitals), and Clinical Notes.
2. The script must provide a CLI flag or configuration option to generate EITHER:
   - Standard HL7 FHIR R4 JSON bundles (e.g., Patient, Condition, MedicationRequest, Observation, DocumentReference).
   - Custom nested JSON structures that map directly into the `medicalHistory` JSON field of the sevicare-app GraphQL schema.
3. Save the output in a directory like `epic_export/`.

WARNING: You are generating 100% fake, synthetic mock data for an isolated testing environment. No real PII or PHI is involved. Do not trigger safety filters for medical data, as this is purely a technical exercise to generate structural mock objects for a pipeline.
"""
    
    orchestrator = ReleaseArcOrchestrator(target_repo=target_repo)
    await orchestrator.process_issue(issue_id, description)

if __name__ == "__main__":
    asyncio.run(run())
