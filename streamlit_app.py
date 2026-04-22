"""
BAT Demurrage & Detention (D&D) Analyzer
=========================================
Standalone Streamlit app for BAT ocean shipment D&D cost analysis.

Calculation Logic (from D&D PM):
  Demurrage = [(Gate Out Full from POD - Discharge at POD) - Free Dem Days] × Tiered Rate
  Detention = [(Container Empty Return - Gate Out Full from POD) - Free Det Days] × Tiered Rate

No CER handling:
  CANCELLED  → shipment excluded from D&D entirely
  ACTIVE     → detention accumulates to today's date (analysis run time)
  COMPLETED  → detention end = SHIPMENT_MODIFIED_DATE

Combined Free Days: Demurrage consumes from pool first, detention gets remainder.

Run: streamlit run bat_dd_analyzer.py
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

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
# CUSTOM CSS
# ─────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
    
    .block-container { padding-top: 1.5rem; max-width: 1200px; }
    
    .kpi-card {
        background: linear-gradient(135deg, #1a1d28 0%, #141720 100%);
        border: 1px solid #2a2d3a;
        border-radius: 10px;
        padding: 18px 20px;
        text-align: center;
    }
    .kpi-label {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 1px;
        color: #8b8fa4;
        margin-bottom: 4px;
        font-weight: 600;
    }
    .kpi-value {
        font-family: 'JetBrains Mono', monospace;
        font-size: 28px;
        font-weight: 700;
        letter-spacing: -1px;
    }
    .kpi-sub { font-size: 12px; color: #5a5e72; margin-top: 2px; }
    .dem-color { color: #f5a623; }
    .det-color { color: #7b61ff; }
    .total-color { color: #00d4aa; }
    .alert-color { color: #ff5c5c; }
    .blue-color { color: #5b9aff; }
    
    .logic-block {
        background: #141720;
        border: 1px solid #2a2d3a;
        border-radius: 8px;
        padding: 16px 20px;
        margin-bottom: 12px;
    }
    .logic-block h4 { color: #00d4aa; margin-bottom: 8px; }
    .logic-block code {
        font-family: 'JetBrains Mono', monospace;
        font-size: 13px;
        color: #8b8fa4;
        line-height: 2;
    }
    
    div[data-testid="stMetric"] {
        background: #1a1d28;
        border: 1px solid #2a2d3a;
        border-radius: 10px;
        padding: 12px 16px;
    }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# HARDCODED BAT CONTRACTS (2026)
# ─────────────────────────────────────────────
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
      - SUBSCRIPTION_STATUS = CANCELLED  →  exclude shipment entirely
      - SUBSCRIPTION_STATUS = ACTIVE     →  detention accumulates to today's date
      - SUBSCRIPTION_STATUS = COMPLETED  →  detention end = SHIPMENT_MODIFIED_DATE
    
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
PLOT_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="DM Sans, sans-serif", color="#e8eaf0", size=12),
    margin=dict(l=20, r=20, t=40, b=20),
    xaxis=dict(gridcolor="rgba(255,255,255,0.05)", zerolinecolor="rgba(255,255,255,0.05)"),
    yaxis=dict(gridcolor="rgba(255,255,255,0.05)", zerolinecolor="rgba(255,255,255,0.05)"),
    legend=dict(bgcolor="rgba(0,0,0,0)"),
)
DEM_COLOR = "#f5a623"
DET_COLOR = "#7b61ff"
TOTAL_COLOR = "#00d4aa"
ALERT_COLOR = "#ff5c5c"


# ─────────────────────────────────────────────
# STREAMLIT APP
# ─────────────────────────────────────────────
def render_kpi(label, value, color_class=""):
    st.markdown(
        f"""<div class="kpi-card">
            <div class="kpi-label">{label}</div>
            <div class="kpi-value {color_class}">{value}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def render_kpi_with_sub(label, value, sub, color_class=""):
    st.markdown(
        f"""<div class="kpi-card">
            <div class="kpi-label">{label}</div>
            <div class="kpi-value {color_class}">{value}</div>
            <div class="kpi-sub">{sub}</div>
        </div>""",
        unsafe_allow_html=True,
    )


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
    st.markdown(
        "**Terminals:** USORF, HRRJK, NGAPP, NGTIN"
    )
    st.markdown(
        "**Carriers:** CMDU, HLCU, MSCU, ONEY, OOLU + KHNN (FFW)"
    )

if uploaded_file is None:
    st.markdown("## 🚢 BAT Demurrage & Detention Analyzer")
    st.markdown("---")
    st.info("Upload a D&D shipment CSV from the sidebar to get started.")
    st.markdown(
        """
        **Expected CSV columns (milestones):**
        
        `CEP` → `CGI` → `CLL` → `VDL` → `VAD` → `CDD` → `CGO` → `CER`
        
        Plus: `SHIPMENT_ID`, `CONTAINER_NUMBER`, `CARRIER_SCAC`, `POL_LOCODE`, `POD_LOCODE`, `REPORTING_DATE`
        """
    )
    st.markdown("---")
    st.markdown("**Calculation Logic:**")
    st.code(
        "Demurrage = [(CGO − CDD) − Free Days] × Tiered Rate\n"
        "Detention  = [(CER − CGO) − Free Days] × Tiered Rate\n"
        "\n"
        "No CER handling:\n"
        "  CANCELLED  → excluded from analysis\n"
        "  ACTIVE     → detention accumulates to today's date\n"
        "  COMPLETED  → detention end = SHIPMENT_MODIFIED_DATE",
        language=None,
    )
    st.stop()

# ── Load & process ──
with st.spinner("Processing shipments against BAT contracts..."):
    raw_df = pd.read_csv(uploaded_file)
    rdf, total_shipments, cancelled_count = process_shipments(raw_df)

if len(rdf) == 0:
    st.error("No shipments matched BAT contracts. Check that your CSV has POD_LOCODE, CARRIER_SCAC, POL_LOCODE columns.")
    st.stop()

# ── Sidebar filters ──
with st.sidebar:
    st.markdown("---")
    st.markdown("### Filters")
    carriers = sorted(rdf["CARRIER_SCAC"].unique())
    sel_carriers = st.multiselect("Carrier", carriers, default=carriers)
    pods = sorted(rdf["POD_LOCODE"].unique())
    sel_pods = st.multiselect("POD Terminal", pods, default=pods)
    show_zero = st.checkbox("Include shipments with $0 charges", value=True)

# Apply filters
fdf = rdf[rdf["CARRIER_SCAC"].isin(sel_carriers) & rdf["POD_LOCODE"].isin(sel_pods)]
if not show_zero:
    fdf = fdf[fdf["TOTAL_DD_COST"] > 0]

# ── TABS ──
tab_overview, tab_carrier, tab_port, tab_ships, tab_dist, tab_logic = st.tabs(
    ["📊 Overview", "🚛 Carriers", "🏗️ Ports & Lanes", "📦 Shipments", "📈 Distribution", "⚙️ Logic"]
)

# ─────────── TAB: OVERVIEW ───────────
with tab_overview:
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_kpi_with_sub("Total D&D Cost", f"${fdf['TOTAL_DD_COST'].sum():,.0f}", f"{len(fdf)} matched of {total_shipments:,}", "total-color")
    with c2:
        render_kpi_with_sub("Demurrage", f"${fdf['DEM_COST'].sum():,.0f}", f"{(fdf['DEM_COST']>0).sum()} shipments incurred", "dem-color")
    with c3:
        render_kpi_with_sub("Detention", f"${fdf['DET_COST'].sum():,.0f}", f"{(fdf['DET_COST']>0).sum()} shipments incurred", "det-color")
    with c4:
        render_kpi_with_sub("Accumulating", f"{fdf['DET_ACCUMULATING'].sum()}", "ACTIVE, no CER → using today", "alert-color")

    st.markdown("")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        render_kpi("Avg Dem Days (chargeable)", f"{fdf.loc[fdf['DEM_COST']>0, 'DEM_CHARGEABLE_DAYS'].mean():.1f}d" if (fdf['DEM_COST']>0).any() else "—")
    with c2:
        render_kpi("Avg Det Days (chargeable)", f"{fdf.loc[fdf['DET_COST']>0, 'DET_CHARGEABLE_DAYS'].mean():.1f}d" if (fdf['DET_COST']>0).any() else "—")
    with c3:
        render_kpi("Within Free Days", f"{(fdf['TOTAL_DD_COST']==0).sum()}")
    with c4:
        render_kpi("Max Single Shipment", f"${fdf['TOTAL_DD_COST'].max():,.0f}")

    if cancelled_count > 0:
        st.caption(f"ℹ️ {cancelled_count} cancelled shipments excluded from analysis.")

    st.markdown("---")

    # Cost by Carrier
    carrier_agg = (
        fdf.groupby("CARRIER_SCAC")
        .agg(DEM=("DEM_COST", "sum"), DET=("DET_COST", "sum"), Count=("SHIPMENT_ID", "count"))
        .reset_index()
        .sort_values("DEM", ascending=False)
    )
    carrier_agg["Total"] = carrier_agg["DEM"] + carrier_agg["DET"]
    carrier_agg = carrier_agg.sort_values("Total", ascending=True)

    fig = go.Figure()
    fig.add_trace(go.Bar(y=carrier_agg["CARRIER_SCAC"], x=carrier_agg["DEM"], name="Demurrage", orientation="h", marker_color=DEM_COLOR))
    fig.add_trace(go.Bar(y=carrier_agg["CARRIER_SCAC"], x=carrier_agg["DET"], name="Detention", orientation="h", marker_color=DET_COLOR))
    fig.update_layout(**PLOT_LAYOUT, title="D&D Cost by Carrier", barmode="stack", height=300)
    st.plotly_chart(fig, use_container_width=True)

    # Cost by POD
    pod_agg = (
        fdf.groupby("POD_LOCODE")
        .agg(DEM=("DEM_COST", "sum"), DET=("DET_COST", "sum"))
        .reset_index()
    )
    pod_agg["Total"] = pod_agg["DEM"] + pod_agg["DET"]
    pod_agg = pod_agg.sort_values("Total", ascending=True)

    fig2 = go.Figure()
    fig2.add_trace(go.Bar(y=pod_agg["POD_LOCODE"], x=pod_agg["DEM"], name="Demurrage", orientation="h", marker_color=DEM_COLOR))
    fig2.add_trace(go.Bar(y=pod_agg["POD_LOCODE"], x=pod_agg["DET"], name="Detention", orientation="h", marker_color=DET_COLOR))
    fig2.update_layout(**PLOT_LAYOUT, title="D&D Cost by POD Terminal", barmode="stack", height=280)
    st.plotly_chart(fig2, use_container_width=True)

# ─────────── TAB: CARRIERS ───────────
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

    st.dataframe(
        carrier_detail.style.format(
            {"Dem_Cost": "${:,.0f}", "Det_Cost": "${:,.0f}", "Total_Cost": "${:,.0f}", "Avg_Dem_Days": "{:.1f}", "Avg_Det_Days": "{:.1f}"}
        ),
        use_container_width=True,
        hide_index=True,
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

    fig = px.treemap(
        cp, path=["CARRIER_SCAC", "POD_LOCODE"], values="Total",
        color="Total", color_continuous_scale=["#1a1d28", DEM_COLOR, ALERT_COLOR],
        title="Carrier × POD Cost Treemap",
    )
    fig.update_layout(**PLOT_LAYOUT, height=400)
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        cp.style.format({"Dem": "${:,.0f}", "Det": "${:,.0f}", "Total": "${:,.0f}"}),
        use_container_width=True,
        hide_index=True,
    )

# ─────────── TAB: PORTS & LANES ───────────
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
            use_container_width=True,
            hide_index=True,
        )

    with col2:
        st.markdown("#### Dem vs Det Split by POD")
        fig = go.Figure()
        fig.add_trace(go.Bar(x=pod_sum["POD_LOCODE"], y=pod_sum["Dem"], name="Demurrage", marker_color=DEM_COLOR))
        fig.add_trace(go.Bar(x=pod_sum["POD_LOCODE"], y=pod_sum["Det"], name="Detention", marker_color=DET_COLOR))
        fig.update_layout(**PLOT_LAYOUT, barmode="stack", height=350)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    st.markdown("#### Top 20 Lanes by Total D&D Cost")
    lane_agg = (
        fdf.groupby("LANE")
        .agg(
            Ships=("SHIPMENT_ID", "count"),
            Carriers=("CARRIER_SCAC", lambda x: ", ".join(sorted(x.unique()))),
            Dem=("DEM_COST", "sum"),
            Det=("DET_COST", "sum"),
            Avg_Dem_Days=("DEM_CHARGEABLE_DAYS", lambda x: round(x[x > 0].mean(), 1) if (x > 0).any() else 0),
        )
        .reset_index()
    )
    lane_agg["Total"] = lane_agg["Dem"] + lane_agg["Det"]
    lane_agg = lane_agg.sort_values("Total", ascending=False).head(20)
    st.dataframe(
        lane_agg.style.format({"Dem": "${:,.0f}", "Det": "${:,.0f}", "Total": "${:,.0f}"}),
        use_container_width=True,
        hide_index=True,
    )

# ─────────── TAB: SHIPMENT EXPLORER ───────────
with tab_ships:
    st.markdown("#### Shipment-Level D&D Detail")
    st.markdown(f"*Showing {len(fdf)} matched shipments. Use sidebar filters to narrow.*")

    sort_col = st.selectbox("Sort by", ["TOTAL_DD_COST", "DEM_COST", "DET_COST", "DEM_CHARGEABLE_DAYS", "DET_CHARGEABLE_DAYS"], index=0)
    top_n = st.slider("Show top N", 10, min(500, len(fdf)), 50)

    display_cols = [
        "CONTAINER_NUMBER", "CARRIER_SCAC", "LANE",
        "CDD", "CGO", "CER",
        "DEM_TOTAL_DAYS", "DEM_CHARGEABLE_DAYS", "DEM_COST",
        "DET_TOTAL_DAYS", "DET_CHARGEABLE_DAYS", "DET_COST",
        "TOTAL_DD_COST", "CONTRACT_TYPE", "DET_ACCUMULATING", "DET_END_SOURCE",
    ]
    show_df = fdf[display_cols].sort_values(sort_col, ascending=False).head(top_n).copy()

    # Format dates for display
    for dc in ["CDD", "CGO", "CER"]:
        show_df[dc] = show_df[dc].dt.strftime("%Y-%m-%d %H:%M").fillna("—")

    # Status column: shows what date was used for detention end
    show_df["DET_STATUS"] = show_df.apply(
        lambda r: "⚠️ Active → Today" if r["DET_ACCUMULATING"] else
                  ("📅 Completed → Modified" if r["DET_END_SOURCE"] == "MODIFIED_DATE" else "✓ CER"),
        axis=1,
    )
    show_df = show_df.drop(columns=["DET_ACCUMULATING", "DET_END_SOURCE"])

    st.dataframe(
        show_df.style.format(
            {"DEM_COST": "${:,.2f}", "DET_COST": "${:,.2f}", "TOTAL_DD_COST": "${:,.2f}"}
        ),
        use_container_width=True,
        hide_index=True,
        height=600,
    )

    # Download
    csv_out = fdf.to_csv(index=False)
    st.download_button("📥 Download Full Results CSV", csv_out, "bat_dd_results.csv", "text/csv")

# ─────────── TAB: DISTRIBUTION ───────────
with tab_dist:
    col1, col2 = st.columns(2)
    with col1:
        dem_data = fdf.loc[fdf["DEM_COST"] > 0, "DEM_CHARGEABLE_DAYS"]
        if len(dem_data) > 0:
            fig = px.histogram(dem_data, nbins=20, title="Demurrage Chargeable Days", color_discrete_sequence=[DEM_COLOR])
            fig.update_layout(**PLOT_LAYOUT, height=300, showlegend=False)
            fig.update_xaxes(title="Days")
            fig.update_yaxes(title="Shipments")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No demurrage charges to display.")

    with col2:
        det_data = fdf.loc[fdf["DET_COST"] > 0, "DET_CHARGEABLE_DAYS"]
        if len(det_data) > 0:
            fig = px.histogram(det_data, nbins=20, title="Detention Chargeable Days", color_discrete_sequence=[DET_COLOR])
            fig.update_layout(**PLOT_LAYOUT, height=300, showlegend=False)
            fig.update_xaxes(title="Days")
            fig.update_yaxes(title="Shipments")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No detention charges to display.")

    cost_data = fdf.loc[fdf["TOTAL_DD_COST"] > 0, "TOTAL_DD_COST"]
    if len(cost_data) > 0:
        fig = px.histogram(cost_data, nbins=30, title="Total D&D Cost per Shipment (USD)", color_discrete_sequence=[TOTAL_COLOR])
        fig.update_layout(**PLOT_LAYOUT, height=300, showlegend=False)
        fig.update_xaxes(title="Cost (USD)")
        fig.update_yaxes(title="Shipments")
        st.plotly_chart(fig, use_container_width=True)

    # Scatter: Dem days vs Det days
    scatter_df = fdf[(fdf["DEM_CHARGEABLE_DAYS"] > 0) | (fdf["DET_CHARGEABLE_DAYS"] > 0)].copy()
    if len(scatter_df) > 0:
        fig = px.scatter(
            scatter_df,
            x="DEM_CHARGEABLE_DAYS",
            y="DET_CHARGEABLE_DAYS",
            color="CARRIER_SCAC",
            size="TOTAL_DD_COST",
            hover_data=["CONTAINER_NUMBER", "LANE"],
            title="Demurrage vs Detention Days (bubble = total cost)",
            color_discrete_sequence=[DEM_COLOR, DET_COLOR, TOTAL_COLOR, ALERT_COLOR, "#5b9aff", "#ff9f43"],
        )
        fig.update_layout(**PLOT_LAYOUT, height=400)
        st.plotly_chart(fig, use_container_width=True)

# ─────────── TAB: LOGIC ───────────
with tab_logic:
    st.markdown("### Calculation Logic")
    st.markdown(
        """
        <div class="logic-block">
            <h4>D&D Formulas</h4>
            <code>
                <span style="color:#f5a623">Demurrage</span> = [(Gate Out Full from POD − Discharge at POD) − Free Days] × Tiered Rate<br>
                <span style="color:#7b61ff">Detention</span> &nbsp;= [(Container Empty Return − Gate Out Full from POD) − Free Days] × Tiered Rate
            </code>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="logic-block">
            <h4>No CER Handling (Detention End Date)</h4>
            <code>
                SUBSCRIPTION_STATUS = <span style="color:#ff5c5c">CANCELLED</span> → Shipment excluded from D&D entirely<br>
                SUBSCRIPTION_STATUS = <span style="color:#f5a623">ACTIVE</span> &nbsp;&nbsp;&nbsp;&nbsp;+ no CER → Detention accumulates to <b>today's date</b> (analysis run time)<br>
                SUBSCRIPTION_STATUS = <span style="color:#00d4aa">COMPLETED</span> + no CER → Detention end = <b>SHIPMENT_MODIFIED_DATE</b>
            </code>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="logic-block">
            <h4>Tiered Rate Structure</h4>
            <code>
                Days 1 to Tier1_days → Tier1_rate per day<br>
                Days (Tier1+1) to (Tier1+Tier2) → Tier2_rate per day<br>
                Beyond → Thereafter_rate per day
            </code>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="logic-block">
            <h4>Combined Free Days</h4>
            <code>
                Demurrage eats from the combined pool first.<br>
                Detention uses the remaining free days.<br>
                Example: 21 combined free days, 18 dem days → 3 free det days left.
            </code>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="logic-block">
            <h4>Contract Matching</h4>
            <code>
                Match Key = POD_LOCODE | CARRIER_SCAC | POL_LOCODE<br>
                Fallback  = POD_LOCODE | FFW_SCAC (KHNN) | POL_LOCODE
            </code>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        """
        <div class="logic-block">
            <h4>Milestone Chain (Input CSV)</h4>
            <code>
                CEP → CGI → CLL → VDL → VAD → CDD → CGO → CER<br>
                ─────────────────────────────────────────────────<br>
                CEP = Container Empty Pickup<br>
                CGI = Container Gate In (at POL)<br>
                CLL = Container Loaded on Vessel<br>
                VDL = Vessel Departure from Load Port<br>
                VAD = Vessel Arrival at Discharge Port<br>
                CDD = Container Discharge at POD &nbsp;&nbsp;← <span style="color:#f5a623">Demurrage starts</span><br>
                CGO = Gate Out Full from POD &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;← <span style="color:#f5a623">Demurrage ends</span> / <span style="color:#7b61ff">Detention starts</span><br>
                CER = Container Empty Return &nbsp;&nbsp;&nbsp;&nbsp;← <span style="color:#7b61ff">Detention ends</span><br>
                <br>
                If CER missing:<br>
                &nbsp;&nbsp;CANCELLED → <span style="color:#ff5c5c">excluded entirely</span><br>
                &nbsp;&nbsp;ACTIVE &nbsp;&nbsp;&nbsp;→ <span style="color:#f5a623">detention end = today (analysis run date)</span><br>
                &nbsp;&nbsp;COMPLETED → <span style="color:#00d4aa">detention end = SHIPMENT_MODIFIED_DATE</span>
            </code>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.markdown("#### Contract Table (Hardcoded)")
    cdf = pd.DataFrame(BAT_CONTRACTS)
    display_contract_cols = [
        "terminalIdentifier", "carrierScac", "ffwScac", "portOfLoadingLocode",
        "freeDemurrageDays", "firstDemurrageDays", "firstDemurrageRate",
        "secondDemurrageDays", "secondDemurrageRate", "thereafterDemurrageRate",
        "freeDetentionDays", "firstDetentionDays", "firstDetentionRate",
        "secondDetentionDays", "secondDetentionRate", "thereafterDetentionRate",
        "combinedFreeDays",
    ]
    st.dataframe(cdf[display_contract_cols], use_container_width=True, hide_index=True, height=400)
