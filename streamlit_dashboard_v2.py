"""
streamlit_dashboard_v2.py
=========================

Streamlit dashboard for the Snowy 2.0 IPS validator with parameterised
geometric IFC generation.

What's new in v2
----------------
- Sidebar sliders for outer radius, lining thickness, display length, gap,
  joint radius and joint height — every visible dimension is now live.
- Per-segment editor (tabs) for chainage start/end, GSI, discharge, Miner
  damage ratio, load shares, crack count and aperture.
- Live 2D plan preview (SVG) showing what the geometry will look like
  before the IFC is generated.
- Pre-build sanity check shown inline as the user adjusts parameters.
- Generate-and-validate button that produces the IFC, runs the eight-rule
  validator on it, and offers downloads of the IFC plus JSON audit trail.

Launch with:

    streamlit run streamlit_dashboard_v2.py

Files needed in the same folder:

    ips_validator.py
    generate_geometric_ifc_param.py
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

from ips_validator import IPSValidator, Severity
from generate_geometric_ifc_param import (
    GeometryParams,
    SegmentSpec,
    build_parametric_geometric_ifc,
)


st.set_page_config(
    page_title="Snowy 2.0 IPS — parametric IFC generator",
    page_icon="◢",
    layout="wide",
)


SEVERITY_COLORS = {
    "pass": "#639922", "info": "#888780",
    "warn": "#EF9F27", "error": "#E24B4A",
}
SEVERITY_BG = {
    "pass": "#EAF3DE", "info": "#F1EFE8",
    "warn": "#FAEEDA", "error": "#FCEBEB",
}


# ----- Session state init ---------------------------------------------------

def _default_params() -> GeometryParams:
    return GeometryParams()


if "geo_params" not in st.session_state:
    st.session_state.geo_params = _default_params()
if "last_build" not in st.session_state:
    st.session_state.last_build = None
if "last_validation" not in st.session_state:
    st.session_state.last_validation = None
if "last_ifc_bytes" not in st.session_state:
    st.session_state.last_ifc_bytes = None


# ----- Sidebar — global geometry parameters --------------------------------

with st.sidebar:
    st.markdown("### Snowy 2.0 IPS")
    st.caption("Parametric IFC4X3 generator + 8-rule validator")
    st.markdown("---")

    st.markdown("**Tunnel cross-section**")
    outer_radius = st.slider(
        "Outer radius (m)", 1.0, 25.0,
        value=st.session_state.geo_params.outer_radius, step=0.5,
        help="Outer radius of the steel-lined tunnel cross-section, used both for IFC display geometry and to derive InternalDiameter property.",
    )
    lining_thickness = st.slider(
        "Lining thickness (m)", 0.05, 5.0,
        value=st.session_state.geo_params.lining_thickness, step=0.05,
        help="Combined concrete + steel liner thickness. Internal diameter = 2 × (outer_radius − lining_thickness).",
    )

    st.markdown("**Display geometry**")
    display_length = st.slider(
        "Display segment length (m)", 20.0, 400.0,
        value=st.session_state.geo_params.display_length, step=10.0,
        help="Length of each segment in the IFC display model. Real chainage stored separately in property sets.",
    )
    display_gap = st.slider(
        "Gap between segments (m)", 0.0, 100.0,
        value=st.session_state.geo_params.display_gap, step=5.0,
        help="Visible gap so each segment is identifiable in the model viewer.",
    )

    st.markdown("**FACS joint markers**")
    joint_radius = st.slider(
        "Joint marker radius (m)", 1.0, 30.0,
        value=st.session_state.geo_params.joint_radius, step=0.5,
    )
    joint_height = st.slider(
        "Joint marker height (m)", 0.5, 20.0,
        value=st.session_state.geo_params.joint_height, step=0.5,
    )

    st.markdown("**Operational baseline**")
    operating_pressure_mean = st.number_input(
        "Operating pressure at start (MPa)",
        min_value=0.5, max_value=20.0,
        value=st.session_state.geo_params.operating_pressure_mean / 1e6,
        step=0.1,
    ) * 1e6
    head_loss_per_segment = st.number_input(
        "Head loss per segment (m)",
        min_value=0.1, max_value=50.0,
        value=st.session_state.geo_params.head_loss_per_segment,
        step=0.1,
        help="Used by R2 (Bernoulli consistency) — operating pressure declines along the alignment by this amount per segment.",
    )

    st.markdown("---")
    inject_errors = st.checkbox("Inject realistic engineering errors",
                                 value=False,
                                 help="When on, segment 1 fails R2/R3/R5/R6, segment 2 fails R1, segment 3 fails R4/R7, joint 1 fails R8.")


# ----- Main area ------------------------------------------------------------

st.markdown("## Parametric IFC4X3 geometric model generator")
st.caption(
    "Adjust dimensions in the sidebar and per-segment values below. "
    "Click **Generate and validate** to build the IFC and run the 8-rule check."
)


# ----- Per-segment editor ---------------------------------------------------

st.markdown("### Per-segment values")

current_segments = st.session_state.geo_params.segments
n_segments = st.number_input(
    "Number of segments",
    min_value=2, max_value=8,
    value=len(current_segments), step=1,
)

# Resize segment list if needed
if n_segments != len(current_segments):
    if n_segments > len(current_segments):
        last = current_segments[-1] if current_segments else SegmentSpec("seg_X", 0.0, 500.0)
        seg_length = last.chainage_end - last.chainage_start
        for i in range(len(current_segments), n_segments):
            new_start = (current_segments[-1].chainage_end
                          if current_segments else 1000.0)
            current_segments.append(SegmentSpec(
                label=f"seg_{int(new_start + seg_length / 2)}",
                chainage_start=new_start,
                chainage_end=new_start + seg_length,
            ))
    else:
        current_segments = current_segments[:n_segments]

new_segments: list[SegmentSpec] = []
tabs = st.tabs([f"Segment {i+1}: {s.label}"
                for i, s in enumerate(current_segments)])

for i, (tab, spec) in enumerate(zip(tabs, current_segments)):
    with tab:
        cols = st.columns(3)
        with cols[0]:
            label = st.text_input(f"Label##{i}", value=spec.label, key=f"label_{i}")
            chainage_start = st.number_input(
                f"Chainage start (m)##{i}",
                value=float(spec.chainage_start), step=10.0, key=f"cs_{i}")
            chainage_end = st.number_input(
                f"Chainage end (m)##{i}",
                value=float(spec.chainage_end), step=10.0, key=f"ce_{i}")
            stage = st.selectbox(
                f"Lifecycle stage##{i}",
                ["DESIGN", "CONSTRUCTION", "OPERATION", "MAINTENANCE"],
                index=["DESIGN", "CONSTRUCTION", "OPERATION", "MAINTENANCE"]
                    .index(spec.lifecycle_stage)
                    if spec.lifecycle_stage in
                       ["DESIGN", "CONSTRUCTION", "OPERATION", "MAINTENANCE"]
                    else 2,
                key=f"stage_{i}")
        with cols[1]:
            gsi = st.slider(f"Rock GSI##{i}", 30.0, 100.0,
                             value=float(spec.GSI), step=1.0, key=f"gsi_{i}")
            discharge_gen = st.number_input(
                f"Design discharge generating (m³/s)##{i}",
                value=float(spec.discharge_generating), step=10.0,
                key=f"dg_{i}")
            discharge_pump = st.number_input(
                f"Design discharge pumping (m³/s)##{i}",
                value=float(spec.discharge_pumping), step=10.0,
                key=f"dp_{i}")
            crack_count = st.number_input(
                f"Crack count##{i}",
                min_value=0, max_value=200,
                value=int(spec.crack_count), step=1, key=f"cc_{i}")
            crack_aperture = st.number_input(
                f"Mean crack aperture (mm)##{i}",
                value=float(spec.crack_aperture_mm),
                step=0.01, format="%.2f", key=f"ca_{i}")
        with cols[2]:
            miner_damage = st.slider(
                f"Miner damage ratio##{i}", 0.0, 1.5,
                value=float(spec.miner_damage_ratio), step=0.01,
                key=f"md_{i}")
            miner_action = st.slider(
                f"Threshold action##{i}", 0.1, 1.5,
                value=float(spec.miner_threshold_action), step=0.05,
                key=f"ta_{i}")
            miner_critical = st.slider(
                f"Threshold critical##{i}", 0.1, 2.0,
                value=float(spec.miner_threshold_critical), step=0.05,
                key=f"tc_{i}")
            ls_concrete = st.slider(
                f"Load share concrete##{i}", 0.0, 1.0,
                value=float(spec.load_share_concrete), step=0.01,
                key=f"lsc_{i}")
            ls_steel = st.slider(
                f"Load share steel##{i}", 0.0, 1.0,
                value=float(spec.load_share_steel), step=0.01,
                key=f"lss_{i}")
            ls_rock = st.slider(
                f"Load share rock##{i}", 0.0, 1.0,
                value=float(spec.load_share_rock), step=0.01,
                key=f"lsr_{i}")

        new_segments.append(SegmentSpec(
            label=label,
            chainage_start=chainage_start,
            chainage_end=chainage_end,
            GSI=gsi,
            discharge_generating=discharge_gen,
            discharge_pumping=discharge_pump,
            miner_damage_ratio=miner_damage,
            miner_threshold_action=miner_action,
            miner_threshold_critical=miner_critical,
            crack_count=crack_count,
            crack_aperture_mm=crack_aperture,
            load_share_concrete=ls_concrete,
            load_share_steel=ls_steel,
            load_share_rock=ls_rock,
            lifecycle_stage=stage,
        ))


# Build the live params object
live_params = GeometryParams(
    outer_radius=outer_radius,
    lining_thickness=lining_thickness,
    display_length=display_length,
    display_gap=display_gap,
    joint_radius=joint_radius,
    joint_height=joint_height,
    operating_pressure_mean=operating_pressure_mean,
    head_loss_per_segment=head_loss_per_segment,
    segments=new_segments,
)


# ----- Pre-build sanity feedback -------------------------------------------

st.markdown("### Pre-build sanity check")

problems = live_params.validate()
if problems:
    for p in problems:
        st.error(p)
else:
    st.success(
        f"Parameters look consistent. Ready to build "
        f"{len(new_segments)} segments and {len(new_segments)-1} joints. "
        f"Total real chainage {new_segments[-1].chainage_end - new_segments[0].chainage_start:.0f} m, "
        f"display extent {(len(new_segments)-1)*(display_length+display_gap)+display_length:.0f} m."
    )


# ----- Live 2D plan preview -------------------------------------------------

st.markdown("### Live geometry preview (top-down plan)")

def render_preview_svg(params: GeometryParams, inject: bool) -> str:
    """Render a top-down 2D plan view of the planned IFC geometry."""
    n = len(params.segments)
    total_len = (n - 1) * (params.display_length + params.display_gap) + params.display_length
    margin = 40
    width = 880
    height = 220
    scale = (width - 2 * margin) / max(total_len, 1.0)
    seg_height = min(params.outer_radius * 2 * scale * 0.8, 80)
    cy = height / 2

    parts = [
        f'<svg width="100%" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" style="background:#FAF9F5;border-radius:6px">'
    ]

    # Centreline
    parts.append(
        f'<line x1="{margin-10}" y1="{cy}" x2="{width-margin+10}" y2="{cy}" '
        f'stroke="#888780" stroke-width="0.5" stroke-dasharray="4 4"/>'
    )

    for i, spec in enumerate(params.segments):
        x0 = margin + i * (params.display_length + params.display_gap) * scale
        x1 = x0 + params.display_length * scale
        if not inject:
            color = "#97C459"
            stroke = "#27500A"
        elif i == 0:
            color = "#E24B4A"
            stroke = "#791F1F"
        elif i == 1:
            color = "#EF9F27"
            stroke = "#854F0B"
        else:
            color = "#E24B4A"
            stroke = "#791F1F"

        parts.append(
            f'<rect x="{x0}" y="{cy - seg_height/2}" '
            f'width="{x1 - x0}" height="{seg_height}" '
            f'fill="{color}" stroke="{stroke}" stroke-width="1" rx="4"/>'
        )
        # Inner diameter line
        inner_h = max(seg_height - params.lining_thickness * scale * 2, 4)
        parts.append(
            f'<rect x="{x0+4}" y="{cy - inner_h/2}" '
            f'width="{x1 - x0 - 8}" height="{inner_h}" '
            f'fill="white" stroke="#888780" stroke-width="0.5" rx="2"/>'
        )
        # Label
        parts.append(
            f'<text x="{(x0+x1)/2}" y="{cy + seg_height/2 + 18}" '
            f'text-anchor="middle" style="font-family:sans-serif;font-size:11px;fill:#444441">'
            f'{spec.label}</text>'
        )
        parts.append(
            f'<text x="{(x0+x1)/2}" y="{cy + seg_height/2 + 32}" '
            f'text-anchor="middle" style="font-family:sans-serif;font-size:10px;fill:#888780">'
            f'Sta {spec.chainage_start:.0f}–{spec.chainage_end:.0f}</text>'
        )

    # Joint markers
    for j in range(n - 1):
        jx = (margin + (j + 1) * params.display_length * scale
              + (j + 0.5) * params.display_gap * scale)
        jr = max(params.joint_radius * scale * 0.4, 4)
        parts.append(
            f'<circle cx="{jx}" cy="{cy}" r="{jr}" '
            f'fill="#185FA5" stroke="#0C447C" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{jx}" y="{cy - seg_height/2 - 8}" '
            f'text-anchor="middle" style="font-family:sans-serif;font-size:9px;fill:#0C447C">'
            f'joint {j+1}</text>'
        )

    # Scale bar
    bar_len_m = 100.0
    bar_x0 = margin
    bar_x1 = bar_x0 + bar_len_m * scale
    parts.append(
        f'<line x1="{bar_x0}" y1="{height-15}" x2="{bar_x1}" y2="{height-15}" '
        f'stroke="#444441" stroke-width="2"/>'
    )
    parts.append(
        f'<text x="{(bar_x0+bar_x1)/2}" y="{height-3}" text-anchor="middle" '
        f'style="font-family:sans-serif;font-size:10px;fill:#444441">100 m</text>'
    )

    # Title
    title = "FAULTY MODEL — colours match validator severity" if inject else "CLEAN MODEL — all green"
    parts.append(
        f'<text x="{width/2}" y="20" text-anchor="middle" '
        f'style="font-family:sans-serif;font-size:12px;fill:#444441;font-weight:600">'
        f'{title}</text>'
    )

    parts.append("</svg>")
    return "".join(parts)

st.markdown(render_preview_svg(live_params, inject_errors), unsafe_allow_html=True)


# ----- Generate and validate ------------------------------------------------

st.markdown("### Generate and validate")

cols = st.columns([1, 1, 3])
with cols[0]:
    do_generate = st.button("Generate and validate",
                             type="primary",
                             disabled=bool(problems),
                             use_container_width=True)

if do_generate:
    with st.spinner("Building IFC and running 8-rule validator..."):
        tmp_path = tempfile.NamedTemporaryFile(suffix=".ifc", delete=False).name
        summary = build_parametric_geometric_ifc(
            tmp_path, live_params, inject_errors=inject_errors)
        report = IPSValidator(tmp_path).run_all()
        with open(tmp_path, "rb") as fh:
            ifc_bytes = fh.read()
        st.session_state.last_build = summary
        st.session_state.last_validation = report
        st.session_state.last_ifc_bytes = ifc_bytes


# ----- Show results ---------------------------------------------------------

if st.session_state.last_validation is not None:
    summary = st.session_state.last_build
    report = st.session_state.last_validation

    s = report.summary()
    if s["error"] > 0:
        verdict = "REJECTED"
        color = "error"
    elif s["warn"] > 0:
        verdict = "CONDITIONAL"
        color = "warn"
    else:
        verdict = "ACCEPTED"
        color = "pass"

    bg = SEVERITY_BG[color]
    fg = SEVERITY_COLORS[color]
    st.markdown(
        f'''<div style="background:{bg};border-left:5px solid {fg};
                        padding:14px 18px;border-radius:6px;margin:16px 0">
              <div style="font-size:18px;font-weight:600;color:{fg}">{verdict}</div>
              <div style="font-size:13px;color:#444;margin-top:4px">
                {s['pass']} pass · {s['warn']} warning · {s['error']} error
                across the 8-rule schema.
              </div>
            </div>''',
        unsafe_allow_html=True,
    )

    cols = st.columns(6)
    cols[0].metric("Schema", summary["schema"])
    cols[1].metric("Segments", summary["segments"])
    cols[2].metric("Joints", summary["joints"])
    cols[3].metric("File size", f"{summary['file_size_bytes']/1024:.1f} KB")
    cols[4].metric("Real chainage", f"{summary['total_real_chainage_m']:.0f} m")
    cols[5].metric("Display extent", f"{summary['total_display_extent_m']:.0f} m")

    st.markdown("#### Rule outcomes")
    for r in report.results:
        sev = r.severity.value
        c = SEVERITY_COLORS[sev]
        b = SEVERITY_BG[sev]
        guid = f' <span style="font-family:monospace;font-size:11px;color:#888">{r.segment_guid[:14]}…</span>' if r.segment_guid else ""
        st.markdown(
            f'''<div style="background:{b};padding:8px 12px;
                            border-left:3px solid {c};border-radius:4px;
                            margin-bottom:6px">
                  <span style="font-family:monospace;font-size:11px;color:{c};
                               font-weight:600">{r.rule_id}</span>
                  <span style="font-weight:600;font-size:13px">
                    {r.rule_name}</span>{guid}
                  <div style="font-size:12px;color:#555;margin-top:2px">{r.message}</div>
                </div>''',
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown("#### Downloads")
    download_cols = st.columns(2)

    label = "faulty" if inject_errors else "clean"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    with download_cols[0]:
        st.download_button(
            "⬇ Download IFC4X3 file",
            data=st.session_state.last_ifc_bytes,
            file_name=f"snowy2_param_{label}_{ts}.ifc",
            mime="application/x-step",
            use_container_width=True,
        )
    with download_cols[1]:
        json_payload = json.dumps(report.to_dict(), indent=2, default=str)
        st.download_button(
            "⬇ Download JSON audit trail",
            data=json_payload,
            file_name=f"validation_report_{label}_{ts}.json",
            mime="application/json",
            use_container_width=True,
        )

    st.caption(
        "Open the .ifc file in BlenderBIM, Solibri, or BIMcollab Zoom to see "
        "the segment geometry, lining, joint markers and seven Pset_*_IPS "
        "property sets attached to each element."
    )

else:
    st.info(
        "Adjust dimensions in the sidebar and segment values above, "
        "then click **Generate and validate** to build the IFC."
    )
