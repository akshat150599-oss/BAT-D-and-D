"""
BAT Demurrage & Detention (D&D) Analyzer
=========================================
Standalone Streamlit app for BAT ocean shipment D&D cost analysis.

Calculation Logic (from D&D PM):
  Demurrage = [(Gate Out Full from POD - Discharge at POD) - Free Dem Days] x Tiered Rate
  Detention = [(Container Empty Return - Gate Out Full from POD) - Free Det Days] x Tiered Rate

Exclusion rule (applies to ALL shipments):
  CANCELLED  -> shipment excluded from D&D entirely

No CER handling (when CER is missing):
  ACTIVE     -> detention accumulates to today's date (analysis run time)
  COMPLETED  -> detention end = SHIPMENT_MODIFIED_DATE

Combined Free Days: Demurrage consumes from pool first, detention gets remainder.

Run: streamlit run streamlit_app.py
"""

import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
from datetime import datetime
from io import BytesIO

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="BAT D&D Analyzer",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
# ALTAIR THEME
# ─────────────────────────────────────────────
DEM_COLOR = "#f5a623"
DET_COLOR = "#7b61ff"
TOTAL_COLOR = "#00d4aa"
ALERT_COLOR = "#ff5c5c"

# ─────────────────────────────────────────────
# CUSTOM CSS
# ─────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { 
        padding-top: 1.5rem; 
        max-width: 1200px; 
    }

    /* Fix tabs getting cut by horizontal line */
    div[data-testid="stTabs"] div[role="tablist"] {
        border-bottom: 1px solid #e6e6e6;
        padding-bottom: 0.35rem;
        overflow: visible;
    }

    div[data-testid="stTabs"] button[role="tab"] {
        padding-top: 0.6rem;
        padding-bottom: 0.6rem;
        min-height: 42px;
        overflow: visible;
        color: #222222 !important;
        font-weight: 600;
    }

    div[data-testid="stTabs"] button[aria-selected="true"] {
        color: #ff4b4b !important;
        font-weight: 700;
        border-bottom: 2px solid #ff4b4b;
    }

    /* Metric card styling */
    div[data-testid="stMetric"] {
        background: #111827;
        border: 1px solid #2a2d3a;
        border-radius: 10px;
        padding: 12px 16px;
    }

    div[data-testid="stMetric"] label {
        color: #ffffff !important;
        font-size: 11px !important;
        text-transform: uppercase;
        letter-spacing: 0.8px;
        font-weight: 800 !important;
    }

    div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        color: #ffffff !important;
        font-size: 28px !important;
        font-weight: 900 !important;
    }

    div[data-testid="stMetricDelta"] {
        color: #22c55e !important;
        font-weight: 800 !important;
    }
</style>
""", unsafe_allow_html=True)

BAT_CONTRACTS = [
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":4,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"INMAA","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":4,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"NGTIN","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":4,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"NGAPP","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":4,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"BGVAR","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":4,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"TRIZM","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":4,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"TRALI","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":4,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"NGLEK","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":4,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"NGLKK","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":4,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"CLSAI","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":3,"firstDetentionRate":215.0,"secondDetentionDays":3,"secondDetentionRate":265.0,"thereafterDetentionRate":315.0,"currency":"USD","carrierScac":"HLCU","ffwScac":None,"portOfLoadingLocode":"CLSAI","combinedFreeDays":6.0},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":3,"firstDetentionRate":215.0,"secondDetentionDays":3,"secondDetentionRate":265.0,"thereafterDetentionRate":315.0,"currency":"USD","carrierScac":"HLCU","ffwScac":None,"portOfLoadingLocode":"IDSUB","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":4,"firstDemurrageRate":195.0,"secondDemurrageDays":4,"secondDemurrageRate":225.0,"thereafterDemurrageRate":260.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":4,"firstDetentionRate":195.0,"secondDetentionDays":4,"secondDetentionRate":225.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":None,"ffwScac":"KHNN","portOfLoadingLocode":"GTPBR","combinedFreeDays":14.0},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":4,"firstDemurrageRate":195.0,"secondDemurrageDays":4,"secondDemurrageRate":225.0,"thereafterDemurrageRate":260.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":4,"firstDetentionRate":195.0,"secondDetentionDays":4,"secondDetentionRate":225.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":None,"ffwScac":"KHNN","portOfLoadingLocode":"MXZLO","combinedFreeDays":20.0},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":3.0,"firstDemurrageDays":4,"firstDemurrageRate":195.0,"secondDemurrageDays":4,"secondDemurrageRate":225.0,"thereafterDemurrageRate":260.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":4,"firstDetentionRate":195.0,"secondDetentionDays":4,"secondDetentionRate":225.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":None,"ffwScac":"KHNN","portOfLoadingLocode":"CLSAI","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":5,"firstDetentionRate":165.0,"secondDetentionDays":1,"secondDetentionRate":120.0,"thereafterDetentionRate":120.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"BRRIG","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":5,"firstDetentionRate":165.0,"secondDetentionDays":1,"secondDetentionRate":120.0,"thereafterDetentionRate":120.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"MXATM","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":5,"firstDetentionRate":165.0,"secondDetentionDays":1,"secondDetentionRate":120.0,"thereafterDetentionRate":120.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"BRRIG","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":5,"firstDetentionRate":165.0,"secondDetentionDays":1,"secondDetentionRate":120.0,"thereafterDetentionRate":120.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"GTPBR","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":5,"firstDetentionRate":165.0,"secondDetentionDays":1,"secondDetentionRate":120.0,"thereafterDetentionRate":120.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"MXZLO","combinedFreeDays":None},
    {"terminalIdentifier":"USORF","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":4.0,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":4.0,"firstDetentionDays":5,"firstDetentionRate":165.0,"secondDetentionDays":1,"secondDetentionRate":120.0,"thereafterDetentionRate":120.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"GTPBR","combinedFreeDays":None},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"CNHUA","combinedFreeDays":21.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"BDCGP","combinedFreeDays":21.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"BRRIG","combinedFreeDays":21.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"ARBUE","combinedFreeDays":21.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"BRNVT","combinedFreeDays":21.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"BRIOA","combinedFreeDays":21.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":4,"firstDemurrageRate":17.25,"secondDemurrageDays":3,"secondDemurrageRate":28.75,"thereafterDemurrageRate":34.5,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":4,"firstDetentionRate":17.25,"secondDetentionDays":3,"secondDetentionRate":28.75,"thereafterDetentionRate":34.5,"currency":"USD","carrierScac":"HLCU","ffwScac":None,"portOfLoadingLocode":"ARBUE","combinedFreeDays":10.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":4,"firstDemurrageRate":17.25,"secondDemurrageDays":3,"secondDemurrageRate":28.75,"thereafterDemurrageRate":34.5,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":4,"firstDetentionRate":17.25,"secondDetentionDays":3,"secondDetentionRate":28.75,"thereafterDetentionRate":34.5,"currency":"USD","carrierScac":"HLCU","ffwScac":None,"portOfLoadingLocode":"IDSUB","combinedFreeDays":10.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":4,"firstDemurrageRate":17.25,"secondDemurrageDays":3,"secondDemurrageRate":28.75,"thereafterDemurrageRate":34.5,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":4,"firstDetentionRate":17.25,"secondDetentionDays":3,"secondDetentionRate":28.75,"thereafterDetentionRate":34.5,"currency":"USD","carrierScac":"HLCU","ffwScac":None,"portOfLoadingLocode":"BRNVT","combinedFreeDays":10.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":4,"firstDemurrageRate":195.0,"secondDemurrageDays":4,"secondDemurrageRate":225.0,"thereafterDemurrageRate":260.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":4,"firstDetentionRate":195.0,"secondDetentionDays":4,"secondDetentionRate":225.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":None,"ffwScac":"KHNN","portOfLoadingLocode":"TRSSX","combinedFreeDays":21.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":4,"firstDemurrageRate":195.0,"secondDemurrageDays":4,"secondDemurrageRate":225.0,"thereafterDemurrageRate":260.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":4,"firstDetentionRate":195.0,"secondDetentionDays":4,"secondDetentionRate":225.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":None,"ffwScac":"KHNN","portOfLoadingLocode":"TRALI","combinedFreeDays":21.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":5,"secondDetentionRate":165.0,"thereafterDetentionRate":195.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"MZBEW","combinedFreeDays":20.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":5,"secondDetentionRate":165.0,"thereafterDetentionRate":195.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"USORF","combinedFreeDays":20.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":5,"secondDetentionRate":165.0,"thereafterDetentionRate":195.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"ZADUR","combinedFreeDays":20.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":5,"secondDetentionRate":165.0,"thereafterDetentionRate":195.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"TRIZM","combinedFreeDays":20.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":5,"secondDetentionRate":165.0,"thereafterDetentionRate":195.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"INMAA","combinedFreeDays":20.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":5,"secondDetentionRate":165.0,"thereafterDetentionRate":195.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"ITNAP","combinedFreeDays":20.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":5,"secondDetentionRate":165.0,"thereafterDetentionRate":195.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"INENR","combinedFreeDays":20.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":5,"secondDetentionRate":165.0,"thereafterDetentionRate":195.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"INKAT","combinedFreeDays":20.0},
    {"terminalIdentifier":"HRRJK","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":1,"firstDemurrageRate":30.0,"secondDemurrageDays":1,"secondDemurrageRate":30.0,"thereafterDemurrageRate":30.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":1,"firstDetentionRate":30.0,"secondDetentionDays":1,"secondDetentionRate":30.0,"thereafterDetentionRate":30.0,"currency":"USD","carrierScac":"OOLU","ffwScac":None,"portOfLoadingLocode":"CNSHK","combinedFreeDays":14.0},
    {"terminalIdentifier":"NGAPP","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":4,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"BGVAR","combinedFreeDays":28.0},
    {"terminalIdentifier":"NGAPP","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":4,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"INMAA","combinedFreeDays":28.0},
    {"terminalIdentifier":"NGAPP","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":4,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"MZBEW","combinedFreeDays":28.0},
    {"terminalIdentifier":"NGAPP","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":6.28,"secondDemurrageDays":5,"secondDemurrageRate":8.22,"thereafterDemurrageRate":12.08,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":6.28,"secondDetentionDays":5,"secondDetentionRate":8.22,"thereafterDetentionRate":12.08,"currency":"USD","carrierScac":"HLCU","ffwScac":None,"portOfLoadingLocode":"ARBUE","combinedFreeDays":7.0},
    {"terminalIdentifier":"NGAPP","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":6.28,"secondDemurrageDays":5,"secondDemurrageRate":8.22,"thereafterDemurrageRate":12.08,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":6.28,"secondDetentionDays":5,"secondDetentionRate":8.22,"thereafterDetentionRate":12.08,"currency":"USD","carrierScac":"HLCU","ffwScac":None,"portOfLoadingLocode":"BRRIG","combinedFreeDays":7.0},
    {"terminalIdentifier":"NGAPP","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":0,"secondDetentionRate":0.0,"thereafterDetentionRate":80.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"BRNVT","combinedFreeDays":14.0},
    {"terminalIdentifier":"NGAPP","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":0,"secondDetentionRate":0.0,"thereafterDetentionRate":80.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"CNHUA","combinedFreeDays":20.0},
    {"terminalIdentifier":"NGAPP","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":0,"secondDetentionRate":0.0,"thereafterDetentionRate":80.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"INENR","combinedFreeDays":14.0},
    {"terminalIdentifier":"NGAPP","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":0,"secondDetentionRate":0.0,"thereafterDetentionRate":80.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"ZADUR","combinedFreeDays":14.0},
    {"terminalIdentifier":"NGAPP","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":0,"secondDetentionRate":0.0,"thereafterDetentionRate":80.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"USORF","combinedFreeDays":14.0},
    {"terminalIdentifier":"NGAPP","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":5.95,"secondDemurrageDays":5,"secondDemurrageRate":7.83,"thereafterDemurrageRate":9.91,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":5.95,"secondDetentionDays":5,"secondDetentionRate":7.83,"thereafterDetentionRate":9.91,"currency":"USD","carrierScac":"ONEY","ffwScac":None,"portOfLoadingLocode":"BDCGP","combinedFreeDays":14.0},
    {"terminalIdentifier":"NGTIN","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"BGVAR","combinedFreeDays":28.0},
    {"terminalIdentifier":"NGTIN","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"INMAA","combinedFreeDays":28.0},
    {"terminalIdentifier":"NGTIN","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":295.0,"secondDemurrageDays":5,"secondDemurrageRate":355.0,"thereafterDemurrageRate":395.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":200.0,"secondDetentionDays":4,"secondDetentionRate":235.0,"thereafterDetentionRate":260.0,"currency":"USD","carrierScac":"CMDU","ffwScac":None,"portOfLoadingLocode":"MZBEW","combinedFreeDays":28.0},
    {"terminalIdentifier":"NGTIN","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":6.28,"secondDemurrageDays":5,"secondDemurrageRate":8.22,"thereafterDemurrageRate":12.08,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":6.28,"secondDetentionDays":5,"secondDetentionRate":8.22,"thereafterDetentionRate":12.08,"currency":"USD","carrierScac":"HLCU","ffwScac":None,"portOfLoadingLocode":"ARBUE","combinedFreeDays":7.0},
    {"terminalIdentifier":"NGTIN","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":6.28,"secondDemurrageDays":5,"secondDemurrageRate":8.22,"thereafterDemurrageRate":12.08,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":6.28,"secondDetentionDays":5,"secondDetentionRate":8.22,"thereafterDetentionRate":12.08,"currency":"USD","carrierScac":"HLCU","ffwScac":None,"portOfLoadingLocode":"BRRIG","combinedFreeDays":7.0},
    {"terminalIdentifier":"NGTIN","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":5,"secondDetentionRate":165.0,"thereafterDetentionRate":195.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"BRNVT","combinedFreeDays":14.0},
    {"terminalIdentifier":"NGTIN","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":5,"secondDetentionRate":165.0,"thereafterDetentionRate":195.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"CNHUA","combinedFreeDays":20.0},
    {"terminalIdentifier":"NGTIN","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":5,"secondDetentionRate":165.0,"thereafterDetentionRate":195.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"INENR","combinedFreeDays":14.0},
    {"terminalIdentifier":"NGTIN","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":5,"secondDetentionRate":165.0,"thereafterDetentionRate":195.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"ZADUR","combinedFreeDays":14.0},
    {"terminalIdentifier":"NGTIN","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":0,"firstDemurrageRate":0.0,"secondDemurrageDays":5,"secondDemurrageRate":65.0,"thereafterDemurrageRate":120.0,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":0,"firstDetentionRate":0.0,"secondDetentionDays":5,"secondDetentionRate":165.0,"thereafterDetentionRate":195.0,"currency":"USD","carrierScac":"MSCU","ffwScac":None,"portOfLoadingLocode":"USORF","combinedFreeDays":14.0},
    {"terminalIdentifier":"NGTIN","demurrageStartEventType":"DISCHARGE_AT_POD","demurrageTariffCalculationMethod":"CALENDAR_DAYS","validityStartDate":"2026-01-01","validityEndDate":"2026-12-31","freeDemurrageDays":None,"firstDemurrageDays":5,"firstDemurrageRate":5.95,"secondDemurrageDays":5,"secondDemurrageRate":7.83,"thereafterDemurrageRate":9.91,"detentionStartEventType":"GATE_OUT_FULL_FROM_POD","detentionTariffCalculationMethod":"CALENDAR_DAYS","freeDetentionDays":None,"firstDetentionDays":5,"firstDetentionRate":5.95,"secondDetentionDays":5,"secondDetentionRate":7.83,"thereafterDetentionRate":9.91,"currency":"USD","carrierScac":"ONEY","ffwScac":None,"portOfLoadingLocode":"BDCGP","combinedFreeDays":14.0},
]


# ─────────────────────────────────────────────
# D&D CALCULATION ENGINE
# ─────────────────────────────────────────────
def _safe(val, default=0):
    """Return val if not None/NaN, else default."""
    if val is None:
        return default
    try:
        if np.isnan(val):
            return default
    except (TypeError, ValueError):
        pass
    return val


def calc_tiered_cost(chargeable_days, t1_days, t1_rate, t2_days, t2_rate, thereafter_rate):
    """Calculate cost using a 3-tier rate structure."""
    if chargeable_days <= 0:
        return 0.0
    cost = 0.0
    remaining = chargeable_days
    # Tier 1
    t1 = min(remaining, _safe(t1_days))
    cost += t1 * _safe(t1_rate)
    remaining -= t1
    # Tier 2
    if remaining > 0:
        t2 = min(remaining, _safe(t2_days))
        cost += t2 * _safe(t2_rate)
        remaining -= t2
    # Thereafter
    if remaining > 0:
        cost += remaining * _safe(thereafter_rate)
    return round(cost, 2)


def build_contract_lookup(contracts_list):
    """Build carrier and FFW lookup dicts from contract records."""
    carrier_lookup = {}
    ffw_lookup = {}
    for c in contracts_list:
        terminal = c.get("terminalIdentifier", "")
        pol = c.get("portOfLoadingLocode", "")
        carrier = c.get("carrierScac")
        ffw = c.get("ffwScac")
        if carrier:
            key = f"{terminal}|{carrier}|{pol}"
            if key not in carrier_lookup:
                carrier_lookup[key] = c
        if ffw:
            key = f"{terminal}|{ffw}|{pol}"
            if key not in ffw_lookup:
                ffw_lookup[key] = c
    return carrier_lookup, ffw_lookup


def process_shipments(df):
    """
    Main D&D calculation engine.
    
    Milestones in the input CSV:
      CEP → CGI → CLL → VDL → VAD → CDD → CGO → CER
    
    D&D Logic:
      Demurrage = (CGO - CDD) - free days  →  tiered rate
      Detention  = (CER - CGO) - free days  →  tiered rate
    
    No CER handling:
      - SUBSCRIPTION_STATUS = CANCELLED  →  exclude shipment entirely (all shipments)
      - SUBSCRIPTION_STATUS = ACTIVE     + no CER →  detention accumulates to today's date
      - SUBSCRIPTION_STATUS = COMPLETED  + no CER →  detention end = SHIPMENT_MODIFIED_DATE
    
    Combined Free Days: demurrage eats first, detention gets leftover.
    """
    # Parse event timestamps
    event_cols = ["CDD", "CGO", "CER", "VAD", "VDL", "CGI", "CEP", "CLL"]
    for col in event_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    df["REPORTING_DATE"] = pd.to_datetime(df["REPORTING_DATE"], errors="coerce", utc=True)
    df["SHIPMENT_MODIFIED_DATE"] = pd.to_datetime(df["SHIPMENT_MODIFIED_DATE"], errors="coerce", utc=True)

    # "today" = the moment this analysis runs (used for active shipments with no CER)
    analysis_run_date = pd.Timestamp.now(tz="UTC")

    carrier_lookup, ffw_lookup = build_contract_lookup(BAT_CONTRACTS)

    # Build match key per shipment
    df["_match_key"] = (
        df["POD_LOCODE"].fillna("")
        + "|"
        + df["CARRIER_SCAC"].fillna("")
        + "|"
        + df["POL_LOCODE"].fillna("")
    )

    # ── Exclude CANCELLED shipments from D&D calculation entirely ──
    cancelled_count = (df["SUBSCRIPTION_STATUS"] == "CANCELLED").sum()
    df = df[df["SUBSCRIPTION_STATUS"] != "CANCELLED"].copy()

    results = []
    for _, row in df.iterrows():
        key = row["_match_key"]
        contract = carrier_lookup.get(key) or ffw_lookup.get(key)
        if contract is None:
            continue

        cdd = row["CDD"]
        cgo = row["CGO"]
        cer = row["CER"]
        sub_status = row.get("SUBSCRIPTION_STATUS", "")

        # Must have CDD (discharge) to calculate anything
        if pd.isna(cdd):
            continue

        # ── Free days setup ──
        free_dem = contract.get("freeDemurrageDays")
        free_det = contract.get("freeDetentionDays")
        combined_free = contract.get("combinedFreeDays")
        has_combined = combined_free is not None

        # ── DEMURRAGE: CDD → CGO ──
        dem_total_days = None
        dem_chargeable = 0.0
        dem_cost = 0.0
        remaining_free_for_det = 0.0

        if not pd.isna(cgo):
            dem_total_days = max(0, (cgo - cdd).total_seconds() / 86400)

            if has_combined:
                dem_chargeable = max(0, dem_total_days - combined_free)
                remaining_free_for_det = max(0, combined_free - dem_total_days)
            else:
                dem_chargeable = max(0, dem_total_days - _safe(free_dem))

            if dem_chargeable > 0:
                dem_cost = calc_tiered_cost(
                    dem_chargeable,
                    contract.get("firstDemurrageDays"),
                    contract.get("firstDemurrageRate"),
                    contract.get("secondDemurrageDays"),
                    contract.get("secondDemurrageRate"),
                    contract.get("thereafterDemurrageRate"),
                )

        # ── DETENTION: CGO → CER (or accumulating if no CER) ──
        det_total_days = None
        det_chargeable = 0.0
        det_cost = 0.0
        det_accumulating = False
        det_end_source = ""  # tracks which date was used for transparency

        if not pd.isna(cgo):
            if not pd.isna(cer):
                # Normal case — CER exists
                det_total_days = max(0, (cer - cgo).total_seconds() / 86400)
                det_end_source = "CER"
            else:
                # No CER — determine end date based on subscription status
                if sub_status == "ACTIVE":
                    # Still active → accumulate to today (analysis run date)
                    det_total_days = max(0, (analysis_run_date - cgo).total_seconds() / 86400)
                    det_accumulating = True
                    det_end_source = "TODAY"
                elif sub_status == "COMPLETED":
                    # Completed → use shipment modified date as end
                    modified = row["SHIPMENT_MODIFIED_DATE"]
                    if not pd.isna(modified):
                        det_total_days = max(0, (modified - cgo).total_seconds() / 86400)
                    det_end_source = "MODIFIED_DATE"

            if det_total_days is not None:
                if has_combined:
                    det_chargeable = max(0, det_total_days - remaining_free_for_det)
                else:
                    det_chargeable = max(0, det_total_days - _safe(free_det))

                if det_chargeable > 0:
                    det_cost = calc_tiered_cost(
                        det_chargeable,
                        contract.get("firstDetentionDays"),
                        contract.get("firstDetentionRate"),
                        contract.get("secondDetentionDays"),
                        contract.get("secondDetentionRate"),
                        contract.get("thereafterDetentionRate"),
                    )

        results.append(
            {
                "SHIPMENT_ID": row["SHIPMENT_ID"],
                "CONTAINER_NUMBER": row.get("CONTAINER_NUMBER", ""),
                "CARRIER_SCAC": row["CARRIER_SCAC"],
                "CARRIER_NAME": row.get("CARRIER_NAME", ""),
                "POL_LOCODE": row["POL_LOCODE"],
                "POL": row.get("POL", ""),
                "POD_LOCODE": row["POD_LOCODE"],
                "POD": row.get("POD", ""),
                "SUBSCRIPTION_STATUS": sub_status,
                "LIFECYCLE_STATUS": row.get("LIFECYCLE_STATUS", ""),
                "CDD": cdd,
                "CGO": cgo if not pd.isna(cgo) else pd.NaT,
                "CER": cer if not pd.isna(cer) else pd.NaT,
                "DEM_TOTAL_DAYS": round(dem_total_days, 2) if dem_total_days is not None else None,
                "DEM_CHARGEABLE_DAYS": round(dem_chargeable, 2),
                "DEM_COST": dem_cost,
                "DET_TOTAL_DAYS": round(det_total_days, 2) if det_total_days is not None else None,
                "DET_CHARGEABLE_DAYS": round(det_chargeable, 2),
                "DET_COST": det_cost,
                "DET_ACCUMULATING": det_accumulating,
                "DET_END_SOURCE": det_end_source,
                "FREE_DEM_DAYS": _safe(free_dem, None),
                "FREE_DET_DAYS": _safe(free_det, None),
                "COMBINED_FREE_DAYS": combined_free,
                "TOTAL_DD_COST": round(dem_cost + det_cost, 2),
                "CONTRACT_TYPE": "Combined" if has_combined else "Separate",
                "LANE": f"{row['POL_LOCODE']} → {row['POD_LOCODE']}",
            }
        )

    return pd.DataFrame(results), len(df) + cancelled_count, cancelled_count


# ─────────────────────────────────────────────
# PLOTLY THEME
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# STREAMLIT APP
# ─────────────────────────────────────────────

# ── Sidebar ──
with st.sidebar:
    st.markdown("### 🚢 BAT D&D Analyzer")
    st.markdown("---")
    uploaded_file = st.file_uploader(
        "Upload D&D Shipment CSV",
        type=["csv"],
        help="Upload the P44 ocean shipment export CSV with milestone events.",
    )
    st.markdown("---")
    st.markdown("**Contracts:** BAT 2026 (hardcoded)")
    st.markdown(f"**Contract rows:** {len(BAT_CONTRACTS)}")
    st.markdown("**Terminals:** USORF, HRRJK, NGAPP, NGTIN")
    st.markdown("**Carriers:** CMDU, HLCU, MSCU, ONEY, OOLU + KHNN (FFW)")

if uploaded_file is None:
    st.markdown("## 🚢 BAT Demurrage & Detention Analyzer")
    st.markdown("---")
    st.info("Upload a D&D shipment CSV from the sidebar to get started.")
    st.markdown("**Expected milestone columns:** `CEP → CGI → CLL → VDL → VAD → CDD → CGO → CER`")
    st.code(
        "Demurrage = [(CGO - CDD) - Free Days] x Tiered Rate\n"
        "Detention  = [(CER - CGO) - Free Days] x Tiered Rate\n"
        "\n"
        "Exclusion rule:\n"
        "  CANCELLED  -> excluded from analysis (all shipments)\n"
        "\n"
        "No CER handling:\n"
        "  ACTIVE     -> detention accumulates to today's date\n"
        "  COMPLETED  -> detention end = SHIPMENT_MODIFIED_DATE",
        language=None,
    )
    st.stop()

# ── Load & process ──
with st.spinner("Processing shipments against BAT contracts..."):
    raw_df = pd.read_csv(uploaded_file)
    rdf, total_shipments, cancelled_count = process_shipments(raw_df)

if len(rdf) == 0:
    st.error("No shipments matched BAT contracts. Check POD_LOCODE, CARRIER_SCAC, POL_LOCODE columns.")
    st.stop()

# ── Sidebar filters ──
with st.sidebar:
    st.markdown("---")
    st.markdown("### Filters")
    carriers = sorted(rdf["CARRIER_SCAC"].unique())
    sel_carriers = st.multiselect("Carrier", carriers, default=carriers)
    pods = sorted(rdf["POD_LOCODE"].unique())
    sel_pods = st.multiselect("POD Terminal", pods, default=pods)
    show_zero = st.checkbox("Include $0 charge shipments", value=True)

fdf = rdf[rdf["CARRIER_SCAC"].isin(sel_carriers) & rdf["POD_LOCODE"].isin(sel_pods)]
if not show_zero:
    fdf = fdf[fdf["TOTAL_DD_COST"] > 0]

# ─────────────────────────────────────────────
# HELPER: Clean download file
# ─────────────────────────────────────────────
def build_download_df(data):
    """Build a clean, easy-to-understand download DataFrame."""
    dl = data.copy()
    # Format dates
    for col in ["CDD", "CGO", "CER"]:
        if col in dl.columns:
            dl[col] = pd.to_datetime(dl[col], errors="coerce").dt.strftime("%Y-%m-%d %H:%M").fillna("")

    # Rename columns to human-readable names
    rename_map = {
        "SHIPMENT_ID": "Shipment ID",
        "CONTAINER_NUMBER": "Container",
        "CARRIER_SCAC": "Carrier SCAC",
        "CARRIER_NAME": "Carrier Name",
        "POL_LOCODE": "Port of Loading",
        "POD_LOCODE": "Port of Discharge",
        "LANE": "Lane",
        "SUBSCRIPTION_STATUS": "Subscription Status",
        "CDD": "Discharge Date (CDD)",
        "CGO": "Gate Out Date (CGO)",
        "CER": "Empty Return Date (CER)",
        "DEM_TOTAL_DAYS": "Demurrage Total Days",
        "DEM_CHARGEABLE_DAYS": "Demurrage Chargeable Days",
        "DEM_COST": "Demurrage Cost (USD)",
        "DET_TOTAL_DAYS": "Detention Total Days",
        "DET_CHARGEABLE_DAYS": "Detention Chargeable Days",
        "DET_COST": "Detention Cost (USD)",
        "TOTAL_DD_COST": "Total D&D Cost (USD)",
        "CONTRACT_TYPE": "Free Days Type",
        "FREE_DEM_DAYS": "Free Demurrage Days",
        "FREE_DET_DAYS": "Free Detention Days",
        "COMBINED_FREE_DAYS": "Combined Free Days",
        "DET_ACCUMULATING": "Detention Still Accumulating",
        "DET_END_SOURCE": "Detention End Date Source",
    }
    # Only rename columns that exist
    dl = dl.rename(columns={k: v for k, v in rename_map.items() if k in dl.columns})

    # Drop internal columns
    drop_cols = [c for c in ["POL", "POD", "LIFECYCLE_STATUS"] if c in dl.columns]
    dl = dl.drop(columns=drop_cols, errors="ignore")

    # Reorder: identifiers first, then events, then charges
    desired_order = [
        "Shipment ID", "Container", "Carrier SCAC", "Carrier Name",
        "Lane", "Port of Loading", "Port of Discharge", "Subscription Status",
        "Discharge Date (CDD)", "Gate Out Date (CGO)", "Empty Return Date (CER)",
        "Free Days Type", "Free Demurrage Days", "Free Detention Days", "Combined Free Days",
        "Demurrage Total Days", "Demurrage Chargeable Days", "Demurrage Cost (USD)",
        "Detention Total Days", "Detention Chargeable Days", "Detention Cost (USD)",
        "Total D&D Cost (USD)",
        "Detention Still Accumulating", "Detention End Date Source",
    ]
    existing = [c for c in desired_order if c in dl.columns]
    remaining = [c for c in dl.columns if c not in existing]
    dl = dl[existing + remaining]

    return dl


# ── TABS ──
tab_overview, tab_carrier, tab_port, tab_ships, tab_download, tab_logic = st.tabs(
    ["📊 Overview", "🚛 Carriers", "🏗️ Ports & Lanes", "📦 Shipments", "📥 Download", "⚙️ Logic"]
)

# ═══════════════════════════════════════════════
# TAB: OVERVIEW
# ═══════════════════════════════════════════════
with tab_overview:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(
        "Total D&D Cost", f"${fdf['TOTAL_DD_COST'].sum():,.0f}", f"{len(fdf)} matched of {total_shipments:,}",
        help="Combined demurrage + detention charges across all matched shipments. Only shipments with a matching BAT contract (POD + Carrier + POL) are included.",
    )
    c2.metric(
        "Demurrage", f"${fdf['DEM_COST'].sum():,.0f}", f"{(fdf['DEM_COST']>0).sum()} shipments",
        help="Cost for containers sitting at the port terminal after discharge (CDD) and before gate out (CGO). Charges start after free days are used up.",
    )
    c3.metric(
        "Detention", f"${fdf['DET_COST'].sum():,.0f}", f"{(fdf['DET_COST']>0).sum()} shipments",
        help="Cost for containers held outside the port after gate out (CGO) and before empty return (CER). Charges start after remaining free days are used up.",
    )
    c4.metric(
        "⚠️ Accumulating", f"{fdf['DET_ACCUMULATING'].sum()}", "ACTIVE, no CER",
        help="Shipments where the container was gated out but no empty return (CER) event was received. These are still ACTIVE, so detention is calculated up to today's date and keeps growing.",
    )

    c1, c2, c3, c4 = st.columns(4)
    avg_dem = fdf.loc[fdf["DEM_COST"] > 0, "DEM_CHARGEABLE_DAYS"].mean()
    avg_det = fdf.loc[fdf["DET_COST"] > 0, "DET_CHARGEABLE_DAYS"].mean()
    c1.metric(
        "Avg Dem Days", f"{avg_dem:.1f}d" if not np.isnan(avg_dem) else "—",
        help="Average number of chargeable demurrage days per shipment (only counting shipments that actually incurred demurrage). This is the time beyond free days that the container sat at the port terminal.",
    )
    c2.metric(
        "Avg Det Days", f"{avg_det:.1f}d" if not np.isnan(avg_det) else "—",
        help="Average number of chargeable detention days per shipment (only counting shipments that actually incurred detention). This is the time beyond free days that the container was held after gate out.",
    )
    c3.metric(
        "Within Free Days", f"{(fdf['TOTAL_DD_COST'] == 0).sum()}",
        help="Number of shipments where the total time (discharge to empty return) stayed within the contractual free days — so no D&D charges were incurred.",
    )
    c4.metric(
        "Max Single Shipment", f"${fdf['TOTAL_DD_COST'].max():,.0f}",
        help="The highest total D&D cost on a single shipment. Check the Shipments tab sorted by Total Cost to see which container this is.",
    )

    if cancelled_count > 0:
        st.caption(f"ℹ️ {cancelled_count} cancelled shipments excluded from analysis.")

    st.markdown("---")

    # ── Cost by Carrier (stacked bar) ──
    st.caption("💡 Which carriers are driving the most D&D cost? Orange = time at port (demurrage), purple = time after gate out (detention).")
    carrier_agg = (
        fdf.groupby("CARRIER_SCAC")
        .agg(Demurrage=("DEM_COST", "sum"), Detention=("DET_COST", "sum"))
        .reset_index()
    )
    carrier_melt = carrier_agg.melt(id_vars="CARRIER_SCAC", var_name="Type", value_name="Cost")
    carrier_melt["Cost"] = carrier_melt["Cost"].round(0)

    chart_carrier = (
        alt.Chart(carrier_melt)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            y=alt.Y("CARRIER_SCAC:N", sort="-x", title="Carrier"),
            x=alt.X("Cost:Q", title="Cost (USD)"),
            color=alt.Color("Type:N", scale=alt.Scale(domain=["Demurrage", "Detention"], range=[DEM_COLOR, DET_COLOR])),
            tooltip=["CARRIER_SCAC", "Type", alt.Tooltip("Cost:Q", format="$,.0f")],
        )
        .properties(title="D&D Cost by Carrier", height=250)
    )
    st.altair_chart(chart_carrier, use_container_width=True)

    # ── Cost by POD (stacked bar) ──
    st.caption("💡 Which port terminals are the most expensive? High demurrage = slow customs/pickup. High detention = consignee holding containers.")
    pod_agg = (
        fdf.groupby("POD_LOCODE")
        .agg(Demurrage=("DEM_COST", "sum"), Detention=("DET_COST", "sum"))
        .reset_index()
    )
    pod_melt = pod_agg.melt(id_vars="POD_LOCODE", var_name="Type", value_name="Cost")
    pod_melt["Cost"] = pod_melt["Cost"].round(0)

    chart_pod = (
        alt.Chart(pod_melt)
        .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
        .encode(
            y=alt.Y("POD_LOCODE:N", sort="-x", title="POD Terminal"),
            x=alt.X("Cost:Q", title="Cost (USD)"),
            color=alt.Color("Type:N", scale=alt.Scale(domain=["Demurrage", "Detention"], range=[DEM_COLOR, DET_COLOR])),
            tooltip=["POD_LOCODE", "Type", alt.Tooltip("Cost:Q", format="$,.0f")],
        )
        .properties(title="D&D Cost by POD Terminal", height=220)
    )
    st.altair_chart(chart_pod, use_container_width=True)


# ═══════════════════════════════════════════════
# TAB: CARRIERS
# ═══════════════════════════════════════════════
with tab_carrier:
    carrier_detail = (
        fdf.groupby(["CARRIER_SCAC", "CARRIER_NAME"])
        .agg(
            Ships=("SHIPMENT_ID", "count"),
            Dem_Ships=("DEM_COST", lambda x: (x > 0).sum()),
            Det_Ships=("DET_COST", lambda x: (x > 0).sum()),
            Dem_Cost=("DEM_COST", "sum"),
            Det_Cost=("DET_COST", "sum"),
            Avg_Dem_Days=("DEM_CHARGEABLE_DAYS", lambda x: x[x > 0].mean() if (x > 0).any() else 0),
            Avg_Det_Days=("DET_CHARGEABLE_DAYS", lambda x: x[x > 0].mean() if (x > 0).any() else 0),
        )
        .reset_index()
    )
    carrier_detail["Total_Cost"] = carrier_detail["Dem_Cost"] + carrier_detail["Det_Cost"]
    carrier_detail = carrier_detail.sort_values("Total_Cost", ascending=False)

    st.markdown("#### Carrier Summary")
    st.dataframe(
        carrier_detail.style.format({
            "Dem_Cost": "${:,.0f}", "Det_Cost": "${:,.0f}", "Total_Cost": "${:,.0f}",
            "Avg_Dem_Days": "{:.1f}", "Avg_Det_Days": "{:.1f}",
        }),
        use_container_width=True, hide_index=True,
    )

    st.markdown("---")
    st.markdown("#### Carrier × POD Breakdown")
    cp = (
        fdf.groupby(["CARRIER_SCAC", "POD_LOCODE"])
        .agg(Ships=("SHIPMENT_ID", "count"), Dem=("DEM_COST", "sum"), Det=("DET_COST", "sum"))
        .reset_index()
    )
    cp["Total"] = cp["Dem"] + cp["Det"]
    cp = cp[cp["Total"] > 0].sort_values("Total", ascending=False)

    # Heatmap
    if len(cp) > 0:
        heat = (
            alt.Chart(cp)
            .mark_rect(cornerRadius=4)
            .encode(
                x=alt.X("POD_LOCODE:N", title="POD"),
                y=alt.Y("CARRIER_SCAC:N", title="Carrier"),
                color=alt.Color("Total:Q", scale=alt.Scale(scheme="oranges"), title="Total D&D"),
                tooltip=["CARRIER_SCAC", "POD_LOCODE", "Ships",
                         alt.Tooltip("Dem:Q", format="$,.0f"),
                         alt.Tooltip("Det:Q", format="$,.0f"),
                         alt.Tooltip("Total:Q", format="$,.0f")],
            )
            .properties(title="Cost Heatmap: Carrier × POD", height=250)
        )
        text = heat.mark_text(fontSize=11, fontWeight="bold").encode(
            text=alt.Text("Total:Q", format="$,.0f"),
            color=alt.condition(alt.datum.Total > cp["Total"].median(), alt.value("white"), alt.value("black")),
        )
        st.altair_chart(heat + text, use_container_width=True)

    st.dataframe(
        cp.style.format({"Dem": "${:,.0f}", "Det": "${:,.0f}", "Total": "${:,.0f}"}),
        use_container_width=True, hide_index=True,
    )


# ═══════════════════════════════════════════════
# TAB: PORTS & LANES
# ═══════════════════════════════════════════════
with tab_port:
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### POD Terminal Summary")
        pod_sum = (
            fdf.groupby(["POD_LOCODE", "POD"])
            .agg(Ships=("SHIPMENT_ID", "count"), Dem=("DEM_COST", "sum"), Det=("DET_COST", "sum"))
            .reset_index()
        )
        pod_sum["Total"] = pod_sum["Dem"] + pod_sum["Det"]
        pod_sum = pod_sum.sort_values("Total", ascending=False)
        st.dataframe(
            pod_sum.style.format({"Dem": "${:,.0f}", "Det": "${:,.0f}", "Total": "${:,.0f}"}),
            use_container_width=True, hide_index=True,
        )
    with col2:
        st.markdown("#### Dem vs Det Split by POD")
        pod_melt2 = pod_sum.melt(id_vars="POD_LOCODE", value_vars=["Dem", "Det"], var_name="Type", value_name="Cost")
        ch = (
            alt.Chart(pod_melt2)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("POD_LOCODE:N", title="POD"),
                y=alt.Y("Cost:Q", title="Cost (USD)", stack=True),
                color=alt.Color("Type:N", scale=alt.Scale(domain=["Dem", "Det"], range=[DEM_COLOR, DET_COLOR])),
                tooltip=["POD_LOCODE", "Type", alt.Tooltip("Cost:Q", format="$,.0f")],
            )
            .properties(height=300)
        )
        st.altair_chart(ch, use_container_width=True)

    st.markdown("---")
    st.markdown("#### Top 20 Lanes by Total D&D Cost")
    lane_agg = (
        fdf.groupby("LANE")
        .agg(
            Ships=("SHIPMENT_ID", "count"),
            Carriers=("CARRIER_SCAC", lambda x: ", ".join(sorted(x.unique()))),
            Dem=("DEM_COST", "sum"), Det=("DET_COST", "sum"),
            Avg_Dem_Days=("DEM_CHARGEABLE_DAYS", lambda x: round(x[x > 0].mean(), 1) if (x > 0).any() else 0),
        )
        .reset_index()
    )
    lane_agg["Total"] = lane_agg["Dem"] + lane_agg["Det"]
    lane_agg = lane_agg.sort_values("Total", ascending=False).head(20)
    st.dataframe(
        lane_agg.style.format({"Dem": "${:,.0f}", "Det": "${:,.0f}", "Total": "${:,.0f}"}),
        use_container_width=True, hide_index=True,
    )


# ═══════════════════════════════════════════════
# TAB: SHIPMENT EXPLORER
# ═══════════════════════════════════════════════
with tab_ships:
    st.markdown("#### Shipment-Level D&D Detail")
    st.caption(f"Showing {len(fdf)} matched shipments. Use sidebar filters to narrow.")

    sort_col = st.selectbox("Sort by", ["TOTAL_DD_COST", "DEM_COST", "DET_COST", "DEM_CHARGEABLE_DAYS", "DET_CHARGEABLE_DAYS"])
    top_n = st.slider("Show top N", 10, min(500, len(fdf)), 50)

    display_cols = [
        "CONTAINER_NUMBER", "CARRIER_SCAC", "LANE",
        "CDD", "CGO", "CER",
        "DEM_TOTAL_DAYS", "DEM_CHARGEABLE_DAYS", "DEM_COST",
        "DET_TOTAL_DAYS", "DET_CHARGEABLE_DAYS", "DET_COST",
        "TOTAL_DD_COST", "CONTRACT_TYPE", "DET_ACCUMULATING", "DET_END_SOURCE",
    ]
    show_df = fdf[display_cols].sort_values(sort_col, ascending=False).head(top_n).copy()

    for dc in ["CDD", "CGO", "CER"]:
        show_df[dc] = pd.to_datetime(show_df[dc], errors="coerce").dt.strftime("%Y-%m-%d").fillna("—")

    show_df["DET_STATUS"] = show_df.apply(
        lambda r: "⚠️ Active → Today" if r["DET_ACCUMULATING"] else
                  ("📅 Completed → Modified" if r["DET_END_SOURCE"] == "MODIFIED_DATE" else "✓ CER"),
        axis=1,
    )
    show_df = show_df.drop(columns=["DET_ACCUMULATING", "DET_END_SOURCE"])

    st.dataframe(
        show_df.style.format({"DEM_COST": "${:,.2f}", "DET_COST": "${:,.2f}", "TOTAL_DD_COST": "${:,.2f}"}),
        use_container_width=True, hide_index=True, height=600,
    )

    # ── Distribution charts ──
    st.markdown("---")
    col1, col2 = st.columns(2)

    with col1:
        dem_data = fdf.loc[fdf["DEM_COST"] > 0, ["DEM_CHARGEABLE_DAYS"]].copy()
        if len(dem_data) > 0:
            ch = alt.Chart(dem_data).mark_bar(color=DEM_COLOR, cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
                x=alt.X("DEM_CHARGEABLE_DAYS:Q", bin=alt.Bin(maxbins=20), title="Chargeable Days"),
                y=alt.Y("count()", title="Shipments"),
            ).properties(title="Demurrage Days Distribution", height=220)
            st.altair_chart(ch, use_container_width=True)

    with col2:
        det_data = fdf.loc[fdf["DET_COST"] > 0, ["DET_CHARGEABLE_DAYS"]].copy()
        if len(det_data) > 0:
            ch = alt.Chart(det_data).mark_bar(color=DET_COLOR, cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
                x=alt.X("DET_CHARGEABLE_DAYS:Q", bin=alt.Bin(maxbins=20), title="Chargeable Days"),
                y=alt.Y("count()", title="Shipments"),
            ).properties(title="Detention Days Distribution", height=220)
            st.altair_chart(ch, use_container_width=True)

    # Scatter
    scatter_df = fdf[(fdf["DEM_CHARGEABLE_DAYS"] > 0) | (fdf["DET_CHARGEABLE_DAYS"] > 0)].copy()
    if len(scatter_df) > 0:
        ch = (
            alt.Chart(scatter_df)
            .mark_circle(opacity=0.7)
            .encode(
                x=alt.X("DEM_CHARGEABLE_DAYS:Q", title="Demurrage Days"),
                y=alt.Y("DET_CHARGEABLE_DAYS:Q", title="Detention Days"),
                size=alt.Size("TOTAL_DD_COST:Q", title="Total Cost", scale=alt.Scale(range=[30, 500])),
                color=alt.Color("CARRIER_SCAC:N", title="Carrier"),
                tooltip=["CONTAINER_NUMBER", "CARRIER_SCAC", "LANE",
                         alt.Tooltip("DEM_CHARGEABLE_DAYS:Q", format=".1f"),
                         alt.Tooltip("DET_CHARGEABLE_DAYS:Q", format=".1f"),
                         alt.Tooltip("TOTAL_DD_COST:Q", format="$,.0f")],
            )
            .properties(title="Demurrage vs Detention (bubble = total cost)", height=350)
        )
        st.altair_chart(ch, use_container_width=True)


# ═══════════════════════════════════════════════
# TAB: DOWNLOAD
# ═══════════════════════════════════════════════
with tab_download:
    st.markdown("#### 📥 Download Shipment-Level D&D Results")
    st.markdown("Clean, easy-to-read file with human-friendly column names. Ready to share with stakeholders.")

    dl_df = build_download_df(fdf)

    st.markdown(f"**Rows:** {len(dl_df)} | **Columns:** {len(dl_df.columns)}")

    # Preview
    st.dataframe(dl_df.head(10), use_container_width=True, hide_index=True)

    # CSV download
    csv_bytes = dl_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="📥 Download as CSV",
        data=csv_bytes,
        file_name=f"BAT_DD_Results_{datetime.now().strftime('%Y-%m-%d')}.csv",
        mime="text/csv",
    )

    # Excel download (with fallback if openpyxl not installed)
    try:
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            dl_df.to_excel(writer, sheet_name="D&D Results", index=False)

            # Also write a summary sheet
            summary_data = {
                "Metric": [
                    "Total Matched Shipments", "Cancelled Excluded",
                    "Total D&D Cost", "Total Demurrage", "Total Detention",
                    "Shipments with Dem Charges", "Shipments with Det Charges",
                    "Shipments Within Free Days", "Detention Accumulating (no CER)",
                    "Analysis Date",
                ],
                "Value": [
                    len(fdf), cancelled_count,
                    f"${fdf['TOTAL_DD_COST'].sum():,.2f}",
                    f"${fdf['DEM_COST'].sum():,.2f}",
                    f"${fdf['DET_COST'].sum():,.2f}",
                    (fdf["DEM_COST"] > 0).sum(),
                    (fdf["DET_COST"] > 0).sum(),
                    (fdf["TOTAL_DD_COST"] == 0).sum(),
                    fdf["DET_ACCUMULATING"].sum(),
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                ],
            }
            pd.DataFrame(summary_data).to_excel(writer, sheet_name="Summary", index=False)

            # Contract reference sheet
            cdf = pd.DataFrame(BAT_CONTRACTS)
            contract_cols = [
                "terminalIdentifier", "carrierScac", "ffwScac", "portOfLoadingLocode",
                "freeDemurrageDays", "firstDemurrageDays", "firstDemurrageRate",
                "secondDemurrageDays", "secondDemurrageRate", "thereafterDemurrageRate",
                "freeDetentionDays", "firstDetentionDays", "firstDetentionRate",
                "secondDetentionDays", "secondDetentionRate", "thereafterDetentionRate",
                "combinedFreeDays",
            ]
            cdf[contract_cols].to_excel(writer, sheet_name="Contracts", index=False)

        st.download_button(
            label="📥 Download as Excel (3 sheets: Results + Summary + Contracts)",
            data=buffer.getvalue(),
            file_name=f"BAT_DD_Results_{datetime.now().strftime('%Y-%m-%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except ImportError:
        st.info("Excel download requires openpyxl. Add `openpyxl` to requirements.txt, or use the CSV download above.")

    st.markdown("---")
    st.markdown("**Download contains 3 sheets:**")
    st.markdown("1. **D&D Results** — Every matched shipment with charges, dates, free days, and costs")
    st.markdown("2. **Summary** — KPI snapshot (total costs, counts, analysis date)")
    st.markdown("3. **Contracts** — BAT 2026 contract terms used for calculation")


# ═══════════════════════════════════════════════
# TAB: LOGIC
# ═══════════════════════════════════════════════
with tab_logic:
    st.markdown("### Calculation Logic")

    st.markdown("#### D&D Formulas")
    st.code(
        "Demurrage = [(Gate Out Full from POD - Discharge at POD) - Free Days] x Tiered Rate\n"
        "Detention  = [(Container Empty Return - Gate Out Full from POD) - Free Days] x Tiered Rate",
        language=None,
    )

    st.markdown("#### Exclusion Rule (all shipments)")
    st.code(
        "SUBSCRIPTION_STATUS = CANCELLED  -> Shipment excluded from D&D entirely",
        language=None,
    )

    st.markdown("#### No CER Handling (when CER is missing)")
    st.code(
        "SUBSCRIPTION_STATUS = ACTIVE    + no CER -> Detention accumulates to TODAY (analysis run time)\n"
        "SUBSCRIPTION_STATUS = COMPLETED + no CER -> Detention end = SHIPMENT_MODIFIED_DATE",
        language=None,
    )

    st.markdown("#### Tiered Rate Structure")
    st.code(
        "Days 1 to Tier1_days          -> Tier1_rate per day\n"
        "Days (Tier1+1) to (Tier1+Tier2) -> Tier2_rate per day\n"
        "Beyond                          -> Thereafter_rate per day",
        language=None,
    )

    st.markdown("#### Combined Free Days")
    st.code(
        "Combined free days are consumed continuously from discharge date.\n"
        "DEM applies while container is in terminal (CDD to CGO).\n"
        "DET applies after gate out (CGO to CER).\n"
        "Charges apply only after free days are exhausted, based on location.\n"
        "\n"
        "Example 1: 21 combined free, 25 days at terminal\n"
        "  DEM chargeable = 25 - 21 = 4 days (free pool exhausted at terminal)\n"
        "  DET gets 0 remaining free → charges from day 1 after gate out\n"
        "\n"
        "Example 2: 21 combined free, 9 days at terminal, 15 days after gate out\n"
        "  DEM chargeable = 0 (9 < 21, within free)\n"
        "  Remaining free for DET = 21 - 9 = 12 days\n"
        "  DET chargeable = 15 - 12 = 3 days",
        language=None,
    )

    st.markdown("#### Contract Matching")
    st.code(
        "Match Key = POD_LOCODE | CARRIER_SCAC | POL_LOCODE\n"
        "Fallback  = POD_LOCODE | FFW_SCAC (KHNN) | POL_LOCODE",
        language=None,
    )

    st.markdown("#### Milestone Chain")
    st.code(
        "CEP -> CGI -> CLL -> VDL -> VAD -> CDD -> CGO -> CER\n"
        "─────────────────────────────────────────────────────\n"
        "CEP = Container Empty Pickup\n"
        "CGI = Container Gate In (at POL)\n"
        "CLL = Container Loaded on Vessel\n"
        "VDL = Vessel Departure from Load Port\n"
        "VAD = Vessel Arrival at Discharge Port\n"
        "CDD = Container Discharge at POD    <- Demurrage starts\n"
        "CGO = Gate Out Full from POD         <- Demurrage ends / Detention starts\n"
        "CER = Container Empty Return         <- Detention ends\n"
        "\n"
        "If CER missing:\n"
        "  ACTIVE    -> detention end = today\n"
        "  COMPLETED -> detention end = SHIPMENT_MODIFIED_DATE\n"
        "\n"
        "CANCELLED shipments -> excluded from all calculations",
        language=None,
    )

    st.markdown("---")
    st.markdown("#### Contract Table (Hardcoded)")
    cdf = pd.DataFrame(BAT_CONTRACTS)
    contract_display_cols = [
        "terminalIdentifier", "carrierScac", "ffwScac", "portOfLoadingLocode",
        "freeDemurrageDays", "firstDemurrageDays", "firstDemurrageRate",
        "secondDemurrageDays", "secondDemurrageRate", "thereafterDemurrageRate",
        "freeDetentionDays", "firstDetentionDays", "firstDetentionRate",
        "secondDetentionDays", "secondDetentionRate", "thereafterDetentionRate",
        "combinedFreeDays",
    ]
    st.dataframe(cdf[contract_display_cols], use_container_width=True, hide_index=True, height=400)
