import time

import httpx

from config import COMPANIES_HOUSE_API_KEY

BASE_URL = "https://api.company-information.service.gov.uk"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class CompaniesHouseError(Exception):
    pass


def _get(path: str, params: dict = None, attempts: int = 3) -> dict:
    url = f"{BASE_URL}{path}"
    last_error = None
    for attempt in range(attempts):
        try:
            response = httpx.get(url, params=params or {}, auth=(COMPANIES_HOUSE_API_KEY, ""), timeout=15.0)
        except httpx.HTTPError as exc:
            last_error = str(exc)
            if attempt < attempts - 1:
                time.sleep(2 ** attempt)
                continue
            raise CompaniesHouseError(f"Companies House request failed: {last_error}") from exc
        if response.status_code == 404:
            raise CompaniesHouseError(f"Not found: {path}")
        if response.status_code == 200:
            return response.json()
        last_error = f"HTTP {response.status_code}: {response.text[:300]}"
        if response.status_code in RETRYABLE_STATUS_CODES and attempt < attempts - 1:
            try:
                delay = max(1, int(response.headers.get("Retry-After", 2 ** attempt)))
            except (TypeError, ValueError):
                delay = 2 ** attempt
            time.sleep(delay)
            continue
        break
    raise CompaniesHouseError(f"Companies House request failed for {path}: {last_error}")


def get_company_profile(company_number: str) -> dict:
    return _get(f"/company/{company_number}")


def get_company_officers(company_number: str) -> dict:
    return _get(f"/company/{company_number}/officers")


def get_filing_history(company_number: str, items_per_page: int = 25) -> dict:
    return _get(f"/company/{company_number}/filing-history", {"items_per_page": items_per_page})


def get_company_charges(company_number: str) -> dict:
    return _get(f"/company/{company_number}/charges")


def get_company_insolvency(company_number: str) -> dict:
    try:
        return _get(f"/company/{company_number}/insolvency")
    except CompaniesHouseError as exc:
        if "Not found" in str(exc):
            return {}
        raise


def get_company_pscs(company_number: str) -> dict:
    return _get(f"/company/{company_number}/persons-with-significant-control")


def search_disqualified_officers(name: str) -> dict:
    return _get("/search/disqualified-officers", {"q": name, "items_per_page": 10})
