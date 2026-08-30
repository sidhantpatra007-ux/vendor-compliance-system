"""Local screening service backed by the official UK Sanctions List XML."""

import hashlib
import json
import os
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

SOURCE_URL = "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.xml"
DATA_DIR = Path(os.getenv("SANCTIONS_DATA_DIR", "/app/data"))
SNAPSHOT_PATH = DATA_DIR / "uk_sanctions_list.json"
REFRESH_SECONDS = 86400
MAX_AGE = timedelta(hours=30)

app = FastAPI(title="UK Sanctions List Service")
_lock = threading.RLock()
_stop_event = threading.Event()
_refresh_thread: threading.Thread | None = None


class Subject(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    subject_type: Literal["company", "person", "ship"] = "company"


class ScreenRequest(BaseModel):
    subjects: list[Subject] = Field(min_length=1, max_length=100)
    match_threshold: int = Field(default=90, ge=80, le=100)
    review_threshold: int = Field(default=80, ge=60, le=100)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalise(value: str) -> str:
    return " ".join(value.upper().replace("&", " AND ").split())


def _list_type(subject_type: str) -> str:
    return {"company": "Entity", "person": "Individual", "ship": "Ship"}[subject_type]


def _names(designation: ET.Element) -> list[str]:
    result: list[str] = []
    names_element = designation.find("Names")
    if names_element is None:
        return result
    for item in names_element.findall("Name"):
        name6 = item.findtext("Name6")
        if name6 and name6.strip():
            result.append(name6.strip())
        parts = [
            (child.text or "").strip()
            for child in item
            if child.tag.startswith("Name") and child.tag != "NameType" and (child.text or "").strip()
        ]
        if parts:
            result.append(" ".join(parts))
    return list(dict.fromkeys(result))


def _parse(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    entries = []
    for designation in root.findall("Designation"):
        unique_id = designation.findtext("UniqueID")
        designation_type = designation.findtext("IndividualEntityShip")
        names = _names(designation)
        if unique_id and designation_type in {"Entity", "Individual", "Ship"} and names:
            entries.append({"unique_id": unique_id.strip(), "type": designation_type, "names": names})
    return entries


def _load() -> dict | None:
    if not SNAPSHOT_PATH.exists():
        return None
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def _save(snapshot: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = SNAPSHOT_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    temporary.replace(SNAPSHOT_PATH)


def refresh_list(force: bool = False) -> dict:
    with _lock:
        current = _load()
        if current and not force:
            if _now() - datetime.fromisoformat(current["fetched_at"]) < timedelta(seconds=REFRESH_SECONDS):
                return current
        response = httpx.get(SOURCE_URL, timeout=60.0, follow_redirects=True)
        response.raise_for_status()
        entries = _parse(response.content)
        if not entries:
            raise RuntimeError("The official list parsed to zero records")
        snapshot = {
            "source_url": SOURCE_URL,
            "fetched_at": _now().isoformat(),
            "list_version": hashlib.sha256(response.content).hexdigest(),
            "entry_count": len(entries),
            "entries": entries,
        }
        _save(snapshot)
        return snapshot


def _snapshot() -> dict:
    with _lock:
        current = _load()
    return current if current else refresh_list()


def _refresh_loop() -> None:
    while not _stop_event.wait(REFRESH_SECONDS):
        try:
            refresh_list(force=True)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("UK Sanctions List refresh failed")


@app.on_event("startup")
def startup() -> None:
    global _refresh_thread
    refresh_list()
    _refresh_thread = threading.Thread(target=_refresh_loop, daemon=True)
    _refresh_thread.start()


@app.on_event("shutdown")
def shutdown() -> None:
    _stop_event.set()
    if _refresh_thread:
        _refresh_thread.join(timeout=5)


@app.get("/")
def health() -> dict:
    try:
        current = _snapshot()
        age = _now() - datetime.fromisoformat(current["fetched_at"])
        return {
            "status": "ok" if age <= MAX_AGE else "stale",
            "list_version": current["list_version"],
            "list_fetched_at": current["fetched_at"],
            "entry_count": current["entry_count"],
        }
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


@app.post("/refresh")
def refresh() -> dict:
    try:
        current = refresh_list(force=True)
    except Exception as exc:
        raise HTTPException(503, f"Could not refresh official UK Sanctions List: {exc}") from exc
    return {"status": "refreshed", "list_version": current["list_version"], "list_fetched_at": current["fetched_at"], "entry_count": current["entry_count"]}


@app.post("/screen")
def screen(request: ScreenRequest) -> dict:
    try:
        current = _snapshot()
    except Exception as exc:
        raise HTTPException(503, f"No usable UK Sanctions List snapshot: {exc}") from exc
    results = []
    for subject in request.subjects:
        score = -1.0
        candidate = None
        for entry in current["entries"]:
            if entry["type"] != _list_type(subject.subject_type):
                continue
            for listed_name in entry["names"]:
                match_score = fuzz.token_sort_ratio(_normalise(subject.name), _normalise(listed_name))
                if match_score > score:
                    score = match_score
                    candidate = {"unique_id": entry["unique_id"], "matched_name": listed_name}
        results.append({
            "subject_name": subject.name,
            "subject_type": subject.subject_type,
            "matched": score >= request.match_threshold,
            "review_required": score >= request.review_threshold,
            "score": round(score, 2) if score >= 0 else None,
            "candidate": candidate,
        })
    return {"list_version": current["list_version"], "list_fetched_at": current["fetched_at"], "results": results}
