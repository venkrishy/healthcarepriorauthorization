from pydantic import BaseModel, Field


class PriorAuthRequest(BaseModel):
    patient_id: str = Field(..., description="Patient identifier (hashed before logging)")
    procedure_code: str = Field(..., description="CPT procedure code")
    diagnosis_codes: list[str] = Field(..., description="ICD-10 diagnosis codes")
    clinical_notes: str = Field(..., description="Clinical notes (scrubbed before telemetry)")
    requesting_provider: str = Field(..., description="NPI or provider identifier")
    insurance_plan: str = Field(..., description="Insurance plan identifier")
