"""
streamlit_dashboard.py
======================

Streamlit dashboard for the Snowy 2.0 IPS validator.

For asset managers and EPC executives who don't want to touch Jupyter:

  1. Drag-drop an .ifc file (or click a button to generate a synthetic one)
  2. See the verdict — ACCEPTED / CONDITIONAL / REJECTED
  3. Drill into the eight rule outcomes
  4. Inspect per-segment evidence
  5. Download the JSON audit trail

Launch with:

    streamlit run streamlit_dashboard.py

Optional: drop ips_validator.py and generate_synthetic_ifc.py in the same
folder. The dashboard imports both at startup.

Author:  Eric's group, University of Melbourne
License: Apache-2.0
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from ips_validator import IPSValidator, IPSDictValidator, Severity


st.set_page_config(
    page_title="Snowy 2.0 IPS validator",
    page_icon="◢",
    layout="wide",
)


# Helpers --------------------------------------------------------------------

SEVERITY_COLORS = {
    "pass": "#639922",
    "info": "#888780",
    "warn": "#EF9F27",
    "error": "#E24B4A",
}

SEVERITY_BG = {
    "pass": "#EAF3DE",
    "info": "#F1EFE8",
    "warn": "#FAEEDA",
    "error": "#FCEBEB",
}


def compute_verdict(report) -> tuple[str, str]:
    """Return (verdict, banner_color) from a validation report."""
    s = report.summary()
    if s["error"] > 0:
        return "REJECTED", "error"
    if s["warn"] > 0:
        return "CONDITIONAL", "warn"
    return "ACCEPTED", "pass"


def render_kpi_row(report):
    s = report.summary()
    cols = st.columns(5)
    cols[0].metric("Segments", report.segment_count)
    cols[1].metric("Joints", report.joint_count)
    cols[2].metric("Pass", s["pass"])
    cols[3].metric("Warnings", s["warn"])
    cols[4].metric("Errors", s["error"])


def render_verdict_banner(report):
    verdict, color_key = compute_verdict(report)
    bg = SEVERITY_BG[color_key]
    fg = SEVERITY_COLORS[color_key]
    s = report.summary()

    if verdict == "ACCEPTED":
        text = "All eight rules pass. Submission may proceed to the next milestone."
    elif verdict == "CONDITIONAL":
        text = f"{s['warn']} warning(s) require explanation in the assessment cycle review."
    else:
        text = f"{s['error']} error(s) must be resolved at source before resubmission."

    st.markdown(
        f"""
        <div style="background:{bg};border-left:5px solid {fg};
                     padding:14px 18px;border-radius:6px;margin:8px 0 16px;">
          <div style="font-size:18px;font-weight:600;color:{fg}">{verdict}</div>
          <div style="font-size:14px;color:#444;margin-top:4px">{text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_rule_table(report):
    rows = []
    for r in report.results:
        rows.append({
            "Rule": r.rule_id,
            "Severity": r.severity.value.upper(),
            "Check": r.rule_name,
            "Segment": r.segment_guid or "—",
            "Message": r.message,
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_per_rule_summary(report):
    severity_order = {"pass": 0, "info": 1, "warn": 2, "error": 3}
    per_rule = {}
    for r in report.results:
        cur = per_rule.get(r.rule_id, "pass")
        if severity_order[r.severity.value] > severity_order[cur]:
            per_rule[r.rule_id] = r.severity.value
        elif r.rule_id not in per_rule:
            per_rule[r.rule_id] = r.severity.value

    rule_titles = {
        "R1": "Mass conservation",
        "R2": "Bernoulli consistency",
        "R3": "Einstein-Schwartz closure",
        "R4": "Stage applicability",
        "R5": "Source provenance",
        "R6": "Surrogate freshness",
        "R7": "Damage thresholds",
        "R8": "Joint topology",
    }

    cols = st.columns(4)
    for i, rule_id in enumerate(sorted(rule_titles.keys())):
        sev = per_rule.get(rule_id, "info")
        with cols[i % 4]:
            color = SEVERITY_COLORS[sev]
            bg = SEVERITY_BG[sev]
            label = rule_titles[rule_id]
            st.markdown(
                f"""
                <div style="background:{bg};border-radius:6px;padding:10px 12px;
                            margin-bottom:8px;border-left:4px solid {color}">
                  <div style="font-size:11px;color:#666;font-family:monospace">
                    {rule_id}
                  </div>
                  <div style="font-size:13px;font-weight:600;color:#222">
                    {label}
                  </div>
                  <div style="font-size:11px;color:{color};font-weight:600;margin-top:4px">
                    {sev.upper()}
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def offer_download(report, label: str):
    payload = json.dumps(report.to_dict(), indent=2, default=str)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    fname = f"validation_report_{label}_{ts}.json"
    st.download_button(
        label="Download JSON audit trail",
        data=payload,
        file_name=fname,
        mime="application/json",
    )


# Sidebar --------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Snowy 2.0 IPS validator")
    st.caption("Lifecycle sustainability MVD — IFC 4.3 + 7 Pset family")
    st.markdown("---")
    mode = st.radio(
        "Input mode",
        ["Upload .ifc", "Generate synthetic", "Try the demo dataset"],
        index=2,
    )
    st.markdown("---")
    strict = st.toggle("Strict mode", value=True,
                       help="If on, warnings count as failures.")
    show_evidence = st.toggle("Show full evidence per segment", value=False)
    st.markdown("---")
    st.caption("Built for asset managers and EPC executives.")
    st.caption("Source: ips_validator.py, generate_synthetic_ifc.py")


# Main page ------------------------------------------------------------------

st.markdown("## IFC submission validation")

report = None
label = "demo"

if mode == "Upload .ifc":
    uploaded = st.file_uploader("Drop your IFC 4.3 file here", type=["ifc"])
    if uploaded is not None:
        with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as tmp:
            tmp.write(uploaded.getvalue())
            tmp_path = tmp.name
        with st.spinner("Validating IFC submission..."):
            try:
                report = IPSValidator(tmp_path).run_all()
                label = Path(uploaded.name).stem
            except Exception as e:
                st.error(f"Could not validate: {e}")
    else:
        st.info("Upload an IFC 4.3 file to begin. Schema must include the seven Pset_*_IPS family members.")

elif mode == "Generate synthetic":
    st.write("Generate a synthetic Snowy 2.0 IPS IFC file using IfcOpenShell.")
    inject = st.checkbox("Inject realistic engineering errors", value=False)
    if st.button("Generate and validate"):
        try:
            from generate_synthetic_ifc import build_synthetic_ifc
            tmp_path = tempfile.NamedTemporaryFile(suffix=".ifc", delete=False).name
            with st.spinner("Building IFC and validating..."):
                summary = build_synthetic_ifc(tmp_path, inject_errors=inject)
                report = IPSValidator(tmp_path).run_all()
                label = "synthetic_faulty" if inject else "synthetic_clean"
            st.success(f"Built {summary['file_size_bytes']/1024:.1f} KB IFC with "
                       f"{summary['segments']} segments, {summary['joints']} joints.")
        except ImportError:
            st.error("ifcopenshell not installed. Run: pip install ifcopenshell")

else:
    st.write("Loading the in-memory Snowy 2.0 demo dataset (no IFC file required).")
    inject = st.checkbox("Inject realistic engineering errors", value=False)

    base_dataset = {
        "segments": [
            {"guid": "seg_1+250",
             "psets": {
                 "Pset_HydraulicPerformance_IPS": {
                     "SegmentChainageStart": 1000.0,
                     "InternalDiameter": 6.5, "InternalDiameter_Source": "DESIGN",
                     "DesignDischarge_Generating": 337.0,
                     "DesignDischarge_Source": "DESIGN",
                     "MeanFlowVelocity_Generating": 10.16,
                     "ManningRoughnessCoefficient": 0.012, "Manning_Source": "DESIGN",
                     "HeadLoss_Segment": 7.1,
                     "OperatingPressure_Mean": 4.92e6,
                     "WaterDensity": 999.7,
                 },
                 "Pset_CompositeLining_IPS": {
                     "RockMass_GSI": 58.0, "Rock_Source": "FIELD_MONITORING",
                     "RockMass_DeformationModulus": 1.2e10,
                     "EinsteinSchwartz_LoadShareConcrete": 0.32,
                     "EinsteinSchwartz_LoadShareSteelLiner": 0.30,
                     "EinsteinSchwartz_LoadShareRockMass": 0.38,
                     "ES_Source": "FLAC3D",
                 },
                 "Pset_FatigueDamage_IPS": {
                     "MinerDamageRatio_Cumulative": 0.31,
                     "Miner_Source": "SURROGATE_DEEPONET",
                     "MinerDamageThreshold_Action": 0.5,
                     "MinerDamageThreshold_Critical": 1.0,
                 },
                 "Pset_SurrogatePrediction_IPS": {
                     "PredictionTimestamp": "2026-04-25T10:00:00+00:00",
                     "PredictedDamageRatio_Mean": 0.31,
                 },
                 "Pset_AssessmentMeta_IPS": {
                     "LifecycleStage": "OPERATION",
                     "AssessmentTimestamp": "2026-05-08T14:00:00+00:00",
                 }}},
            {"guid": "seg_1+750",
             "psets": {
                 "Pset_HydraulicPerformance_IPS": {
                     "SegmentChainageStart": 1500.0,
                     "InternalDiameter": 6.5, "InternalDiameter_Source": "DESIGN",
                     "DesignDischarge_Generating": 337.0,
                     "DesignDischarge_Source": "DESIGN",
                     "MeanFlowVelocity_Generating": 10.16,
                     "HeadLoss_Segment": 7.1,
                     "OperatingPressure_Mean": 4.92e6 - 7.1 * 999.7 * 9.81,
                     "WaterDensity": 999.7,
                 },
                 "Pset_CompositeLining_IPS": {
                     "EinsteinSchwartz_LoadShareConcrete": 0.30,
                     "EinsteinSchwartz_LoadShareSteelLiner": 0.28,
                     "EinsteinSchwartz_LoadShareRockMass": 0.42,
                     "ES_Source": "FLAC3D",
                 },
                 "Pset_FatigueDamage_IPS": {
                     "MinerDamageThreshold_Action": 0.5,
                     "MinerDamageThreshold_Critical": 1.0,
                 },
                 "Pset_AssessmentMeta_IPS": {
                     "LifecycleStage": "OPERATION",
                     "AssessmentTimestamp": "2026-05-08T14:00:00+00:00",
                 }}},
            {"guid": "seg_2+250",
             "psets": {
                 "Pset_HydraulicPerformance_IPS": {
                     "SegmentChainageStart": 2000.0,
                     "InternalDiameter": 6.5, "InternalDiameter_Source": "DESIGN",
                     "DesignDischarge_Generating": 337.0,
                     "DesignDischarge_Source": "DESIGN",
                     "MeanFlowVelocity_Generating": 10.16,
                     "HeadLoss_Segment": 7.1,
                     "OperatingPressure_Mean": 4.92e6 - 2 * 7.1 * 999.7 * 9.81,
                     "WaterDensity": 999.7,
                 },
                 "Pset_CompositeLining_IPS": {
                     "EinsteinSchwartz_LoadShareConcrete": 0.31,
                     "EinsteinSchwartz_LoadShareSteelLiner": 0.29,
                     "EinsteinSchwartz_LoadShareRockMass": 0.40,
                     "ES_Source": "FLAC3D",
                 },
                 "Pset_FatigueDamage_IPS": {
                     "MinerDamageThreshold_Action": 0.5,
                     "MinerDamageThreshold_Critical": 1.0,
                 },
                 "Pset_AssessmentMeta_IPS": {
                     "LifecycleStage": "OPERATION",
                     "AssessmentTimestamp": "2026-05-08T14:00:00+00:00",
                 }}},
        ],
        "joints": [
            {"guid": "joint_1500",
             "psets": {"Pset_FACSJoint_IPS": {
                 "JointGUID_Upstream": "seg_1+250",
                 "JointGUID_Downstream": "seg_1+750",
                 "JointStationing": 1500.0}}},
            {"guid": "joint_2000",
             "psets": {"Pset_FACSJoint_IPS": {
                 "JointGUID_Upstream": "seg_1+750",
                 "JointGUID_Downstream": "seg_2+250",
                 "JointStationing": 2000.0}}},
        ],
    }

    if inject:
        import copy
        d = copy.deepcopy(base_dataset)
        d["segments"][1]["psets"]["Pset_HydraulicPerformance_IPS"]["DesignDischarge_Generating"] = 250.0
        d["segments"][0]["psets"]["Pset_CompositeLining_IPS"]["EinsteinSchwartz_LoadShareConcrete"] = 0.40
        del d["segments"][0]["psets"]["Pset_HydraulicPerformance_IPS"]["DesignDischarge_Source"]
        d["segments"][0]["psets"]["Pset_SurrogatePrediction_IPS"]["PredictionTimestamp"] = "2026-01-15T10:00:00+00:00"
        d["segments"][2]["psets"]["Pset_FatigueDamage_IPS"]["MinerDamageThreshold_Action"] = 1.0
        d["segments"][2]["psets"]["Pset_FatigueDamage_IPS"]["MinerDamageThreshold_Critical"] = 0.8
        d["joints"][0]["psets"]["Pset_FACSJoint_IPS"]["JointGUID_Downstream"] = "missing"
        report = IPSDictValidator(d).run_all()
        label = "demo_faulty"
    else:
        report = IPSDictValidator(base_dataset).run_all()
        label = "demo_clean"


if report is not None:
    render_verdict_banner(report)
    render_kpi_row(report)
    st.markdown("### Per-rule status")
    render_per_rule_summary(report)
    st.markdown("### Detailed rule outcomes")
    render_rule_table(report)
    if show_evidence:
        st.markdown("### Per-segment evidence")
        for r in report.results:
            if r.severity in (Severity.WARN, Severity.ERROR):
                color = SEVERITY_COLORS[r.severity.value]
                bg = SEVERITY_BG[r.severity.value]
                st.markdown(
                    f"""<div style="background:{bg};padding:10px 14px;
                                    border-radius:6px;border-left:4px solid {color};
                                    margin-bottom:8px">
                          <div style="font-size:13px;font-weight:600">
                            {r.rule_id} — {r.rule_name}
                          </div>
                          <div style="font-size:11px;color:#666;
                                      font-family:monospace">
                            Segment: {r.segment_guid or '—'}
                          </div>
                          <div style="font-size:13px;margin-top:6px">{r.message}</div>
                          {f"<pre style='font-size:11px;background:white;padding:8px;border-radius:4px;margin-top:6px'>{json.dumps(r.details, indent=2, default=str)}</pre>" if r.details else ""}
                        </div>""",
                    unsafe_allow_html=True,
                )

    st.markdown("---")
    cols = st.columns([1, 3])
    with cols[0]:
        offer_download(report, label)
    with cols[1]:
        st.caption(
            f"Run timestamp {datetime.now(timezone.utc).isoformat()} · "
            f"Schema {report.schema} · Validator v0.4.0 (8 rules)"
        )
