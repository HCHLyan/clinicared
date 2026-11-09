"""Patient endpoints. Every call is audited by ``AuditMiddleware`` (RN-10)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.models.patient import Patient
from src.schemas.patient import PatientCreate, PatientResponse

router = APIRouter(prefix="/patients", tags=["Patients"])


@router.post("/", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
def create_patient(patient: PatientCreate, db: Session = Depends(get_db)) -> Patient:
    """Create a patient; the national id must be unique."""
    if db.query(Patient).filter(Patient.national_id == patient.national_id).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"El paciente con documento {patient.national_id} ya está registrado.",
        )
    new_patient = Patient(**patient.model_dump())
    db.add(new_patient)
    db.commit()
    db.refresh(new_patient)
    return new_patient


@router.get("/", response_model=list[PatientResponse])
def get_patients(
    skip: int = Query(0, ge=0, description="Número de registros a saltar"),
    limit: int = Query(100, ge=1, le=500, description="Límite de registros a retornar"),
    national_id: str | None = Query(None, description="Filtrar por documento de identidad"),
    db: Session = Depends(get_db),
) -> list[Patient]:
    """List patients with pagination (stable order by id)."""
    query = db.query(Patient)
    if national_id:
        query = query.filter(Patient.national_id == national_id)
    return query.order_by(Patient.id).offset(skip).limit(limit).all()


@router.get("/{patient_id}", response_model=PatientResponse)
def get_patient_by_id(patient_id: int, db: Session = Depends(get_db)) -> Patient:
    """Get one patient by internal id."""
    patient = db.get(Patient, patient_id)
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Paciente con ID {patient_id} no encontrado.")
    return patient
