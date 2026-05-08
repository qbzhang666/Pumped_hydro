import streamlit as st
import pandas as pd
from pathlib import Path
import tempfile
import json

from ips_validator import IPSValidator, IPSDictValidator


st.set_page_config(
    page_title="Snowy 2.0 IPS IFC Validator",
    layout="wide"
)

st.title("Snowy 2.0 IPS — IFC Validator Dashboard")

st.write(
    "Upload an IFC file and run the IPS validation rules R1–R8."
)

strict = st.toggle("Strict mode: treat warnings as rejection", value=True)

uploaded_file = st.file_uploader(
    "Upload IFC file",
    type=["ifc"]
)

if uploaded_file is not None:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ifc") as tmp:
        tmp.write(uploaded_file.read())
        ifc_path = tmp.name

    st.success(f"File uploaded: {uploaded_file.name}")

    if st.button("Run validation"):
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

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Verdict", verdict)
        col2.metric("Pass", summary["pass"])
        col3.metric("Warnings", summary["warn"])
        col4.metric("Errors", summary["error"])

        rows = []
        for r in report.results:
            rows.append({
                "Rule": r.rule_id,
                "Severity": r.severity.value.upper(),
                "Check": r.rule_name,
                "Segment / Joint": r.segment_guid or "—",
                "Message": r.message
            })

        df = pd.DataFrame(rows)

        st.subheader("Rule-by-rule Results")
        st.dataframe(df, use_container_width=True)

        st.subheader("Action Items")
        action_items = df[df["Severity"].isin(["WARN", "ERROR"])]
        st.dataframe(action_items, use_container_width=True)

        st.subheader("Worst Severity per Rule")

        severity_order = {
            "PASS": 0,
            "INFO": 1,
            "WARN": 2,
            "ERROR": 3
        }

        worst = (
            df.sort_values(
                "Severity",
                key=lambda x: x.map(severity_order)
            )
            .groupby("Rule")
            .tail(1)
        )

        st.bar_chart(
            worst.set_index("Rule")["Severity"].map(severity_order)
        )

        report_json = json.dumps(report.to_dict(), indent=2, default=str)

        st.download_button(
            label="Download JSON validation report",
            data=report_json,
            file_name="ips_validation_report.json",
            mime="application/json"
        )
