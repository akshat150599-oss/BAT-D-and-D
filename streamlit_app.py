"""
Demurrage & Detention Analyzer
==============================
Standalone Streamlit app for ocean shipment demurrage and detention cost analysis.

Calculation Logic:
  POL Demurrage = Container Loaded on Vessel - Container Gate In at POL
                = CLL - CGI

  POD Demurrage = Gate Out Full from POD - Discharge at POD
                = CGO - CDD

  POD Detention = Container Empty Return - Gate Out Full from POD
                = CER - CGO

Exclusion rule:
  CANCELLED -> shipment excluded from D&D entirely

No CER handling:
  ACTIVE    -> detention accumulates to today's date (analysis run time)
  COMPLETED -> detention end = SHIPMENT_MODIFIED_DATE

Combined Free Days:
  Combined free days are consumed continuously across POD demurrage first,
  then POD detention receives the remaining free-day balance.

Contract Gaps:
  Shipments without matching contracts are not costed. They are surfaced as
  potential risk if their POL demurrage, POD demurrage, or POD detention days
  are above averages observed in matched/contracted shipments.

Run:
  streamlit run demurrage_detention_analyzer.py
"""

import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
from datetime import datetime
from io import BytesIO

# -----------------------------------------------------------------------------
# PAGE CONFIG
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Demurrage & Detention Analyzer",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -----------------------------------------------------------------------------
# COLORS
# -----------------------------------------------------------------------------
POL_DEM_COLOR = "#00a6ff"
DEM_COLOR = "#f5a623"
DET_COLOR = "#7b61ff"
TOTAL_COLOR = "#00d4aa"
ALERT_COLOR = "#ff5c5c"
TIER1_COLOR = "#8fd694"
TIER2_COLOR = "#f5a623"
THEREAFTER_COLOR = "#ff5c5c"

# -----------------------------------------------------------------------------
# CUSTOM CSS
# -----------------------------------------------------------------------------
st.markdown(
    """
<style>
    .block-container {
        padding-top: 1.5rem;
        max-width: 1250px;
    }

    div[data-testid="stTabs"] {
        margin-top: 0.5rem !important;
    }

    div[data-testid="stTabs"] div[role="tablist"] {
        min-height: 64px !important;
        height: 64px !important;
        padding-top: 8px !important;
        padding-bottom: 14px !important;
        margin-bottom: 18px !important;
        border-bottom: 1px solid #2a2d3a !important;
        overflow: visible !important;
        gap: 18px !important;
    }

    div[data-testid="stTabs"] button[role="tab"] {
        min-height: 48px !important;
        height: 48px !important;
        padding: 8px 8px 12px 8px !important;
        margin: 0 !important;
        overflow: visible !important;
        border-bottom: none !important;
        background: transparent !important;
    }

    div[data-testid="stTabs"] button[role="tab"] p {
        color: #cbd5e1 !important;
        font-size: 15px !important;
        font-weight: 800 !important;
        line-height: 22px !important;
        margin: 0 !important;
        padding: 0 !important;
        white-space: nowrap !important;
        overflow: visible !important;
        text-overflow: unset !important;
    }

    div[data-testid="stTabs"] button[role="tab"]:hover p {
        color: #ffffff !important;
    }

    div[data-testid="stTabs"] button[aria-selected="true"] p {
        color: #ff4b4b !important;
        font-weight: 900 !important;
    }

    div[data-testid="stTabs"] button[aria-selected="true"] {
        border-bottom: 4px solid #ff4b4b !important;
    }

    div[data-testid="stTabs"] div {
        overflow: visible !important;
    }

    div[data-testid="stMetric"] {
        background: #111827;
        border: 1px solid #2a2d3a;
        border-radius: 10px;
        padding: 18px 20px;
    }

    div[data-testid="stMetric"] label {
        color: #ffffff !important;
        font-size: 13px !important;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        font-weight: 900 !important;
    }

    div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        color: #ffffff !important;
        font-size: 32px !important;
        font-weight: 900 !important;
    }

    div[data-testid="stMetricDelta"] {
        color: #22c55e !important;
        font-weight: 900 !important;
    }
</style>
""",
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# CONTRACT CSV PARSER
# -----------------------------------------------------------------------------
NUMERIC_CONTRACT_COLS = [
    "freeDemurrageDays",
    "firstDemurrageDays",
    "firstDemurrageRate",
    "secondDemurrageDays",
    "secondDemurrageRate",
    "thereafterDemurrageRate",
    "freeDetentionDays",
    "firstDetentionDays",
    "firstDetentionRate",
    "secondDetentionDays",
    "secondDetentionRate",
    "thereafterDetentionRate",
    "combinedFreeDays",
]

NULLABLE_CONTRACT_COLS = [
    "freeDemurrageDays",
    "freeDetentionDays",
    "combinedFreeDays",
    "carrierScac",
    "ffwScac",
]


def parse_contracts_csv(contract_file):
    """
    Read contract CSV flexibly.

    Important behavior:
    - Blank / missing numeric tariff fields are allowed.
    - demurrageStartEventType controls whether a demurrage row is POL or POD:
        contains "POL" -> POL demurrage
        contains "POD" -> POD demurrage
      If neither is present, that row is not used for demurrage pricing.
    - Detention fields are treated as POD detention when present.
    """
    cdf = pd.read_csv(contract_file)

    # Ensure optional columns exist so later logic can read safely.
    optional_cols = [
        "terminalIdentifier", "demurrageStartEventType", "demurrageTariffCalculationMethod",
        "validityStartDate", "validityEndDate", "freeDemurrageDays", "firstDemurrageDays",
        "firstDemurrageRate", "secondDemurrageDays", "secondDemurrageRate", "thereafterDemurrageRate",
        "detentionStartEventType", "detentionTariffCalculationMethod", "freeDetentionDays",
        "firstDetentionDays", "firstDetentionRate", "secondDetentionDays", "secondDetentionRate",
        "thereafterDetentionRate", "currency", "carrierScac", "ffwScac", "portOfLoadingLocode",
        "combinedFreeDays",
    ]
    for col in optional_cols:
        if col not in cdf.columns:
            cdf[col] = np.nan

    for col in NUMERIC_CONTRACT_COLS:
        cdf[col] = pd.to_numeric(cdf[col], errors="coerce")

    records = cdf.to_dict(orient="records")
    for rec in records:
        for col, val in list(rec.items()):
            if isinstance(val, float) and np.isnan(val):
                rec[col] = None
            elif isinstance(val, str) and val.strip() == "":
                rec[col] = None

    return records, cdf


def _event_scope(value):
    """Return POL/POD if the event value explicitly contains those tokens."""
    text = str(value or "").upper()
    if "POL" in text:
        return "POL"
    if "POD" in text:
        return "POD"
    return None


def _has_any_number(rec, cols):
    for col in cols:
        val = rec.get(col)
        if val is None:
            continue
        try:
            if not np.isnan(val):
                return True
        except (TypeError, ValueError):
            return True
    return False


def _has_demurrage_terms(rec):
    return _has_any_number(
        rec,
        [
            "freeDemurrageDays", "firstDemurrageDays", "firstDemurrageRate",
            "secondDemurrageDays", "secondDemurrageRate", "thereafterDemurrageRate",
        ],
    )


def _has_detention_terms(rec):
    return _has_any_number(
        rec,
        [
            "freeDetentionDays", "firstDetentionDays", "firstDetentionRate",
            "secondDetentionDays", "secondDetentionRate", "thereafterDetentionRate",
        ],
    )

# -----------------------------------------------------------------------------
# CALCULATION HELPERS
# -----------------------------------------------------------------------------
def _safe(val, default=0):
    if val is None:
        return default
    try:
        if np.isnan(val):
            return default
    except (TypeError, ValueError):
        pass
    return val


def _days_between(end_ts, start_ts):
    if pd.isna(end_ts) or pd.isna(start_ts):
        return None
    return max(0, (end_ts - start_ts).total_seconds() / 86400)


def calc_tiered_cost_breakdown(chargeable_days, t1_days, t1_rate, t2_days, t2_rate, thereafter_rate):
    """Return detailed tier day/cost breakdown for a 3-tier tariff."""
    result = {
        "tier1_days": 0.0,
        "tier1_cost": 0.0,
        "tier2_days": 0.0,
        "tier2_cost": 0.0,
        "thereafter_days": 0.0,
        "thereafter_cost": 0.0,
        "total_cost": 0.0,
    }

    if chargeable_days <= 0:
        return result

    remaining = chargeable_days

    t1 = min(remaining, _safe(t1_days))
    result["tier1_days"] = t1
    result["tier1_cost"] = t1 * _safe(t1_rate)
    remaining -= t1

    if remaining > 0:
        t2 = min(remaining, _safe(t2_days))
        result["tier2_days"] = t2
        result["tier2_cost"] = t2 * _safe(t2_rate)
        remaining -= t2

    if remaining > 0:
        result["thereafter_days"] = remaining
        result["thereafter_cost"] = remaining * _safe(thereafter_rate)

    result["total_cost"] = round(
        result["tier1_cost"] + result["tier2_cost"] + result["thereafter_cost"], 2
    )
    return result


def _blank_contract_profile():
    return {
        "pol_dem": None,
        "pod_dem": None,
        "pod_det": None,
        "source_records": [],
        "is_estimate": False,
    }


def _put_profile(lookup, key):
    if key not in lookup:
        lookup[key] = _blank_contract_profile()
    return lookup[key]


def build_contract_lookup(contracts_list):
    """
    Build carrier and FFW lookups.

    Each lookup key stores separate rate records for:
      - pol_dem: POL demurrage from CGI to CLL
      - pod_dem: POD demurrage from CDD to CGO
      - pod_det: POD detention from CGO to CER

    This prevents POD-only demurrage contracts from being accidentally applied
    to POL demurrage.
    """
    carrier_lookup = {}
    ffw_lookup = {}

    for c in contracts_list or []:
        terminal = str(c.get("terminalIdentifier", "") or "").strip()
        pol = str(c.get("portOfLoadingLocode", "") or "").strip()
        carrier = c.get("carrierScac")
        ffw = c.get("ffwScac")

        keys = []
        if carrier:
            keys.append((carrier_lookup, f"{terminal}|{str(carrier).strip()}|{pol}"))
        if ffw:
            keys.append((ffw_lookup, f"{terminal}|{str(ffw).strip()}|{pol}"))

        dem_scope = _event_scope(c.get("demurrageStartEventType"))
        has_dem = _has_demurrage_terms(c)
        has_det = _has_detention_terms(c)

        for lookup, key in keys:
            profile = _put_profile(lookup, key)
            profile["source_records"].append(c)

            if has_dem and dem_scope == "POL" and profile["pol_dem"] is None:
                profile["pol_dem"] = c
            elif has_dem and dem_scope == "POD" and profile["pod_dem"] is None:
                profile["pod_dem"] = c

            # Detention is POD detention. It does not need demurrageStartEventType.
            if has_det and profile["pod_det"] is None:
                profile["pod_det"] = c

    return carrier_lookup, ffw_lookup


def make_estimate_contract_profile(
    pol_free_dem,
    pol_t1_days,
    pol_t1_rate,
    pol_t2_days,
    pol_t2_rate,
    pol_thereafter_rate,
    use_combined_pod_free,
    combined_pod_free_days,
    pod_free_dem,
    pod_t1_days,
    pod_t1_rate,
    pod_t2_days,
    pod_t2_rate,
    pod_thereafter_rate,
    pod_free_det,
    det_t1_days,
    det_t1_rate,
    det_t2_days,
    det_t2_rate,
    det_thereafter_rate,
):
    """Create an estimate profile shaped like the contract lookup profile."""
    pol_dem = {
        "terminalIdentifier": "ESTIMATE",
        "portOfLoadingLocode": "ESTIMATE",
        "demurrageStartEventType": "POL_ESTIMATE",
        "freeDemurrageDays": pol_free_dem,
        "firstDemurrageDays": pol_t1_days,
        "firstDemurrageRate": pol_t1_rate,
        "secondDemurrageDays": pol_t2_days,
        "secondDemurrageRate": pol_t2_rate,
        "thereafterDemurrageRate": pol_thereafter_rate,
        "combinedFreeDays": None,
    }

    pod_dem = {
        "terminalIdentifier": "ESTIMATE",
        "portOfLoadingLocode": "ESTIMATE",
        "demurrageStartEventType": "POD_ESTIMATE",
        "freeDemurrageDays": 0 if use_combined_pod_free else pod_free_dem,
        "firstDemurrageDays": pod_t1_days,
        "firstDemurrageRate": pod_t1_rate,
        "secondDemurrageDays": pod_t2_days,
        "secondDemurrageRate": pod_t2_rate,
        "thereafterDemurrageRate": pod_thereafter_rate,
        "combinedFreeDays": combined_pod_free_days if use_combined_pod_free else None,
    }

    pod_det = {
        "terminalIdentifier": "ESTIMATE",
        "portOfLoadingLocode": "ESTIMATE",
        "detentionStartEventType": "POD_ESTIMATE",
        "freeDetentionDays": 0 if use_combined_pod_free else pod_free_det,
        "firstDetentionDays": det_t1_days,
        "firstDetentionRate": det_t1_rate,
        "secondDetentionDays": det_t2_days,
        "secondDetentionRate": det_t2_rate,
        "thereafterDetentionRate": det_thereafter_rate,
        "combinedFreeDays": combined_pod_free_days if use_combined_pod_free else None,
    }

    return {
        "pol_dem": pol_dem,
        "pod_dem": pod_dem,
        "pod_det": pod_det,
        "source_records": [pol_dem, pod_dem, pod_det],
        "is_estimate": True,
    }


def _first_existing_column(df, candidates):
    for col in candidates:
        if col in df.columns:
            return col
    return None


def _clean_scac(val):
    if val is None:
        return ""
    try:
        if pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass
    return str(val).strip()


def _shipment_match_identity(row):
    """Prefer shipment carrier SCAC; if blank, fall back to shipment freight-forwarder SCAC."""
    carrier = _clean_scac(row.get("CARRIER_SCAC", ""))
    ffw = _clean_scac(row.get("FFW_SCAC", ""))
    if carrier:
        return carrier, "Carrier"
    if ffw:
        return ffw, "FFW"
    return "", "Missing"


def normalize_required_columns(df):
    # Normalize shipment-side freight forwarder into FFW_SCAC if the export uses a different name.
    ffw_aliases = [
        "FFW_SCAC",
        "FFW",
        "FFW_SCAC_CODE",
        "FREIGHT_FORWARDER_SCAC",
        "FREIGHT_FORWARDER",
        "FORWARDER_SCAC",
        "FORWARDER",
        "FREIGHT_FORWARDER_CODE",
    ]
    ffw_col = _first_existing_column(df, ffw_aliases)
    if ffw_col is not None and ffw_col != "FFW_SCAC":
        df["FFW_SCAC"] = df[ffw_col]

    for col in [
        "SHIPMENT_ID",
        "CONTAINER_NUMBER",
        "CARRIER_SCAC",
        "CARRIER_NAME",
        "FFW_SCAC",
        "POL_LOCODE",
        "POL",
        "POD_LOCODE",
        "POD",
        "SUBSCRIPTION_STATUS",
        "LIFECYCLE_STATUS",
        "SHIPMENT_MODIFIED_DATE",
    ]:
        if col not in df.columns:
            if col == "SHIPMENT_MODIFIED_DATE":
                df[col] = pd.NaT
            else:
                df[col] = ""

    for col in ["CDD", "CGO", "CER", "VAD", "VDL", "CGI", "CEP", "CLL"]:
        if col not in df.columns:
            df[col] = pd.NaT

    return df


# -----------------------------------------------------------------------------
# D&D CALCULATION ENGINE
# -----------------------------------------------------------------------------
def _get_combined_pod_free_days(pod_dem_contract, pod_det_contract):
    """Combined free days can only apply across POD demurrage + POD detention."""
    for c in [pod_dem_contract, pod_det_contract]:
        if c is None:
            continue
        val = c.get("combinedFreeDays")
        if val is not None:
            return val
    return None


def _contract_label(profile, component_key):
    c = profile.get(component_key) if profile else None
    if c is None:
        return "Not configured"
    if profile.get("is_estimate"):
        return "Estimate"
    return str(c.get("terminalIdentifier", "")) or "Contract"


def process_shipments(df, contracts_list=None, estimate_profile=None, use_estimate=False):
    df = normalize_required_columns(df.copy())

    event_cols = ["CDD", "CGO", "CER", "VAD", "VDL", "CGI", "CEP", "CLL"]
    for col in event_cols:
        df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)

    if "REPORTING_DATE" in df.columns:
        df["REPORTING_DATE"] = pd.to_datetime(df["REPORTING_DATE"], errors="coerce", utc=True)

    df["SHIPMENT_MODIFIED_DATE"] = pd.to_datetime(
        df["SHIPMENT_MODIFIED_DATE"], errors="coerce", utc=True
    )

    analysis_run_date = pd.Timestamp.now(tz="UTC")
    carrier_lookup, ffw_lookup = build_contract_lookup(contracts_list or [])

    df["SUBSCRIPTION_STATUS"] = df["SUBSCRIPTION_STATUS"].fillna("").astype(str).str.upper()
    cancelled_count = (df["SUBSCRIPTION_STATUS"] == "CANCELLED").sum()
    original_count = len(df)
    df = df[df["SUBSCRIPTION_STATUS"] != "CANCELLED"].copy()

    match_identity = df.apply(_shipment_match_identity, axis=1, result_type="expand")
    df["CARRIER_FFW_SCAC"] = match_identity[0]
    df["MATCHED_PARTY_TYPE"] = match_identity[1]
    df["_match_key"] = (
        df["POD_LOCODE"].fillna("").astype(str).str.strip()
        + "|"
        + df["CARRIER_FFW_SCAC"].fillna("").astype(str).str.strip()
        + "|"
        + df["POL_LOCODE"].fillna("").astype(str).str.strip()
    )

    matched_results = []
    unmatched_results = []

    for _, row in df.iterrows():
        key = row["_match_key"]
        contract_profile = estimate_profile if use_estimate else (carrier_lookup.get(key) or ffw_lookup.get(key))

        cgi = row["CGI"]
        cll = row["CLL"]
        cdd = row["CDD"]
        cgo = row["CGO"]
        cer = row["CER"]
        sub_status = row.get("SUBSCRIPTION_STATUS", "")

        pol_dem_total_days = _days_between(cll, cgi)
        pod_dem_total_days = _days_between(cgo, cdd)

        pod_det_total_days = None
        det_accumulating = False
        det_end_source = ""
        det_end_ts = pd.NaT

        if not pd.isna(cgo):
            if not pd.isna(cer):
                pod_det_total_days = _days_between(cer, cgo)
                det_end_source = "CER"
                det_end_ts = cer
            elif sub_status == "ACTIVE":
                pod_det_total_days = _days_between(analysis_run_date, cgo)
                det_accumulating = True
                det_end_source = "TODAY"
                det_end_ts = analysis_run_date
            elif sub_status == "COMPLETED":
                modified = row.get("SHIPMENT_MODIFIED_DATE", pd.NaT)
                if not pd.isna(modified):
                    pod_det_total_days = _days_between(modified, cgo)
                    det_end_ts = modified
                det_end_source = "MODIFIED_DATE"

        base_record = {
            "SHIPMENT_ID": row["SHIPMENT_ID"],
            "CONTAINER_NUMBER": row.get("CONTAINER_NUMBER", ""),
            "CARRIER_SCAC": row["CARRIER_SCAC"],
            "CARRIER_NAME": row.get("CARRIER_NAME", ""),
            "FFW_SCAC": row.get("FFW_SCAC", ""),
            "CARRIER_FFW_SCAC": row.get("CARRIER_FFW_SCAC", ""),
            "MATCHED_PARTY_TYPE": row.get("MATCHED_PARTY_TYPE", ""),
            "POL_LOCODE": row["POL_LOCODE"],
            "POL": row.get("POL", ""),
            "POD_LOCODE": row["POD_LOCODE"],
            "POD": row.get("POD", ""),
            "SUBSCRIPTION_STATUS": sub_status,
            "LIFECYCLE_STATUS": row.get("LIFECYCLE_STATUS", ""),
            "CGI": cgi if not pd.isna(cgi) else pd.NaT,
            "CLL": cll if not pd.isna(cll) else pd.NaT,
            "CDD": cdd if not pd.isna(cdd) else pd.NaT,
            "CGO": cgo if not pd.isna(cgo) else pd.NaT,
            "CER": cer if not pd.isna(cer) else pd.NaT,
            "DET_END_TS": det_end_ts,
            "DD_ANCHOR_DATE": cdd if not pd.isna(cdd) else (cgo if not pd.isna(cgo) else (cer if not pd.isna(cer) else (cll if not pd.isna(cll) else cgi))),
            "POL_DEM_TOTAL_DAYS": round(pol_dem_total_days, 2) if pol_dem_total_days is not None else None,
            "POD_DEM_TOTAL_DAYS": round(pod_dem_total_days, 2) if pod_dem_total_days is not None else None,
            "POD_DET_TOTAL_DAYS": round(pod_det_total_days, 2) if pod_det_total_days is not None else None,
            "DET_ACCUMULATING": det_accumulating,
            "DET_END_SOURCE": det_end_source,
            "LANE": f"{row['POL_LOCODE']} → {row['POD_LOCODE']}",
            "MATCH_KEY": key,
        }

        # In contract mode, no profile means the shipment is a contract gap. In estimate mode, all shipments can be priced.
        if contract_profile is None:
            reason_parts = []
            if pd.isna(cgi) or pd.isna(cll):
                reason_parts.append("Cannot evaluate POL demurrage; missing CGI or CLL")
            if pd.isna(cdd) or pd.isna(cgo):
                reason_parts.append("Cannot evaluate POD demurrage; missing CDD or CGO")
            if pd.isna(cgo):
                reason_parts.append("Cannot evaluate POD detention; missing CGO")
            if pd.isna(cer) and sub_status not in ["ACTIVE", "COMPLETED"]:
                reason_parts.append("Cannot evaluate POD detention end; missing CER and status not ACTIVE/COMPLETED")

            unmatched_record = base_record.copy()
            unmatched_record.update(
                {
                    "MISSING_CONTRACT_REASON": "No contract for POD | Carrier/FFW | POL",
                    "DATA_LIMITATION": "; ".join(reason_parts) if reason_parts else "Dwell days available; fees cannot be calculated without contract",
                    "RISK_FLAG": False,
                    "RISK_REASONS": "",
                }
            )
            unmatched_results.append(unmatched_record)
            continue

        pol_dem_contract = contract_profile.get("pol_dem")
        pod_dem_contract = contract_profile.get("pod_dem")
        pod_det_contract = contract_profile.get("pod_det")

        combined_free = _get_combined_pod_free_days(pod_dem_contract, pod_det_contract)
        has_combined = combined_free is not None

        # POL demurrage: separate from destination combined free days.
        pol_dem_chargeable = 0.0
        pol_dem_breakdown = calc_tiered_cost_breakdown(0, 0, 0, 0, 0, 0)
        if pol_dem_contract is not None and pol_dem_total_days is not None:
            pol_dem_chargeable = max(0, pol_dem_total_days - _safe(pol_dem_contract.get("freeDemurrageDays")))
            pol_dem_breakdown = calc_tiered_cost_breakdown(
                pol_dem_chargeable,
                pol_dem_contract.get("firstDemurrageDays"),
                pol_dem_contract.get("firstDemurrageRate"),
                pol_dem_contract.get("secondDemurrageDays"),
                pol_dem_contract.get("secondDemurrageRate"),
                pol_dem_contract.get("thereafterDemurrageRate"),
            )

        # POD demurrage: can consume combined POD free-day pool first.
        pod_dem_chargeable = 0.0
        remaining_free_for_det = 0.0
        pod_dem_breakdown = calc_tiered_cost_breakdown(0, 0, 0, 0, 0, 0)
        if pod_dem_contract is not None and pod_dem_total_days is not None:
            if has_combined:
                pod_dem_chargeable = max(0, pod_dem_total_days - combined_free)
                remaining_free_for_det = max(0, combined_free - pod_dem_total_days)
            else:
                pod_dem_chargeable = max(0, pod_dem_total_days - _safe(pod_dem_contract.get("freeDemurrageDays")))

            pod_dem_breakdown = calc_tiered_cost_breakdown(
                pod_dem_chargeable,
                pod_dem_contract.get("firstDemurrageDays"),
                pod_dem_contract.get("firstDemurrageRate"),
                pod_dem_contract.get("secondDemurrageDays"),
                pod_dem_contract.get("secondDemurrageRate"),
                pod_dem_contract.get("thereafterDemurrageRate"),
            )

        # POD detention: receives remaining combined free days, or its own detention free days.
        pod_det_chargeable = 0.0
        pod_det_breakdown = calc_tiered_cost_breakdown(0, 0, 0, 0, 0, 0)
        if pod_det_contract is not None and pod_det_total_days is not None:
            if has_combined:
                pod_det_chargeable = max(0, pod_det_total_days - remaining_free_for_det)
            else:
                pod_det_chargeable = max(0, pod_det_total_days - _safe(pod_det_contract.get("freeDetentionDays")))

            pod_det_breakdown = calc_tiered_cost_breakdown(
                pod_det_chargeable,
                pod_det_contract.get("firstDetentionDays"),
                pod_det_contract.get("firstDetentionRate"),
                pod_det_contract.get("secondDetentionDays"),
                pod_det_contract.get("secondDetentionRate"),
                pod_det_contract.get("thereafterDetentionRate"),
            )

        pol_dem_cost = pol_dem_breakdown["total_cost"]
        pod_dem_cost = pod_dem_breakdown["total_cost"]
        pod_det_cost = pod_det_breakdown["total_cost"]
        total_cost = round(pol_dem_cost + pod_dem_cost + pod_det_cost, 2)

        matched_record = base_record.copy()
        matched_record.update(
            {
                "RATE_SOURCE": "Estimate" if use_estimate else "Contract",
                "POL_DEM_CHARGEABLE_DAYS": round(pol_dem_chargeable, 2),
                "POL_DEM_COST": pol_dem_cost,
                "POD_DEM_CHARGEABLE_DAYS": round(pod_dem_chargeable, 2),
                "POD_DEM_COST": pod_dem_cost,
                "POD_DET_CHARGEABLE_DAYS": round(pod_det_chargeable, 2),
                "POD_DET_COST": pod_det_cost,
                "DEM_COST": round(pol_dem_cost + pod_dem_cost, 2),
                "DET_COST": pod_det_cost,
                "TOTAL_DD_COST": total_cost,
                "POL_FREE_DEM_DAYS": _safe(pol_dem_contract.get("freeDemurrageDays"), None) if pol_dem_contract else None,
                "POD_FREE_DEM_DAYS": _safe(pod_dem_contract.get("freeDemurrageDays"), None) if pod_dem_contract else None,
                "FREE_DEM_DAYS": _safe(pod_dem_contract.get("freeDemurrageDays"), None) if pod_dem_contract else None,
                "FREE_DET_DAYS": _safe(pod_det_contract.get("freeDetentionDays"), None) if pod_det_contract else None,
                "COMBINED_FREE_DAYS": combined_free,
                "CONTRACT_TYPE": "Estimate" if use_estimate else ("Combined" if has_combined else "Separate"),
                "POL_DEM_CONTRACT_STATUS": _contract_label(contract_profile, "pol_dem"),
                "POD_DEM_CONTRACT_STATUS": _contract_label(contract_profile, "pod_dem"),
                "POD_DET_CONTRACT_STATUS": _contract_label(contract_profile, "pod_det"),
                "CONTRACT_IDENTIFIER": _contract_label(contract_profile, "pod_dem"),
                "CONTRACT_POL": str((pod_dem_contract or pod_det_contract or pol_dem_contract or {}).get("portOfLoadingLocode", "")),
                "POL_DEM_TIER1_DAYS": round(pol_dem_breakdown["tier1_days"], 2),
                "POL_DEM_TIER1_COST": round(pol_dem_breakdown["tier1_cost"], 2),
                "POL_DEM_TIER2_DAYS": round(pol_dem_breakdown["tier2_days"], 2),
                "POL_DEM_TIER2_COST": round(pol_dem_breakdown["tier2_cost"], 2),
                "POL_DEM_THEREAFTER_DAYS": round(pol_dem_breakdown["thereafter_days"], 2),
                "POL_DEM_THEREAFTER_COST": round(pol_dem_breakdown["thereafter_cost"], 2),
                "POD_DEM_TIER1_DAYS": round(pod_dem_breakdown["tier1_days"], 2),
                "POD_DEM_TIER1_COST": round(pod_dem_breakdown["tier1_cost"], 2),
                "POD_DEM_TIER2_DAYS": round(pod_dem_breakdown["tier2_days"], 2),
                "POD_DEM_TIER2_COST": round(pod_dem_breakdown["tier2_cost"], 2),
                "POD_DEM_THEREAFTER_DAYS": round(pod_dem_breakdown["thereafter_days"], 2),
                "POD_DEM_THEREAFTER_COST": round(pod_dem_breakdown["thereafter_cost"], 2),
                "POD_DET_TIER1_DAYS": round(pod_det_breakdown["tier1_days"], 2),
                "POD_DET_TIER1_COST": round(pod_det_breakdown["tier1_cost"], 2),
                "POD_DET_TIER2_DAYS": round(pod_det_breakdown["tier2_days"], 2),
                "POD_DET_TIER2_COST": round(pod_det_breakdown["tier2_cost"], 2),
                "POD_DET_THEREAFTER_DAYS": round(pod_det_breakdown["thereafter_days"], 2),
                "POD_DET_THEREAFTER_COST": round(pod_det_breakdown["thereafter_cost"], 2),
            }
        )
        matched_results.append(matched_record)

    matched_df = pd.DataFrame(matched_results)
    unmatched_df = pd.DataFrame(unmatched_results)

    unmatched_df = enrich_unmatched_risk(unmatched_df, matched_df)

    return matched_df, unmatched_df, original_count, cancelled_count


def enrich_unmatched_risk(unmatched_df, matched_df):
    if unmatched_df.empty:
        return unmatched_df

    # Use matched shipments as the benchmark. Prefer non-zero dwell days so the threshold is operationally meaningful.
    def positive_mean(df, col):
        if df.empty or col not in df.columns:
            return np.nan
        s = pd.to_numeric(df[col], errors="coerce")
        s = s[s > 0]
        return s.mean() if len(s) else np.nan

    global_avg_pol_dem = positive_mean(matched_df, "POL_DEM_TOTAL_DAYS")
    global_avg_pod_dem = positive_mean(matched_df, "POD_DEM_TOTAL_DAYS")
    global_avg_pod_det = positive_mean(matched_df, "POD_DET_TOTAL_DAYS")

    # Fallback thresholds to prevent every small dwell from being flagged when matched data is sparse.
    if np.isnan(global_avg_pol_dem):
        global_avg_pol_dem = 3.0
    if np.isnan(global_avg_pod_dem):
        global_avg_pod_dem = 3.0
    if np.isnan(global_avg_pod_det):
        global_avg_pod_det = 5.0

    unmatched_df["AVG_POL_DEM_BENCHMARK"] = round(global_avg_pol_dem, 2)
    unmatched_df["AVG_POD_DEM_BENCHMARK"] = round(global_avg_pod_dem, 2)
    unmatched_df["AVG_POD_DET_BENCHMARK"] = round(global_avg_pod_det, 2)

    risk_flags = []
    risk_reasons = []
    risk_score = []

    for _, row in unmatched_df.iterrows():
        reasons = []
        score = 0

        pol_days = row.get("POL_DEM_TOTAL_DAYS")
        pod_dem_days = row.get("POD_DEM_TOTAL_DAYS")
        pod_det_days = row.get("POD_DET_TOTAL_DAYS")

        if pd.notna(pol_days) and pol_days > global_avg_pol_dem:
            reasons.append(f"POL demurrage {pol_days:.1f}d vs avg {global_avg_pol_dem:.1f}d")
            score += 1
        if pd.notna(pod_dem_days) and pod_dem_days > global_avg_pod_dem:
            reasons.append(f"POD demurrage {pod_dem_days:.1f}d vs avg {global_avg_pod_dem:.1f}d")
            score += 1
        if pd.notna(pod_det_days) and pod_det_days > global_avg_pod_det:
            reasons.append(f"POD detention {pod_det_days:.1f}d vs avg {global_avg_pod_det:.1f}d")
            score += 1
        if bool(row.get("DET_ACCUMULATING")):
            reasons.append("ACTIVE with no CER; POD detention still accumulating")
            score += 1

        risk_flags.append(score > 0)
        risk_score.append(score)
        risk_reasons.append("; ".join(reasons) if reasons else "No above-average dwell risk detected")

    unmatched_df["RISK_FLAG"] = risk_flags
    unmatched_df["RISK_SCORE"] = risk_score
    unmatched_df["RISK_REASONS"] = risk_reasons
    return unmatched_df


# -----------------------------------------------------------------------------
# DOWNLOAD HELPERS
# -----------------------------------------------------------------------------
def format_datetime_cols(dl, cols):
    for col in cols:
        if col in dl.columns:
            dl[col] = pd.to_datetime(dl[col], errors="coerce").dt.strftime("%Y-%m-%d %H:%M").fillna("")
    return dl


def build_download_df(data):
    dl = data.copy()
    dl = format_datetime_cols(dl, ["CGI", "CLL", "CDD", "CGO", "CER", "DET_END_TS"])

    rename_map = {
        "SHIPMENT_ID": "Shipment ID",
        "CONTAINER_NUMBER": "Container",
        "CARRIER_SCAC": "Carrier SCAC",
        "CARRIER_NAME": "Carrier Name",
        "FFW_SCAC": "Freight Forwarder SCAC",
        "CARRIER_FFW_SCAC": "Carrier / FFW SCAC",
        "MATCHED_PARTY_TYPE": "Matched Party Type",
        "FFW_SCAC": "Freight Forwarder SCAC",
        "CARRIER_FFW_SCAC": "Carrier / FFW SCAC",
        "MATCHED_PARTY_TYPE": "Matched Party Type",
        "POL_LOCODE": "Port of Loading",
        "POD_LOCODE": "Port of Discharge",
        "LANE": "Lane",
        "SUBSCRIPTION_STATUS": "Subscription Status",
        "CGI": "Gate In at POL (CGI)",
        "CLL": "Loaded on Vessel (CLL)",
        "CDD": "Discharge at POD (CDD)",
        "CGO": "Gate Out Full at POD (CGO)",
        "CER": "Empty Return (CER)",
        "POL_DEM_TOTAL_DAYS": "POL Demurrage Total Days",
        "POL_DEM_CHARGEABLE_DAYS": "POL Demurrage Chargeable Days",
        "POL_DEM_COST": "POL Demurrage Cost (USD)",
        "POD_DEM_TOTAL_DAYS": "POD Demurrage Total Days",
        "POD_DEM_CHARGEABLE_DAYS": "POD Demurrage Chargeable Days",
        "POD_DEM_COST": "POD Demurrage Cost (USD)",
        "POD_DET_TOTAL_DAYS": "POD Detention Total Days",
        "POD_DET_CHARGEABLE_DAYS": "POD Detention Chargeable Days",
        "POD_DET_COST": "POD Detention Cost (USD)",
        "DEM_COST": "Total Demurrage Cost (USD)",
        "DET_COST": "Total Detention Cost (USD)",
        "TOTAL_DD_COST": "Total D&D Cost (USD)",
        "CONTRACT_TYPE": "Free Days Type",
        "FREE_DEM_DAYS": "Free Demurrage Days",
        "FREE_DET_DAYS": "Free Detention Days",
        "COMBINED_FREE_DAYS": "Combined Free Days",
        "DET_ACCUMULATING": "Detention Still Accumulating",
        "DET_END_SOURCE": "Detention End Date Source",
        "MATCH_KEY": "Contract Match Key",
    }
    dl = dl.rename(columns={k: v for k, v in rename_map.items() if k in dl.columns})

    drop_cols = [c for c in ["POL", "POD", "LIFECYCLE_STATUS"] if c in dl.columns]
    dl = dl.drop(columns=drop_cols, errors="ignore")

    desired_order = [
        "Shipment ID",
        "Container",
        "Carrier SCAC",
        "Carrier Name",
        "Freight Forwarder SCAC",
        "Carrier / FFW SCAC",
        "Matched Party Type",
        "Lane",
        "Port of Loading",
        "Port of Discharge",
        "Subscription Status",
        "Gate In at POL (CGI)",
        "Loaded on Vessel (CLL)",
        "Discharge at POD (CDD)",
        "Gate Out Full at POD (CGO)",
        "Empty Return (CER)",
        "Free Days Type",
        "Free Demurrage Days",
        "Free Detention Days",
        "Combined Free Days",
        "POL Demurrage Total Days",
        "POL Demurrage Chargeable Days",
        "POL Demurrage Cost (USD)",
        "POD Demurrage Total Days",
        "POD Demurrage Chargeable Days",
        "POD Demurrage Cost (USD)",
        "POD Detention Total Days",
        "POD Detention Chargeable Days",
        "POD Detention Cost (USD)",
        "Total Demurrage Cost (USD)",
        "Total Detention Cost (USD)",
        "Total D&D Cost (USD)",
        "Detention Still Accumulating",
        "Detention End Date Source",
        "Contract Match Key",
    ]
    existing = [c for c in desired_order if c in dl.columns]
    remaining = [c for c in dl.columns if c not in existing]
    return dl[existing + remaining]


def build_unmatched_download_df(data):
    if data.empty:
        return data.copy()

    dl = data.copy()
    dl = format_datetime_cols(dl, ["CGI", "CLL", "CDD", "CGO", "CER", "DET_END_TS"])
    rename_map = {
        "SHIPMENT_ID": "Shipment ID",
        "CONTAINER_NUMBER": "Container",
        "CARRIER_SCAC": "Carrier SCAC",
        "CARRIER_NAME": "Carrier Name",
        "POL_LOCODE": "Port of Loading",
        "POD_LOCODE": "Port of Discharge",
        "LANE": "Lane",
        "SUBSCRIPTION_STATUS": "Subscription Status",
        "CGI": "Gate In at POL (CGI)",
        "CLL": "Loaded on Vessel (CLL)",
        "CDD": "Discharge at POD (CDD)",
        "CGO": "Gate Out Full at POD (CGO)",
        "CER": "Empty Return (CER)",
        "POL_DEM_TOTAL_DAYS": "POL Demurrage Days",
        "POD_DEM_TOTAL_DAYS": "POD Demurrage Days",
        "POD_DET_TOTAL_DAYS": "POD Detention Days",
        "MATCH_KEY": "Missing Contract Key",
        "RISK_FLAG": "Risk Flag",
        "RISK_SCORE": "Risk Score",
        "RISK_REASONS": "Risk Reasons",
        "DATA_LIMITATION": "Data Limitation",
    }
    dl = dl.rename(columns={k: v for k, v in rename_map.items() if k in dl.columns})
    drop_cols = [c for c in ["POL", "POD", "LIFECYCLE_STATUS"] if c in dl.columns]
    dl = dl.drop(columns=drop_cols, errors="ignore")
    return dl


# -----------------------------------------------------------------------------
# SIDEBAR
# -----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### 🚢 Demurrage & Detention Analyzer")
    st.markdown("---")

    rate_source = st.radio(
        "Rate Source",
        ["Upload Contract CSV", "Estimate Rates"],
        help="Use uploaded contract terms when available, or estimate D&D exposure from manually entered tariff assumptions.",
    )

    uploaded_contract_file = None
    estimate_profile = None

    if rate_source == "Upload Contract CSV":
        uploaded_contract_file = st.file_uploader(
            "Upload Contract CSV",
            type=["csv"],
            help="Upload the D&D contract terms CSV. Demurrage rows are classified using demurrageStartEventType containing POL or POD.",
            key="contract_uploader",
        )
    else:
        st.markdown("#### Estimate Tariffs")
        st.caption("All rates are USD/day. POL demurrage is always separate. Combined free days only applies to POD demurrage + POD detention.")

        with st.expander("POL Demurrage Estimate", expanded=True):
            pol_free_dem = st.number_input("POL free demurrage days", min_value=0.0, value=0.0, step=1.0)
            pol_t1_days = st.number_input("POL Tier 1 days", min_value=0.0, value=0.0, step=1.0)
            pol_t1_rate = st.number_input("POL Tier 1 rate (USD/day)", min_value=0.0, value=0.0, step=25.0)
            pol_t2_days = st.number_input("POL Tier 2 days", min_value=0.0, value=0.0, step=1.0)
            pol_t2_rate = st.number_input("POL Tier 2 rate (USD/day)", min_value=0.0, value=0.0, step=25.0)
            pol_thereafter_rate = st.number_input("POL thereafter rate (USD/day)", min_value=0.0, value=0.0, step=25.0)

        with st.expander("POD Free Days", expanded=True):
            use_combined_pod_free = st.checkbox(
                "Use combined POD free days for POD demurrage + POD detention",
                value=False,
                help="When enabled, POD demurrage consumes the combined free-day pool first; POD detention receives any remaining free days. POL demurrage is not included.",
            )
            if use_combined_pod_free:
                combined_pod_free_days = st.number_input("Combined POD free days", min_value=0.0, value=0.0, step=1.0)
                pod_free_dem = 0.0
                pod_free_det = 0.0
            else:
                combined_pod_free_days = None
                pod_free_dem = st.number_input("POD free demurrage days", min_value=0.0, value=0.0, step=1.0)
                pod_free_det = st.number_input("POD free detention days", min_value=0.0, value=0.0, step=1.0)

        with st.expander("POD Demurrage Estimate", expanded=True):
            pod_t1_days = st.number_input("POD demurrage Tier 1 days", min_value=0.0, value=0.0, step=1.0)
            pod_t1_rate = st.number_input("POD demurrage Tier 1 rate (USD/day)", min_value=0.0, value=0.0, step=25.0)
            pod_t2_days = st.number_input("POD demurrage Tier 2 days", min_value=0.0, value=0.0, step=1.0)
            pod_t2_rate = st.number_input("POD demurrage Tier 2 rate (USD/day)", min_value=0.0, value=0.0, step=25.0)
            pod_thereafter_rate = st.number_input("POD demurrage thereafter rate (USD/day)", min_value=0.0, value=0.0, step=25.0)

        with st.expander("POD Detention Estimate", expanded=True):
            det_t1_days = st.number_input("POD detention Tier 1 days", min_value=0.0, value=0.0, step=1.0)
            det_t1_rate = st.number_input("POD detention Tier 1 rate (USD/day)", min_value=0.0, value=0.0, step=25.0)
            det_t2_days = st.number_input("POD detention Tier 2 days", min_value=0.0, value=0.0, step=1.0)
            det_t2_rate = st.number_input("POD detention Tier 2 rate (USD/day)", min_value=0.0, value=0.0, step=25.0)
            det_thereafter_rate = st.number_input("POD detention thereafter rate (USD/day)", min_value=0.0, value=0.0, step=25.0)

        estimate_profile = make_estimate_contract_profile(
            pol_free_dem,
            pol_t1_days,
            pol_t1_rate,
            pol_t2_days,
            pol_t2_rate,
            pol_thereafter_rate,
            use_combined_pod_free,
            combined_pod_free_days,
            pod_free_dem,
            pod_t1_days,
            pod_t1_rate,
            pod_t2_days,
            pod_t2_rate,
            pod_thereafter_rate,
            pod_free_det,
            det_t1_days,
            det_t1_rate,
            det_t2_days,
            det_t2_rate,
            det_thereafter_rate,
        )

    uploaded_file = st.file_uploader(
        "Upload Shipment CSV",
        type=["csv"],
        help="Upload the ocean shipment export CSV with milestone events.",
        key="shipment_uploader",
    )
    st.markdown("---")

contracts_list = None
contracts_df = None
if rate_source == "Upload Contract CSV" and uploaded_contract_file is not None:
    try:
        contracts_list, contracts_df = parse_contracts_csv(uploaded_contract_file)
        with st.sidebar:
            st.success(f"✅ Loaded {len(contracts_list)} contract rows")
            terminals = sorted(set(c.get("terminalIdentifier", "") for c in contracts_list if c.get("terminalIdentifier")))
            carrier_scacs = sorted(set(c.get("carrierScac", "") for c in contracts_list if c.get("carrierScac")))
            ffw_scacs = sorted(set(c.get("ffwScac", "") for c in contracts_list if c.get("ffwScac")))
            pol_dem_rows = sum(1 for c in contracts_list if _event_scope(c.get("demurrageStartEventType")) == "POL" and _has_demurrage_terms(c))
            pod_dem_rows = sum(1 for c in contracts_list if _event_scope(c.get("demurrageStartEventType")) == "POD" and _has_demurrage_terms(c))
            det_rows = sum(1 for c in contracts_list if _has_detention_terms(c))
            st.markdown(f"**Contract rows:** {len(contracts_list)}")
            st.markdown(f"**POL dem rows:** {pol_dem_rows:,} | **POD dem rows:** {pod_dem_rows:,} | **Det rows:** {det_rows:,}")
            st.markdown(f"**Terminals:** {', '.join(terminals) if terminals else '—'}")
            carriers_display = carrier_scacs.copy()
            if ffw_scacs:
                carriers_display += [f"{s} (FFW)" for s in ffw_scacs]
            st.markdown(f"**Carriers/FFWs:** {', '.join(carriers_display) if carriers_display else '—'}")
    except Exception as e:
        st.sidebar.error(f"❌ Error parsing contract CSV: {e}")
        contracts_list = None

# -----------------------------------------------------------------------------
# LANDING PAGE
# -----------------------------------------------------------------------------
missing_contract = rate_source == "Upload Contract CSV" and uploaded_contract_file is None
missing_shipments = uploaded_file is None
if missing_contract or missing_shipments:
    st.markdown("## 🚢 Demurrage & Detention Analyzer")
    st.markdown("---")

    if missing_contract and missing_shipments:
        st.info("Upload both a **Contract CSV** and a **Shipment CSV** from the sidebar to get started, or switch Rate Source to **Estimate Rates**.")
    elif missing_contract:
        st.info("Upload a **Contract CSV** from the sidebar, or switch Rate Source to **Estimate Rates**.")
    elif missing_shipments:
        st.info("Upload a **Shipment CSV** from the sidebar to continue.")

    st.markdown("**Contract CSV behavior:**")
    st.code(
        "demurrageStartEventType contains POL -> used for POL demurrage (CGI to CLL)\n"
        "demurrageStartEventType contains POD -> used for POD demurrage (CDD to CGO)\n"
        "detention fields -> used for POD detention (CGO to CER)\n"
        "missing / blank tariff fields -> treated as 0 / not configured",
        language=None,
    )

    st.markdown("**Expected shipment milestone columns:**")
    st.code(
        "CEP → CGI → CLL → VDL → VAD → CDD → CGO → CER\n\n"
        "POL Demurrage = CGI → CLL\n"
        "POD Demurrage = CDD → CGO\n"
        "POD Detention = CGO → CER",
        language=None,
    )
    st.stop()

if rate_source == "Upload Contract CSV" and contracts_list is None:
    st.error("Contract file could not be parsed. Please check the format and re-upload, or switch to Estimate Rates.")
    st.stop()

# -----------------------------------------------------------------------------
# LOAD AND PROCESS
# -----------------------------------------------------------------------------
process_label = "estimate rates" if rate_source == "Estimate Rates" else "uploaded contracts"
with st.spinner(f"Processing shipments against {process_label}..."):
    raw_df = pd.read_csv(uploaded_file)
    rdf, unmatched_df, total_shipments, cancelled_count = process_shipments(
        raw_df,
        contracts_list=contracts_list,
        estimate_profile=estimate_profile,
        use_estimate=(rate_source == "Estimate Rates"),
    )

if rdf.empty and unmatched_df.empty:
    st.error("No usable shipments found after excluding cancelled shipments.")
    st.stop()

# -----------------------------------------------------------------------------
# SIDEBAR FILTERS
# -----------------------------------------------------------------------------
# Ensure the trend/date anchor exists for older processed rows.
for _df in [rdf, unmatched_df]:
    if not _df.empty:
        if "DD_ANCHOR_DATE" not in _df.columns:
            _df["DD_ANCHOR_DATE"] = pd.NaT
        _df["DD_ANCHOR_DATE"] = pd.to_datetime(_df["DD_ANCHOR_DATE"], errors="coerce", utc=True)

with st.sidebar:
    st.markdown("---")
    st.markdown("### Filters")

    combined_for_filters = pd.concat(
        [
            rdf[["CARRIER_SCAC", "FFW_SCAC", "CARRIER_FFW_SCAC", "POD_LOCODE", "POL_LOCODE", "DD_ANCHOR_DATE"]] if not rdf.empty else pd.DataFrame(),
            unmatched_df[["CARRIER_SCAC", "FFW_SCAC", "CARRIER_FFW_SCAC", "POD_LOCODE", "POL_LOCODE", "DD_ANCHOR_DATE"]] if not unmatched_df.empty else pd.DataFrame(),
        ],
        ignore_index=True,
    )

    carriers = sorted(combined_for_filters.get("CARRIER_FFW_SCAC", pd.Series(dtype=str)).dropna().astype(str).unique())
    pods = sorted(combined_for_filters.get("POD_LOCODE", pd.Series(dtype=str)).dropna().astype(str).unique())
    pols = sorted(combined_for_filters.get("POL_LOCODE", pd.Series(dtype=str)).dropna().astype(str).unique())

    sel_carriers = st.multiselect("Carrier / FFW", carriers, default=carriers)
    sel_pods = st.multiselect("POD Terminal", pods, default=pods)
    sel_pols = st.multiselect("POL", pols, default=pols)
    show_zero = st.checkbox("Include $0 charge shipments", value=True)

    st.markdown("### Time Filters")
    trend_grain = st.radio("Trend View", ["Weekly", "Monthly"], horizontal=True)

    valid_dates = combined_for_filters["DD_ANCHOR_DATE"].dropna() if "DD_ANCHOR_DATE" in combined_for_filters.columns else pd.Series(dtype="datetime64[ns, UTC]")
    if not valid_dates.empty:
        min_date = valid_dates.min().date()
        max_date = valid_dates.max().date()
        date_range = st.date_input(
            "D&D Date Range",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )
    else:
        date_range = None
        st.caption("No valid D&D anchor dates found for date filtering.")

def apply_common_filters(data, require_cost_filter=False):
    if data.empty:
        return data.copy()
    out = data.copy()
    if sel_carriers and "CARRIER_FFW_SCAC" in out.columns:
        out = out[out["CARRIER_FFW_SCAC"].astype(str).isin(sel_carriers)]
    if sel_pods:
        out = out[out["POD_LOCODE"].astype(str).isin(sel_pods)]
    if sel_pols:
        out = out[out["POL_LOCODE"].astype(str).isin(sel_pols)]
    if date_range and len(date_range) == 2 and "DD_ANCHOR_DATE" in out.columns:
        start_date, end_date = date_range
        anchor = pd.to_datetime(out["DD_ANCHOR_DATE"], errors="coerce", utc=True)
        out = out[(anchor.dt.date >= start_date) & (anchor.dt.date <= end_date)]
    if require_cost_filter and not show_zero and "TOTAL_DD_COST" in out.columns:
        out = out[out["TOTAL_DD_COST"] > 0]
    return out

fdf = apply_common_filters(rdf, require_cost_filter=True) if not rdf.empty else rdf.copy()
ufdf = apply_common_filters(unmatched_df, require_cost_filter=False) if not unmatched_df.empty else unmatched_df.copy()

# -----------------------------------------------------------------------------
# TABS
# -----------------------------------------------------------------------------
tab_overview, tab_trends, tab_carrier, tab_port, tab_ships, tab_gaps, tab_tiers, tab_download = st.tabs(
    [
        "📊 Overview",
        "📈 Trends",
        "🚛 Carrier / FFW",
        "🏗️ Ports & Lanes",
        "📦 Shipments",
        "⚠️ Contract Gaps",
        "🔥 Tier Exposure",
        "📥 Download",
    ]
)

# -----------------------------------------------------------------------------
# OVERVIEW
# -----------------------------------------------------------------------------
with tab_overview:
    st.markdown("### Executive Summary")

    if fdf.empty:
        st.warning("No matched shipments available for the selected filters.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(
            "Total D&D Cost",
            f"${fdf['TOTAL_DD_COST'].sum():,.0f}",
            f"{len(fdf)} matched of {total_shipments:,}",
            help="Combined POL demurrage + POD demurrage + POD detention charges across matched shipments.",
        )
        c2.metric(
            "POL Demurrage",
            f"${fdf['POL_DEM_COST'].sum():,.0f}",
            f"{(fdf['POL_DEM_COST'] > 0).sum()} shipments",
            help="Cost from container gate-in at port of loading to loaded-on-vessel.",
        )
        c3.metric(
            "POD Demurrage",
            f"${fdf['POD_DEM_COST'].sum():,.0f}",
            f"{(fdf['POD_DEM_COST'] > 0).sum()} shipments",
            help="Cost from container discharge at POD to gate-out-full at POD.",
        )
        c4.metric(
            "POD Detention",
            f"${fdf['POD_DET_COST'].sum():,.0f}",
            f"{(fdf['POD_DET_COST'] > 0).sum()} shipments",
            help="Cost from gate-out-full at POD to empty container return.",
        )

        c1, c2, c3, c4 = st.columns(4)
        avg_pol_dem = fdf.loc[fdf["POL_DEM_COST"] > 0, "POL_DEM_CHARGEABLE_DAYS"].mean()
        avg_pod_dem = fdf.loc[fdf["POD_DEM_COST"] > 0, "POD_DEM_CHARGEABLE_DAYS"].mean()
        avg_det = fdf.loc[fdf["POD_DET_COST"] > 0, "POD_DET_CHARGEABLE_DAYS"].mean()
        c1.metric("Avg POL Dem Days", f"{avg_pol_dem:.1f}d" if not np.isnan(avg_pol_dem) else "—")
        c2.metric("Avg POD Dem Days", f"{avg_pod_dem:.1f}d" if not np.isnan(avg_pod_dem) else "—")
        c3.metric("Avg POD Det Days", f"{avg_det:.1f}d" if not np.isnan(avg_det) else "—")
        c4.metric(
            "⚠️ Accumulating",
            f"{fdf['DET_ACCUMULATING'].sum()}",
            "ACTIVE, no CER",
            help="Active shipments with no empty return. Detention is calculated up to today's date and keeps growing.",
        )

        if cancelled_count > 0:
            st.caption(f"ℹ️ {cancelled_count} cancelled shipments excluded from analysis.")

        st.markdown("---")
        st.caption("💡 Cost split by carrier / freight forwarder. Blue = POL demurrage, orange = POD demurrage, purple = POD detention.")

        carrier_agg = (
            fdf.groupby("CARRIER_FFW_SCAC")
            .agg(
                POL_Demurrage=("POL_DEM_COST", "sum"),
                POD_Demurrage=("POD_DEM_COST", "sum"),
                POD_Detention=("POD_DET_COST", "sum"),
            )
            .reset_index()
        )
        carrier_melt = carrier_agg.melt(id_vars="CARRIER_FFW_SCAC", var_name="Type", value_name="Cost")
        carrier_melt["Type"] = carrier_melt["Type"].replace(
            {
                "POL_Demurrage": "POL Demurrage",
                "POD_Demurrage": "POD Demurrage",
                "POD_Detention": "POD Detention",
            }
        )

        chart_carrier = (
            alt.Chart(carrier_melt)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                y=alt.Y("CARRIER_FFW_SCAC:N", sort="-x", title="Carrier / FFW"),
                x=alt.X("Cost:Q", title="Cost (USD)"),
                color=alt.Color(
                    "Type:N",
                    scale=alt.Scale(
                        domain=["POL Demurrage", "POD Demurrage", "POD Detention"],
                        range=[POL_DEM_COLOR, DEM_COLOR, DET_COLOR],
                    ),
                ),
                tooltip=["CARRIER_FFW_SCAC", "Type", alt.Tooltip("Cost:Q", format="$,.0f")],
            )
            .properties(title="D&D Cost by Carrier / FFW", height=280)
        )
        st.altair_chart(chart_carrier, use_container_width=True)

        st.caption("💡 Cost split by POD terminal. High POD demurrage = pickup/terminal delay. High detention = empty return delay.")
        pod_agg = (
            fdf.groupby("POD_LOCODE")
            .agg(
                POL_Demurrage=("POL_DEM_COST", "sum"),
                POD_Demurrage=("POD_DEM_COST", "sum"),
                POD_Detention=("POD_DET_COST", "sum"),
            )
            .reset_index()
        )
        pod_melt = pod_agg.melt(id_vars="POD_LOCODE", var_name="Type", value_name="Cost")
        pod_melt["Type"] = pod_melt["Type"].replace(
            {
                "POL_Demurrage": "POL Demurrage",
                "POD_Demurrage": "POD Demurrage",
                "POD_Detention": "POD Detention",
            }
        )
        chart_pod = (
            alt.Chart(pod_melt)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                y=alt.Y("POD_LOCODE:N", sort="-x", title="POD Terminal"),
                x=alt.X("Cost:Q", title="Cost (USD)"),
                color=alt.Color(
                    "Type:N",
                    scale=alt.Scale(
                        domain=["POL Demurrage", "POD Demurrage", "POD Detention"],
                        range=[POL_DEM_COLOR, DEM_COLOR, DET_COLOR],
                    ),
                ),
                tooltip=["POD_LOCODE", "Type", alt.Tooltip("Cost:Q", format="$,.0f")],
            )
            .properties(title="D&D Cost by POD Terminal", height=250)
        )
        st.altair_chart(chart_pod, use_container_width=True)

# -----------------------------------------------------------------------------
# TRENDS
# -----------------------------------------------------------------------------
with tab_trends:
    st.markdown("### D&D Trends")
    st.caption(
        "Trend date uses CDD when available, then CGO, then CER, then CLL/CGI as fallback. "
        "Use the sidebar to switch between weekly and monthly views."
    )

    if fdf.empty:
        st.warning("No matched/priced shipments available for the selected filters.")
    else:
        trend_df = fdf.copy()
        trend_df["DD_ANCHOR_DATE"] = pd.to_datetime(trend_df["DD_ANCHOR_DATE"], errors="coerce", utc=True)
        trend_df = trend_df.dropna(subset=["DD_ANCHOR_DATE"])

        if trend_df.empty:
            st.warning("No valid D&D anchor dates found for the selected filters.")
        else:
            if trend_grain == "Weekly":
                trend_df["PERIOD"] = trend_df["DD_ANCHOR_DATE"].dt.to_period("W").apply(lambda r: r.start_time)
                period_title = "Week"
            else:
                trend_df["PERIOD"] = trend_df["DD_ANCHOR_DATE"].dt.to_period("M").apply(lambda r: r.start_time)
                period_title = "Month"

            trend_agg = (
                trend_df.groupby("PERIOD")
                .agg(
                    Shipments=("SHIPMENT_ID", "count"),
                    POL_Demurrage=("POL_DEM_COST", "sum"),
                    POD_Demurrage=("POD_DEM_COST", "sum"),
                    Detention=("POD_DET_COST", "sum"),
                    Total=("TOTAL_DD_COST", "sum"),
                    Avg_POL_Dem_Days=("POL_DEM_CHARGEABLE_DAYS", "mean"),
                    Avg_POD_Dem_Days=("POD_DEM_CHARGEABLE_DAYS", "mean"),
                    Avg_Det_Days=("POD_DET_CHARGEABLE_DAYS", "mean"),
                    Accumulating=("DET_ACCUMULATING", "sum"),
                )
                .reset_index()
                .sort_values("PERIOD")
            )

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Periods", f"{len(trend_agg):,}")
            c2.metric("Total Cost", f"${trend_agg['Total'].sum():,.0f}")
            c3.metric("Avg Cost / Shipment", f"${(trend_agg['Total'].sum() / max(trend_agg['Shipments'].sum(), 1)):,.0f}")
            c4.metric("Accumulating", f"{int(trend_agg['Accumulating'].sum()):,}", "ACTIVE, no CER")

            st.markdown("#### Cost Trend")
            cost_melt = trend_agg.melt(
                id_vars=["PERIOD"],
                value_vars=["POL_Demurrage", "POD_Demurrage", "Detention"],
                var_name="Charge Type",
                value_name="Cost",
            )
            cost_melt["Charge Type"] = cost_melt["Charge Type"].replace(
                {"POL_Demurrage": "POL Demurrage", "POD_Demurrage": "POD Demurrage"}
            )
            cost_chart = (
                alt.Chart(cost_melt)
                .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                .encode(
                    x=alt.X("PERIOD:T", title=period_title),
                    y=alt.Y("Cost:Q", title="Cost"),
                    color=alt.Color(
                        "Charge Type:N",
                        scale=alt.Scale(
                            domain=["POL Demurrage", "POD Demurrage", "Detention"],
                            range=[POL_DEM_COLOR, DEM_COLOR, DET_COLOR],
                        ),
                    ),
                    tooltip=[
                        alt.Tooltip("PERIOD:T", title=period_title),
                        "Charge Type:N",
                        alt.Tooltip("Cost:Q", format="$,.0f"),
                    ],
                )
                .properties(height=350)
            )
            st.altair_chart(cost_chart, use_container_width=True)

            col1, col2 = st.columns(2)
            with col1:
                st.markdown("#### Shipment Volume")
                shipment_chart = (
                    alt.Chart(trend_agg)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("PERIOD:T", title=period_title),
                        y=alt.Y("Shipments:Q", title="Shipments"),
                        tooltip=[alt.Tooltip("PERIOD:T", title=period_title), "Shipments"],
                    )
                    .properties(height=280)
                )
                st.altair_chart(shipment_chart, use_container_width=True)

            with col2:
                st.markdown("#### Avg Chargeable Days")
                days_melt = trend_agg.melt(
                    id_vars=["PERIOD"],
                    value_vars=["Avg_POL_Dem_Days", "Avg_POD_Dem_Days", "Avg_Det_Days"],
                    var_name="Metric",
                    value_name="Days",
                )
                days_melt["Metric"] = days_melt["Metric"].replace(
                    {
                        "Avg_POL_Dem_Days": "POL Demurrage",
                        "Avg_POD_Dem_Days": "POD Demurrage",
                        "Avg_Det_Days": "Detention",
                    }
                )
                days_chart = (
                    alt.Chart(days_melt)
                    .mark_line(point=True)
                    .encode(
                        x=alt.X("PERIOD:T", title=period_title),
                        y=alt.Y("Days:Q", title="Avg Chargeable Days"),
                        color=alt.Color(
                            "Metric:N",
                            scale=alt.Scale(
                                domain=["POL Demurrage", "POD Demurrage", "Detention"],
                                range=[POL_DEM_COLOR, DEM_COLOR, DET_COLOR],
                            ),
                        ),
                        tooltip=[
                            alt.Tooltip("PERIOD:T", title=period_title),
                            "Metric:N",
                            alt.Tooltip("Days:Q", format=".1f"),
                        ],
                    )
                    .properties(height=280)
                )
                st.altair_chart(days_chart, use_container_width=True)

            st.markdown("#### Trend Summary")
            st.dataframe(
                trend_agg.style.format(
                    {
                        "POL_Demurrage": "${:,.0f}",
                        "POD_Demurrage": "${:,.0f}",
                        "Detention": "${:,.0f}",
                        "Total": "${:,.0f}",
                        "Avg_POL_Dem_Days": "{:.1f}",
                        "Avg_POD_Dem_Days": "{:.1f}",
                        "Avg_Det_Days": "{:.1f}",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    st.markdown("---")
    st.markdown("### Contract Gap Trend")
    if ufdf.empty:
        st.info("No unmatched/contract-gap shipments for the selected filters.")
    else:
        gap_trend = ufdf.copy()
        gap_trend["DD_ANCHOR_DATE"] = pd.to_datetime(gap_trend["DD_ANCHOR_DATE"], errors="coerce", utc=True)
        gap_trend = gap_trend.dropna(subset=["DD_ANCHOR_DATE"])
        if gap_trend.empty:
            st.info("Contract-gap shipments do not have valid anchor dates for trend analysis.")
        else:
            if trend_grain == "Weekly":
                gap_trend["PERIOD"] = gap_trend["DD_ANCHOR_DATE"].dt.to_period("W").apply(lambda r: r.start_time)
                period_title = "Week"
            else:
                gap_trend["PERIOD"] = gap_trend["DD_ANCHOR_DATE"].dt.to_period("M").apply(lambda r: r.start_time)
                period_title = "Month"

            gap_agg = (
                gap_trend.groupby("PERIOD")
                .agg(
                    Unmatched_Shipments=("SHIPMENT_ID", "count"),
                    Risk_Shipments=("RISK_FLAG", "sum"),
                    Missing_Contract_Keys=("MATCH_KEY", "nunique"),
                    Avg_POL_Dem_Days=("POL_DEM_TOTAL_DAYS", "mean"),
                    Avg_POD_Dem_Days=("POD_DEM_TOTAL_DAYS", "mean"),
                    Avg_POD_Det_Days=("POD_DET_TOTAL_DAYS", "mean"),
                )
                .reset_index()
                .sort_values("PERIOD")
            )

            gap_chart = (
                alt.Chart(gap_agg)
                .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                .encode(
                    x=alt.X("PERIOD:T", title=period_title),
                    y=alt.Y("Unmatched_Shipments:Q", title="Unmatched Shipments"),
                    tooltip=[
                        alt.Tooltip("PERIOD:T", title=period_title),
                        "Unmatched_Shipments:Q",
                        "Risk_Shipments:Q",
                        "Missing_Contract_Keys:Q",
                    ],
                )
                .properties(height=260)
            )
            st.altair_chart(gap_chart, use_container_width=True)
            st.dataframe(
                gap_agg.style.format(
                    {
                        "Avg_POL_Dem_Days": "{:.1f}",
                        "Avg_POD_Dem_Days": "{:.1f}",
                        "Avg_POD_Det_Days": "{:.1f}",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

# -----------------------------------------------------------------------------
# CARRIERS
# -----------------------------------------------------------------------------
with tab_carrier:
    st.markdown("### Carrier / FFW Summary")

    if fdf.empty:
        st.warning("No matched shipments available for the selected filters.")
    else:
        carrier_detail = (
            fdf.groupby(["CARRIER_FFW_SCAC", "MATCHED_PARTY_TYPE", "CARRIER_SCAC", "FFW_SCAC", "CARRIER_NAME"])
            .agg(
                Ships=("SHIPMENT_ID", "count"),
                POL_Dem_Ships=("POL_DEM_COST", lambda x: (x > 0).sum()),
                POD_Dem_Ships=("POD_DEM_COST", lambda x: (x > 0).sum()),
                Det_Ships=("POD_DET_COST", lambda x: (x > 0).sum()),
                POL_Dem_Cost=("POL_DEM_COST", "sum"),
                POD_Dem_Cost=("POD_DEM_COST", "sum"),
                Det_Cost=("POD_DET_COST", "sum"),
                Avg_POL_Dem_Days=("POL_DEM_CHARGEABLE_DAYS", lambda x: x[x > 0].mean() if (x > 0).any() else 0),
                Avg_POD_Dem_Days=("POD_DEM_CHARGEABLE_DAYS", lambda x: x[x > 0].mean() if (x > 0).any() else 0),
                Avg_Det_Days=("POD_DET_CHARGEABLE_DAYS", lambda x: x[x > 0].mean() if (x > 0).any() else 0),
            )
            .reset_index()
        )
        carrier_detail["Total_Cost"] = (
            carrier_detail["POL_Dem_Cost"] + carrier_detail["POD_Dem_Cost"] + carrier_detail["Det_Cost"]
        )
        carrier_detail = carrier_detail.sort_values("Total_Cost", ascending=False)

        st.dataframe(
            carrier_detail.style.format(
                {
                    "POL_Dem_Cost": "${:,.0f}",
                    "POD_Dem_Cost": "${:,.0f}",
                    "Det_Cost": "${:,.0f}",
                    "Total_Cost": "${:,.0f}",
                    "Avg_POL_Dem_Days": "{:.1f}",
                    "Avg_POD_Dem_Days": "{:.1f}",
                    "Avg_Det_Days": "{:.1f}",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

        st.markdown("---")
        st.markdown("#### Carrier / FFW × POD Breakdown")
        cp = (
            fdf.groupby(["CARRIER_FFW_SCAC", "MATCHED_PARTY_TYPE", "POD_LOCODE"])
            .agg(
                Ships=("SHIPMENT_ID", "count"),
                POL_Dem=("POL_DEM_COST", "sum"),
                POD_Dem=("POD_DEM_COST", "sum"),
                Det=("POD_DET_COST", "sum"),
            )
            .reset_index()
        )
        cp["Total"] = cp["POL_Dem"] + cp["POD_Dem"] + cp["Det"]
        cp = cp[cp["Total"] > 0].sort_values("Total", ascending=False)

        if len(cp) > 0:
            heat = (
                alt.Chart(cp)
                .mark_rect(cornerRadius=4)
                .encode(
                    x=alt.X("POD_LOCODE:N", title="POD"),
                    y=alt.Y("CARRIER_FFW_SCAC:N", title="Carrier / FFW"),
                    color=alt.Color("Total:Q", scale=alt.Scale(scheme="oranges"), title="Total D&D"),
                    tooltip=[
                        "CARRIER_FFW_SCAC",
                        "MATCHED_PARTY_TYPE",
                        "POD_LOCODE",
                        "Ships",
                        alt.Tooltip("POL_Dem:Q", format="$,.0f"),
                        alt.Tooltip("POD_Dem:Q", format="$,.0f"),
                        alt.Tooltip("Det:Q", format="$,.0f"),
                        alt.Tooltip("Total:Q", format="$,.0f"),
                    ],
                )
                .properties(title="Cost Heatmap: Carrier / FFW × POD", height=280)
            )
            text = heat.mark_text(fontSize=11, fontWeight="bold").encode(
                text=alt.Text("Total:Q", format="$,.0f"),
                color=alt.condition(alt.datum.Total > cp["Total"].median(), alt.value("white"), alt.value("black")),
            )
            st.altair_chart(heat + text, use_container_width=True)

        st.dataframe(
            cp.style.format(
                {"POL_Dem": "${:,.0f}", "POD_Dem": "${:,.0f}", "Det": "${:,.0f}", "Total": "${:,.0f}"}
            ),
            use_container_width=True,
            hide_index=True,
        )

# -----------------------------------------------------------------------------
# PORTS & LANES
# -----------------------------------------------------------------------------
with tab_port:
    st.markdown("### Ports & Lanes")

    if fdf.empty:
        st.warning("No matched shipments available for the selected filters.")
    else:
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### POD Terminal Summary")
            pod_sum = (
                fdf.groupby(["POD_LOCODE", "POD"])
                .agg(
                    Ships=("SHIPMENT_ID", "count"),
                    POL_Dem=("POL_DEM_COST", "sum"),
                    POD_Dem=("POD_DEM_COST", "sum"),
                    Det=("POD_DET_COST", "sum"),
                )
                .reset_index()
            )
            pod_sum["Total"] = pod_sum["POL_Dem"] + pod_sum["POD_Dem"] + pod_sum["Det"]
            pod_sum = pod_sum.sort_values("Total", ascending=False)
            st.dataframe(
                pod_sum.style.format(
                    {"POL_Dem": "${:,.0f}", "POD_Dem": "${:,.0f}", "Det": "${:,.0f}", "Total": "${:,.0f}"}
                ),
                use_container_width=True,
                hide_index=True,
            )

        with col2:
            st.markdown("#### Cost Split by POD")
            pod_melt2 = pod_sum.melt(
                id_vars="POD_LOCODE",
                value_vars=["POL_Dem", "POD_Dem", "Det"],
                var_name="Type",
                value_name="Cost",
            )
            pod_melt2["Type"] = pod_melt2["Type"].replace(
                {"POL_Dem": "POL Demurrage", "POD_Dem": "POD Demurrage", "Det": "POD Detention"}
            )
            ch = (
                alt.Chart(pod_melt2)
                .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                .encode(
                    x=alt.X("POD_LOCODE:N", title="POD"),
                    y=alt.Y("Cost:Q", title="Cost (USD)", stack=True),
                    color=alt.Color(
                        "Type:N",
                        scale=alt.Scale(
                            domain=["POL Demurrage", "POD Demurrage", "POD Detention"],
                            range=[POL_DEM_COLOR, DEM_COLOR, DET_COLOR],
                        ),
                    ),
                    tooltip=["POD_LOCODE", "Type", alt.Tooltip("Cost:Q", format="$,.0f")],
                )
                .properties(height=320)
            )
            st.altair_chart(ch, use_container_width=True)

        st.markdown("---")
        st.markdown("#### Top 20 Lanes by Total D&D Cost")
        lane_agg = (
            fdf.groupby("LANE")
            .agg(
                Ships=("SHIPMENT_ID", "count"),
                Carrier_FFWs=("CARRIER_FFW_SCAC", lambda x: ", ".join(sorted(x.dropna().astype(str).unique()))),
                Carriers=("CARRIER_SCAC", lambda x: ", ".join(sorted([v for v in x.dropna().astype(str).unique() if v.strip()]))),
                Freight_Forwarders=("FFW_SCAC", lambda x: ", ".join(sorted([v for v in x.dropna().astype(str).unique() if v.strip()]))),
                POL_Dem=("POL_DEM_COST", "sum"),
                POD_Dem=("POD_DEM_COST", "sum"),
                Det=("POD_DET_COST", "sum"),
                Avg_POL_Dem_Days=("POL_DEM_CHARGEABLE_DAYS", lambda x: round(x[x > 0].mean(), 1) if (x > 0).any() else 0),
                Avg_POD_Dem_Days=("POD_DEM_CHARGEABLE_DAYS", lambda x: round(x[x > 0].mean(), 1) if (x > 0).any() else 0),
                Avg_Det_Days=("POD_DET_CHARGEABLE_DAYS", lambda x: round(x[x > 0].mean(), 1) if (x > 0).any() else 0),
            )
            .reset_index()
        )
        lane_agg["Total"] = lane_agg["POL_Dem"] + lane_agg["POD_Dem"] + lane_agg["Det"]
        lane_agg = lane_agg.sort_values("Total", ascending=False).head(20)
        st.dataframe(
            lane_agg.style.format(
                {"POL_Dem": "${:,.0f}", "POD_Dem": "${:,.0f}", "Det": "${:,.0f}", "Total": "${:,.0f}"}
            ),
            use_container_width=True,
            hide_index=True,
        )

# -----------------------------------------------------------------------------
# SHIPMENT EXPLORER
# -----------------------------------------------------------------------------
with tab_ships:
    st.markdown("### Shipment-Level D&D Detail")

    if fdf.empty:
        st.warning("No matched shipments available for the selected filters.")
    else:
        st.caption(f"Showing {len(fdf)} matched shipments. Use sidebar filters to narrow.")

        sort_options = [
            "TOTAL_DD_COST",
            "POL_DEM_COST",
            "POD_DEM_COST",
            "POD_DET_COST",
            "POL_DEM_CHARGEABLE_DAYS",
            "POD_DEM_CHARGEABLE_DAYS",
            "POD_DET_CHARGEABLE_DAYS",
        ]
        sort_col = st.selectbox("Sort by", sort_options)
        top_n = st.slider("Show top N", 10, min(500, max(len(fdf), 10)), min(50, max(len(fdf), 10)))

        display_cols = [
            "CONTAINER_NUMBER",
            "SHIPMENT_ID",
            "CARRIER_SCAC",
            "FFW_SCAC",
            "CARRIER_FFW_SCAC",
            "MATCHED_PARTY_TYPE",
            "LANE",
            "CGI",
            "CLL",
            "CDD",
            "CGO",
            "CER",
            "POL_DEM_TOTAL_DAYS",
            "POL_DEM_CHARGEABLE_DAYS",
            "POL_DEM_COST",
            "POD_DEM_TOTAL_DAYS",
            "POD_DEM_CHARGEABLE_DAYS",
            "POD_DEM_COST",
            "POD_DET_TOTAL_DAYS",
            "POD_DET_CHARGEABLE_DAYS",
            "POD_DET_COST",
            "TOTAL_DD_COST",
            "CONTRACT_TYPE",
            "DET_ACCUMULATING",
            "DET_END_SOURCE",
        ]
        show_df = fdf[[c for c in display_cols if c in fdf.columns]].sort_values(sort_col, ascending=False).head(top_n).copy()

        for dc in ["CGI", "CLL", "CDD", "CGO", "CER"]:
            if dc in show_df.columns:
                show_df[dc] = pd.to_datetime(show_df[dc], errors="coerce").dt.strftime("%Y-%m-%d").fillna("—")

        show_df["DET_STATUS"] = show_df.apply(
            lambda r: "⚠️ Active → Today"
            if r.get("DET_ACCUMULATING", False)
            else ("📅 Completed → Modified" if r.get("DET_END_SOURCE") == "MODIFIED_DATE" else "✓ CER"),
            axis=1,
        )
        show_df = show_df.drop(columns=["DET_ACCUMULATING", "DET_END_SOURCE"], errors="ignore")

        st.dataframe(
            show_df.style.format(
                {
                    "POL_DEM_COST": "${:,.2f}",
                    "POD_DEM_COST": "${:,.2f}",
                    "POD_DET_COST": "${:,.2f}",
                    "TOTAL_DD_COST": "${:,.2f}",
                }
            ),
            use_container_width=True,
            hide_index=True,
            height=600,
        )

        st.markdown("---")
        col1, col2, col3 = st.columns(3)

        with col1:
            pol_data = fdf.loc[fdf["POL_DEM_COST"] > 0, ["POL_DEM_CHARGEABLE_DAYS"]].copy()
            if len(pol_data) > 0:
                ch = (
                    alt.Chart(pol_data)
                    .mark_bar(color=POL_DEM_COLOR, cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                    .encode(
                        x=alt.X("POL_DEM_CHARGEABLE_DAYS:Q", bin=alt.Bin(maxbins=20), title="Chargeable Days"),
                        y=alt.Y("count()", title="Shipments"),
                    )
                    .properties(title="POL Demurrage Days Distribution", height=230)
                )
                st.altair_chart(ch, use_container_width=True)

        with col2:
            dem_data = fdf.loc[fdf["POD_DEM_COST"] > 0, ["POD_DEM_CHARGEABLE_DAYS"]].copy()
            if len(dem_data) > 0:
                ch = (
                    alt.Chart(dem_data)
                    .mark_bar(color=DEM_COLOR, cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                    .encode(
                        x=alt.X("POD_DEM_CHARGEABLE_DAYS:Q", bin=alt.Bin(maxbins=20), title="Chargeable Days"),
                        y=alt.Y("count()", title="Shipments"),
                    )
                    .properties(title="POD Demurrage Days Distribution", height=230)
                )
                st.altair_chart(ch, use_container_width=True)

        with col3:
            det_data = fdf.loc[fdf["POD_DET_COST"] > 0, ["POD_DET_CHARGEABLE_DAYS"]].copy()
            if len(det_data) > 0:
                ch = (
                    alt.Chart(det_data)
                    .mark_bar(color=DET_COLOR, cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                    .encode(
                        x=alt.X("POD_DET_CHARGEABLE_DAYS:Q", bin=alt.Bin(maxbins=20), title="Chargeable Days"),
                        y=alt.Y("count()", title="Shipments"),
                    )
                    .properties(title="POD Detention Days Distribution", height=230)
                )
                st.altair_chart(ch, use_container_width=True)

# -----------------------------------------------------------------------------
# CONTRACT GAPS
# -----------------------------------------------------------------------------
with tab_gaps:
    st.markdown("### ⚠️ Contract Gaps")
    st.caption(
        "These shipments did not match a contract, so fees are not calculated. The app surfaces containers where dwell days are above matched-shipment averages, which may indicate missing contract setup."
    )

    if unmatched_df.empty:
        st.success("No unmatched shipments found. All non-cancelled shipments matched uploaded contracts.")
    else:
        risk_df = unmatched_df[unmatched_df["RISK_FLAG"] == True].copy()
        active_no_cer = unmatched_df[unmatched_df["DET_ACCUMULATING"] == True].copy()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Unmatched Shipments", f"{len(unmatched_df):,}")
        c2.metric("Risk Containers", f"{len(risk_df):,}", "above avg dwell")
        c3.metric("Missing Contract Keys", f"{unmatched_df['MATCH_KEY'].nunique():,}")
        c4.metric("Active, No CER", f"{len(active_no_cer):,}", "detention may grow")

        st.markdown("---")
        st.markdown("#### Missing Contract Combinations")
        combo = (
            unmatched_df.groupby(["POD_LOCODE", "CARRIER_FFW_SCAC", "MATCHED_PARTY_TYPE", "CARRIER_SCAC", "FFW_SCAC", "POL_LOCODE", "MATCH_KEY"])
            .agg(
                Shipments=("SHIPMENT_ID", "count"),
                Containers=("CONTAINER_NUMBER", lambda x: x.nunique()),
                Risk_Containers=("RISK_FLAG", lambda x: int(x.sum())),
                Avg_POL_Dem_Days=("POL_DEM_TOTAL_DAYS", "mean"),
                Avg_POD_Dem_Days=("POD_DEM_TOTAL_DAYS", "mean"),
                Avg_POD_Det_Days=("POD_DET_TOTAL_DAYS", "mean"),
                Max_POD_Det_Days=("POD_DET_TOTAL_DAYS", "max"),
                Active_No_CER=("DET_ACCUMULATING", lambda x: int(x.sum())),
            )
            .reset_index()
            .sort_values(["Risk_Containers", "Shipments"], ascending=False)
        )
        st.dataframe(
            combo.style.format(
                {
                    "Avg_POL_Dem_Days": "{:.1f}",
                    "Avg_POD_Dem_Days": "{:.1f}",
                    "Avg_POD_Det_Days": "{:.1f}",
                    "Max_POD_Det_Days": "{:.1f}",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

        if len(combo) > 0:
            top_combo = combo.head(20).copy()
            heat = (
                alt.Chart(top_combo)
                .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                .encode(
                    y=alt.Y("MATCH_KEY:N", sort="-x", title="Missing Contract Key"),
                    x=alt.X("Risk_Containers:Q", title="Risk Containers"),
                    tooltip=[
                        "MATCH_KEY",
                        "Shipments",
                        "Risk_Containers",
                        alt.Tooltip("Avg_POL_Dem_Days:Q", format=".1f"),
                        alt.Tooltip("Avg_POD_Dem_Days:Q", format=".1f"),
                        alt.Tooltip("Avg_POD_Det_Days:Q", format=".1f"),
                    ],
                )
                .properties(title="Top Missing Contract Keys by Risk Containers", height=360)
            )
            st.altair_chart(heat, use_container_width=True)

        st.markdown("---")
        st.markdown("#### Container-Level Contract Gap Risk")
        gap_cols = [
            "CONTAINER_NUMBER",
            "SHIPMENT_ID",
            "CARRIER_SCAC",
            "FFW_SCAC",
            "CARRIER_FFW_SCAC",
            "MATCHED_PARTY_TYPE",
            "LANE",
            "CGI",
            "CLL",
            "CDD",
            "CGO",
            "CER",
            "POL_DEM_TOTAL_DAYS",
            "POD_DEM_TOTAL_DAYS",
            "POD_DET_TOTAL_DAYS",
            "DET_ACCUMULATING",
            "RISK_SCORE",
            "RISK_REASONS",
            "MATCH_KEY",
            "DATA_LIMITATION",
        ]
        gap_show = unmatched_df[[c for c in gap_cols if c in unmatched_df.columns]].sort_values(
            ["RISK_SCORE", "POD_DET_TOTAL_DAYS", "POD_DEM_TOTAL_DAYS", "POL_DEM_TOTAL_DAYS"],
            ascending=False,
        ).copy()
        for dc in ["CGI", "CLL", "CDD", "CGO", "CER"]:
            if dc in gap_show.columns:
                gap_show[dc] = pd.to_datetime(gap_show[dc], errors="coerce").dt.strftime("%Y-%m-%d").fillna("—")

        st.dataframe(gap_show, use_container_width=True, hide_index=True, height=520)

# -----------------------------------------------------------------------------
# TIER EXPOSURE
# -----------------------------------------------------------------------------
with tab_tiers:
    st.markdown("### 🔥 Tier Exposure")
    st.caption(
        "This view explains which contractual charge tiers are being hit. Thereafter tier exposure usually indicates severe exceptions."
    )

    if fdf.empty:
        st.warning("No matched shipments available for the selected filters.")
    else:
        tier_cost_cols = [
            "POL_DEM_TIER1_COST",
            "POL_DEM_TIER2_COST",
            "POL_DEM_THEREAFTER_COST",
            "POD_DEM_TIER1_COST",
            "POD_DEM_TIER2_COST",
            "POD_DEM_THEREAFTER_COST",
            "POD_DET_TIER1_COST",
            "POD_DET_TIER2_COST",
            "POD_DET_THEREAFTER_COST",
        ]
        total_tier_cost = fdf[tier_cost_cols].sum().sum()
        thereafter_cost = fdf[
            ["POL_DEM_THEREAFTER_COST", "POD_DEM_THEREAFTER_COST", "POD_DET_THEREAFTER_COST"]
        ].sum().sum()
        thereafter_containers = (
            (fdf["POL_DEM_THEREAFTER_COST"] > 0)
            | (fdf["POD_DEM_THEREAFTER_COST"] > 0)
            | (fdf["POD_DET_THEREAFTER_COST"] > 0)
        ).sum()

        top_thereafter_carrier = "—"
        if thereafter_cost > 0:
            temp = fdf.copy()
            temp["THEREAFTER_COST"] = (
                temp["POL_DEM_THEREAFTER_COST"]
                + temp["POD_DEM_THEREAFTER_COST"]
                + temp["POD_DET_THEREAFTER_COST"]
            )
            top_thereafter_carrier = temp.groupby("CARRIER_FFW_SCAC")["THEREAFTER_COST"].sum().idxmax()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Tiered Cost", f"${total_tier_cost:,.0f}")
        c2.metric("Thereafter Cost", f"${thereafter_cost:,.0f}")
        c3.metric("% in Thereafter", f"{(thereafter_cost / total_tier_cost * 100):.1f}%" if total_tier_cost else "0.0%")
        c4.metric("Containers Hitting Thereafter", f"{thereafter_containers:,}", str(top_thereafter_carrier))

        st.markdown("---")
        st.markdown("#### Tier Cost by Charge Type")

        tier_summary = pd.DataFrame(
            [
                {
                    "Charge Type": "POL Demurrage",
                    "Tier 1 Cost": fdf["POL_DEM_TIER1_COST"].sum(),
                    "Tier 2 Cost": fdf["POL_DEM_TIER2_COST"].sum(),
                    "Thereafter Cost": fdf["POL_DEM_THEREAFTER_COST"].sum(),
                },
                {
                    "Charge Type": "POD Demurrage",
                    "Tier 1 Cost": fdf["POD_DEM_TIER1_COST"].sum(),
                    "Tier 2 Cost": fdf["POD_DEM_TIER2_COST"].sum(),
                    "Thereafter Cost": fdf["POD_DEM_THEREAFTER_COST"].sum(),
                },
                {
                    "Charge Type": "POD Detention",
                    "Tier 1 Cost": fdf["POD_DET_TIER1_COST"].sum(),
                    "Tier 2 Cost": fdf["POD_DET_TIER2_COST"].sum(),
                    "Thereafter Cost": fdf["POD_DET_THEREAFTER_COST"].sum(),
                },
            ]
        )
        tier_melt = tier_summary.melt(id_vars="Charge Type", var_name="Tier", value_name="Cost")
        ch = (
            alt.Chart(tier_melt)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("Charge Type:N", title="Charge Type"),
                y=alt.Y("Cost:Q", title="Cost (USD)", stack=True),
                color=alt.Color(
                    "Tier:N",
                    scale=alt.Scale(
                        domain=["Tier 1 Cost", "Tier 2 Cost", "Thereafter Cost"],
                        range=[TIER1_COLOR, TIER2_COLOR, THEREAFTER_COLOR],
                    ),
                ),
                tooltip=["Charge Type", "Tier", alt.Tooltip("Cost:Q", format="$,.0f")],
            )
            .properties(height=330)
        )
        st.altair_chart(ch, use_container_width=True)

        st.markdown("#### Carrier / FFW × POD Tier Exposure")
        tier_cp = fdf.copy()
        tier_cp["Tier 1 Cost"] = (
            tier_cp["POL_DEM_TIER1_COST"] + tier_cp["POD_DEM_TIER1_COST"] + tier_cp["POD_DET_TIER1_COST"]
        )
        tier_cp["Tier 2 Cost"] = (
            tier_cp["POL_DEM_TIER2_COST"] + tier_cp["POD_DEM_TIER2_COST"] + tier_cp["POD_DET_TIER2_COST"]
        )
        tier_cp["Thereafter Cost"] = (
            tier_cp["POL_DEM_THEREAFTER_COST"]
            + tier_cp["POD_DEM_THEREAFTER_COST"]
            + tier_cp["POD_DET_THEREAFTER_COST"]
        )
        tier_cp["Carrier / FFW + POD"] = tier_cp["CARRIER_FFW_SCAC"].astype(str) + " | " + tier_cp["POD_LOCODE"].astype(str)
        tier_cp_agg = (
            tier_cp.groupby("Carrier / FFW + POD")
            .agg(
                Ships=("SHIPMENT_ID", "count"),
                Tier_1_Cost=("Tier 1 Cost", "sum"),
                Tier_2_Cost=("Tier 2 Cost", "sum"),
                Thereafter_Cost=("Thereafter Cost", "sum"),
            )
            .reset_index()
        )
        tier_cp_agg["Total"] = tier_cp_agg["Tier_1_Cost"] + tier_cp_agg["Tier_2_Cost"] + tier_cp_agg["Thereafter_Cost"]
        tier_cp_agg = tier_cp_agg.sort_values("Total", ascending=False).head(30)
        st.dataframe(
            tier_cp_agg.style.format(
                {
                    "Tier_1_Cost": "${:,.0f}",
                    "Tier_2_Cost": "${:,.0f}",
                    "Thereafter_Cost": "${:,.0f}",
                    "Total": "${:,.0f}",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

        tier_cp_melt = tier_cp_agg.melt(
            id_vars="Carrier / FFW + POD",
            value_vars=["Tier_1_Cost", "Tier_2_Cost", "Thereafter_Cost"],
            var_name="Tier",
            value_name="Cost",
        )
        tier_cp_melt["Tier"] = tier_cp_melt["Tier"].replace(
            {"Tier_1_Cost": "Tier 1", "Tier_2_Cost": "Tier 2", "Thereafter_Cost": "Thereafter"}
        )
        ch2 = (
            alt.Chart(tier_cp_melt)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                y=alt.Y("Carrier / FFW + POD:N", sort="-x", title="Carrier / FFW + POD"),
                x=alt.X("Cost:Q", title="Cost (USD)"),
                color=alt.Color(
                    "Tier:N",
                    scale=alt.Scale(domain=["Tier 1", "Tier 2", "Thereafter"], range=[TIER1_COLOR, TIER2_COLOR, THEREAFTER_COLOR]),
                ),
                tooltip=["Carrier / FFW + POD", "Tier", alt.Tooltip("Cost:Q", format="$,.0f")],
            )
            .properties(title="Top Carrier / FFW + POD Combinations by Tier Cost", height=500)
        )
        st.altair_chart(ch2, use_container_width=True)

# -----------------------------------------------------------------------------
# DOWNLOAD
# -----------------------------------------------------------------------------
with tab_download:
    st.markdown("### 📥 Download Results")
    st.markdown("Download matched priced shipments, unmatched contract-gap shipments, and the uploaded contracts.")

    matched_dl = build_download_df(fdf) if not fdf.empty else pd.DataFrame()
    unmatched_dl = build_unmatched_download_df(unmatched_df) if not unmatched_df.empty else pd.DataFrame()

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### Matched / Priced Shipments")
        st.markdown(f"**Rows:** {len(matched_dl)} | **Columns:** {len(matched_dl.columns)}")
        if not matched_dl.empty:
            st.dataframe(matched_dl.head(10), use_container_width=True, hide_index=True)
            csv_bytes = matched_dl.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 Download Matched Results CSV",
                data=csv_bytes,
                file_name=f"Demurrage_Detention_Matched_Results_{datetime.now().strftime('%Y-%m-%d')}.csv",
                mime="text/csv",
            )

    with c2:
        st.markdown("#### Contract Gaps / Unmatched Shipments")
        st.markdown(f"**Rows:** {len(unmatched_dl)} | **Columns:** {len(unmatched_dl.columns)}")
        if not unmatched_dl.empty:
            st.dataframe(unmatched_dl.head(10), use_container_width=True, hide_index=True)
            csv_bytes = unmatched_dl.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 Download Contract Gaps CSV",
                data=csv_bytes,
                file_name=f"Demurrage_Detention_Contract_Gaps_{datetime.now().strftime('%Y-%m-%d')}.csv",
                mime="text/csv",
            )

    st.markdown("---")
    st.markdown("#### Excel Workbook")
    try:
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            if not matched_dl.empty:
                matched_dl.to_excel(writer, sheet_name="Matched Results", index=False)
            if not unmatched_dl.empty:
                unmatched_dl.to_excel(writer, sheet_name="Contract Gaps", index=False)

            summary_data = {
                "Metric": [
                    "Total Shipments in Upload",
                    "Cancelled Excluded",
                    "Matched / Priced Shipments",
                    "Unmatched Shipments",
                    "Unmatched Risk Containers",
                    "Total D&D Cost",
                    "POL Demurrage",
                    "POD Demurrage",
                    "POD Detention",
                    "Detention Accumulating",
                    "Analysis Date",
                ],
                "Value": [
                    total_shipments,
                    cancelled_count,
                    len(rdf),
                    len(unmatched_df),
                    int(unmatched_df["RISK_FLAG"].sum()) if not unmatched_df.empty else 0,
                    f"${rdf['TOTAL_DD_COST'].sum():,.2f}" if not rdf.empty else "$0.00",
                    f"${rdf['POL_DEM_COST'].sum():,.2f}" if not rdf.empty else "$0.00",
                    f"${rdf['POD_DEM_COST'].sum():,.2f}" if not rdf.empty else "$0.00",
                    f"${rdf['POD_DET_COST'].sum():,.2f}" if not rdf.empty else "$0.00",
                    int(rdf["DET_ACCUMULATING"].sum()) if not rdf.empty else 0,
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                ],
            }
            pd.DataFrame(summary_data).to_excel(writer, sheet_name="Summary", index=False)

            contract_display_cols = [
                "terminalIdentifier",
                "carrierScac",
                "ffwScac",
                "portOfLoadingLocode",
                "freeDemurrageDays",
                "firstDemurrageDays",
                "firstDemurrageRate",
                "secondDemurrageDays",
                "secondDemurrageRate",
                "thereafterDemurrageRate",
                "freeDetentionDays",
                "firstDetentionDays",
                "firstDetentionRate",
                "secondDetentionDays",
                "secondDetentionRate",
                "thereafterDetentionRate",
                "combinedFreeDays",
            ]
            if contracts_df is not None:
                existing_contract_cols = [c for c in contract_display_cols if c in contracts_df.columns]
                contracts_df[existing_contract_cols].to_excel(writer, sheet_name="Contracts", index=False)
            elif estimate_profile is not None:
                estimate_rows = []
                for charge_name, key in [
                    ("POL Demurrage", "pol_dem"),
                    ("POD Demurrage", "pod_dem"),
                    ("POD Detention", "pod_det"),
                ]:
                    rec = estimate_profile.get(key) or {}
                    row = {"Charge Type": charge_name}
                    row.update(rec)
                    estimate_rows.append(row)
                pd.DataFrame(estimate_rows).to_excel(writer, sheet_name="Estimate Rates", index=False)

        st.download_button(
            label="📥 Download Excel Workbook",
            data=buffer.getvalue(),
            file_name=f"Demurrage_Detention_Analyzer_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except ImportError:
        st.info("Excel download requires openpyxl. Add openpyxl to requirements.txt, or use CSV downloads above.")
