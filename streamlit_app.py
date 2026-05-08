from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ips_validator import IPSValidator

try:
    import ifcopenshell
    import ifcopenshell.util.element
except Exception:  # pragma: no cover
    ifcopenshell = None


st.set_page_config(page_title="Snowy 2.0 IPS IFC Validator + BIM View", layout="wide")
st.title("Snowy 2.0 IPS — IFC Validator Dashboard + BIM Segment View")
st.caption("Upload a clean/faulty/geometric IFC file, validate IPS rules R1–R8, and view tunnel segments coloured by validation status.")


def get_psets(product) -> Dict[str, Dict[str, Any]]:
    if ifcopenshell is None:
        return {}
    try:
        return ifcopenshell.util.element.get_psets(product) or {}
    except Exception:
        return {}


def extract_segments_from_ifc(ifc_path: str) -> pd.DataFrame:
    """Extract tunnel segment metadata from IFC property sets.

    This intentionally uses property sets rather than triangulated geometry, so it remains fast on Streamlit Cloud.
    """
    rows = []
    if ifcopenshell is None:
        return pd.DataFrame(rows)

    model = ifcopenshell.open(ifc_path)
    candidates = []
    for cls in ["IfcBuildingElementProxy", "IfcBuiltElement", "IfcElement"]:
        try:
            candidates.extend(model.by_type(cls))
        except Exception:
            pass

    seen = set()
    for product in candidates:
        if product.id() in seen:
            continue
        seen.add(product.id())
        psets = get_psets(product)
        hyd = psets.get("Pset_HydraulicPerformance_IPS", {})
        ident = psets.get("Pset_IPSIdentity", {})
        if not hyd:
            continue
        start = hyd.get("SegmentChainageStart")
        end = hyd.get("SegmentChainageEnd", None)
        diameter = hyd.get("InternalDiameter", 6.5)
        if start is None:
            continue
        if end is None:
            end = float(start) + 500.0
        rows.append({
            "ifc_guid": getattr(product, "GlobalId", ""),
            "name": getattr(product, "Name", "Tunnel Segment"),
            "ips_guid": ident.get("IPS_GUID", getattr(product, "GlobalId", "")),
            "start": float(start),
            "end": float(end),
            "diameter": float(diameter),
        })

    return pd.DataFrame(rows).sort_values("start").reset_index(drop=True) if rows else pd.DataFrame(rows)


def results_to_dataframe(report) -> pd.DataFrame:
    rows = []
    for r in report.results:
        rows.append({
            "Rule": r.rule_id,
            "Severity": r.severity.value.upper(),
            "Check": r.rule_name,
            "Segment / Joint": r.segment_guid or "—",
            "Message": r.message,
        })
    return pd.DataFrame(rows)


def severity_by_segment(df_results: pd.DataFrame, df_segments: pd.DataFrame) -> Dict[str, str]:
    order = {"PASS": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
    status = {row.ifc_guid: "PASS" for row in df_segments.itertuples()}

    for row in df_results.itertuples():
        target = str(getattr(row, "_4", ""))  # Segment / Joint column from itertuples may become _4
        sev = row.Severity
        for seg in df_segments.itertuples():
            keys = [str(seg.ifc_guid), str(seg.ips_guid), str(seg.name)]
            if target in keys or any(k and k in target for k in keys):
                if order.get(sev, 0) > order.get(status.get(seg.ifc_guid, "PASS"), 0):
                    status[seg.ifc_guid] = sev
    return status


def make_cylinder_mesh(x0: float, x1: float, radius: float, n: int = 48):
    xs, ys, zs = [], [], []
    for x in [x0, x1]:
        for i in range(n):
            theta = 2 * math.pi * i / n
            xs.append(x)
            ys.append(radius * math.cos(theta))
            zs.append(radius * math.sin(theta))
    i_faces, j_faces, k_faces = [], [], []
    for i in range(n):
        a = i
        b = (i + 1) % n
        c = n + i
        d = n + ((i + 1) % n)
        i_faces += [a, b]
        j_faces += [c, d]
        k_faces += [b, c]
    return xs, ys, zs, i_faces, j_faces, k_faces


def bim_view(df_segments: pd.DataFrame, status: Dict[str, str]) -> go.Figure:
    color_map = {"PASS": "green", "INFO": "grey", "WARN": "orange", "ERROR": "red"}
    fig = go.Figure()
    for seg in df_segments.itertuples():
        sev = status.get(seg.ifc_guid, "PASS")
        xs, ys, zs, ii, jj, kk = make_cylinder_mesh(seg.start, seg.end, seg.diameter / 2.0)
        fig.add_trace(go.Mesh3d(
            x=xs, y=ys, z=zs, i=ii, j=jj, k=kk,
            opacity=0.55,
            color=color_map.get(sev, "grey"),
            name=f"{seg.ips_guid} — {sev}",
            hovertemplate=(
                f"<b>{seg.name}</b><br>"
                f"IFC GUID: {seg.ifc_guid}<br>"
                f"IPS GUID: {seg.ips_guid}<br>"
                f"Chainage: {seg.start:.0f}–{seg.end:.0f} m<br>"
                f"Diameter: {seg.diameter:.2f} m<br>"
                f"Status: {sev}<extra></extra>"
            ),
        ))
    fig.update_layout(
        scene=dict(
            xaxis_title="Chainage / X (m)",
            yaxis_title="Y (m)",
            zaxis_title="Z (m)",
            aspectmode="data",
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        height=520,
        legend_title="Segments",
    )
    return fig


strict = st.toggle("Strict mode: treat warnings as rejection", value=True)
uploaded_file = st.file_uploader("Upload IFC file", type=["ifc"])

if uploaded_file is None:
    st.info("Upload `snowy2_geometric_clean.ifc`, `snowy2_geometric_faulty.ifc`, or a vendor IFC file exported from BIM software.")
    st.stop()

with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as tmp:
    tmp.write(uploaded_file.read())
    ifc_path = tmp.name

st.success(f"File uploaded: {uploaded_file.name}")

if st.button("Run validation and BIM view", type="primary"):
    validator = IPSValidator(ifc_path)
    report = validator.run_all()
    summary = report.summary()

    if summary["error"] > 0:
        verdict = "REJECTED"
    elif strict and summary["warn"] > 0:
        verdict = "REJECTED"
    elif summary["warn"] > 0:
        verdict = "CONDITIONAL"
    else:
        verdict = "ACCEPTED"

    st.subheader("Validation Summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Verdict", verdict)
    c2.metric("Pass", summary["pass"])
    c3.metric("Warnings", summary["warn"])
    c4.metric("Errors", summary["error"])

    df = results_to_dataframe(report)

    st.subheader("Rule Summary")
    rule_summary = df.groupby(["Rule", "Severity"]).size().reset_index(name="Count")
    st.dataframe(rule_summary, use_container_width=True)

    st.subheader("Rule-by-rule Results")
    st.dataframe(df, use_container_width=True)

    st.subheader("Action Items")
    action_items = df[df["Severity"].isin(["WARN", "ERROR"])].reset_index(drop=True)
    st.dataframe(action_items, use_container_width=True)

    df_segments = extract_segments_from_ifc(ifc_path)
    st.subheader("BIM Segment View")
    if df_segments.empty:
        st.warning("No IPS tunnel segment geometry/properties were found. Use the new geometric IFC generator, or check that segment property sets include Pset_HydraulicPerformance_IPS.")
    else:
        status = severity_by_segment(df, df_segments)
        fig = bim_view(df_segments, status)
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df_segments, use_container_width=True)

    report_json = json.dumps(report.to_dict(), indent=2, default=str)
    st.download_button(
        "Download JSON validation report",
        data=report_json,
        file_name="ips_validation_report.json",
        mime="application/json",
    )
