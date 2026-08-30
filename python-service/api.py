"""
api.py

FastAPI microservice wrapping evaluate_company() for n8n to call over
HTTP, with SQLite-backed persistence (score history, client overrides).

Run with: uvicorn api:app --host 0.0.0.0 --port 8000
"""

from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

import db
from compliance.evaluate import evaluate_company

app = FastAPI(title="Vendor Compliance Microservice")


@app.on_event("startup")
def startup():
    db.init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/evaluate/{company_number}")
def evaluate(company_number: str, workflow: str = Query("monitoring", regex="^(onboarding|monitoring)$")):
    """
    Runs the full evaluation pipeline for a company and persists the
    result. This is the endpoint n8n calls — for a new vendor screen,
    pass ?workflow=onboarding; for the recurring weekly check,
    ?workflow=monitoring (default).
    """
    try:
        result = evaluate_company(company_number)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}")

    db.save_evaluation(
        company_number=company_number,
        company_name=result.get("company_name"),
        workflow=workflow,
        result=result,
    )

    return result


@app.get("/vendors")
def list_vendors():
    """Latest evaluation per vendor — feeds the dashboard's register (list) view."""
    return db.list_latest_per_vendor()


@app.get("/vendors/{company_number}")
def get_vendor(company_number: str):
    """Full latest result for one vendor, plus its score history — feeds the dossier (detail) view."""
    result = db.get_latest_evaluation(company_number)
    if result is None:
        raise HTTPException(status_code=404, detail="No evaluation found for this company_number yet.")
    result["score_history"] = db.get_score_history(company_number)
    return result


class OverrideRequest(BaseModel):
    concept: str
    value: float
    currency: Optional[str] = None
    note: Optional[str] = None


@app.post("/vendors/{company_number}/override")
def save_override(company_number: str, body: OverrideRequest):
    """
    Client-submitted correction after manually reviewing the evidence
    image for a concept (e.g. "the OCR read this as 96,873 but the scan
    clearly shows 98,044"). Persists immediately; does NOT re-run the
    evaluation — call POST /evaluate/{company_number} again afterward
    if you want the stored evaluation to reflect the correction right
    away, otherwise it'll apply next time this company is evaluated.
    """
    db.save_override(
        company_number=company_number,
        concept=body.concept,
        confirmed_value=body.value,
        confirmed_currency=body.currency,
        note=body.note,
    )
    return {"status": "saved", "company_number": company_number, "concept": body.concept}