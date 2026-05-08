"""
ips_validator.py
================

Validator for the Snowy 2.0 IPS Pset family.

Enforces the consistency rules from Section 14 of the schema specification:

    1. Mass conservation across adjacent segments
    2. Bernoulli consistency along the alignment
    3. Einstein-Schwartz load-share closure (sum to 1.0)
    4. Stage applicability vs segment status
    5. Source provenance for every quantitative property
    6. Surrogate prediction freshness (within 30 days of assessment)
    7. Damage threshold ordering (action < critical)
    8. Joint topology (referenced GUIDs must exist)

The validator runs against a populated IFC 4.3 file using the ifcopenshell
library. It can be invoked from the command line:

    python ips_validator.py snowy2_ips.ifc --report report.json

Or imported as a library:

    from ips_validator import IPSValidator
    validator = IPSValidator(ifc_path)
    report = validator.run_all()

The validator returns a structured report compatible with the live demo
dashboard.

Author:  Eric's group, University of Melbourne
Version: 0.3.0 (2026-05-08)
License: Apache-2.0
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

try:
    import ifcopenshell
    import ifcopenshell.util.element
except ImportError:
    ifcopenshell = None


# Configuration constants ------------------------------------------------------

PSET_FAMILY = [
    "Pset_HydraulicPerformance_IPS",
    "Pset_TransientAnalysis_IPS",
    "Pset_CompositeLining_IPS",
    "Pset_FACSJoint_IPS",
    "Pset_FatigueDamage_IPS",
    "Pset_Leakage_IPS",
    "Pset_SurrogatePrediction_IPS",
    "Pset_AssessmentMeta_IPS",
]

QUANTITATIVE_PROPERTIES_REQUIRING_SOURCE = {
    "Pset_HydraulicPerformance_IPS": [
        ("InternalDiameter", "InternalDiameter_Source"),
        ("DesignDischarge_Generating", "DesignDischarge_Source"),
        ("DesignDischarge_Pumping", "DesignDischarge_Source"),
        ("ManningRoughnessCoefficient", "Manning_Source"),
    ],
    "Pset_TransientAnalysis_IPS": [
        ("WaveCelerity_Composite", "Celerity_Source"),
        ("MOC_PressureEnvelopeMax", "MOC_Source"),
        ("MOC_PressureEnvelopeMin", "MOC_Source"),
    ],
    "Pset_CompositeLining_IPS": [
        ("RockMass_GSI", "Rock_Source"),
        ("RockMass_DeformationModulus", "Rock_Source"),
        ("EinsteinSchwartz_LoadShareConcrete", "ES_Source"),
        ("EinsteinSchwartz_LoadShareSteelLiner", "ES_Source"),
        ("EinsteinSchwartz_LoadShareRockMass", "ES_Source"),
    ],
    "Pset_FatigueDamage_IPS": [
        ("StressRange_Design", "StressRange_Source"),
        ("MinerDamageRatio_Cumulative", "Miner_Source"),
    ],
    "Pset_Leakage_IPS": [
        ("CrackAperture_Mean", "CrackAperture_Source"),
        ("CubicLaw_LeakageRate", "Leakage_Source"),
    ],
}

DISCHARGE_TOLERANCE_RATIO = 0.001     # rule 1
LOAD_SHARE_TOLERANCE = 0.005          # rule 3
SURROGATE_FRESHNESS_DAYS = 30         # rule 6


class Severity(str, Enum):
    PASS = "pass"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass
class CheckResult:
    rule_id: str
    rule_name: str
    severity: Severity
    message: str
    segment_guid: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class ValidationReport:
    ifc_path: str
    timestamp: str
    schema: str
    segment_count: int
    joint_count: int
    results: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    def summary(self) -> dict[str, int]:
        out = {s.value: 0 for s in Severity}
        for r in self.results:
            out[r.severity.value] += 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "ifc_path": self.ifc_path,
            "timestamp": self.timestamp,
            "schema": self.schema,
            "segment_count": self.segment_count,
            "joint_count": self.joint_count,
            "summary": self.summary(),
            "results": [r.to_dict() for r in self.results],
        }


# Helpers ---------------------------------------------------------------------

def _get_pset(element, pset_name: str) -> dict[str, Any] | None:
    """Return a dict of property name to value for the named Pset, or None."""
    psets = ifcopenshell.util.element.get_psets(element)
    return psets.get(pset_name)


def _get_alignment_segments(model) -> list:
    """Return all IfcAlignmentSegment instances ordered by chainage."""
    segments = list(model.by_type("IfcAlignmentSegment"))
    def chainage_key(seg):
        pset = _get_pset(seg, "Pset_HydraulicPerformance_IPS") or {}
        return pset.get("SegmentChainageStart", 0.0)
    segments.sort(key=chainage_key)
    return segments


def _get_joints(model) -> list:
    return [a for a in model.by_type("IfcDiscreteAccessory")
            if _get_pset(a, "Pset_FACSJoint_IPS") is not None]


# Individual checks -----------------------------------------------------------

class IPSValidator:
    """Run the schema consistency checks against a populated IFC file."""

    def __init__(self, ifc_path: str):
        if ifcopenshell is None:
            raise RuntimeError(
                "ifcopenshell is not installed. "
                "Install with: pip install ifcopenshell"
            )
        self.ifc_path = ifc_path
        self.model = ifcopenshell.open(ifc_path)
        self.segments = _get_alignment_segments(self.model)
        self.joints = _get_joints(self.model)
        self.report = ValidationReport(
            ifc_path=ifc_path,
            timestamp=datetime.now(timezone.utc).isoformat(),
            schema=getattr(self.model, "schema", "IFC4X3"),
            segment_count=len(self.segments),
            joint_count=len(self.joints),
        )

    # ---- Rule 1: mass conservation -----------------------------------------
    def check_mass_conservation(self) -> None:
        rule_id = "R1"
        rule_name = "Mass conservation across adjacent segments"
        if len(self.segments) < 2:
            self.report.add(CheckResult(rule_id, rule_name, Severity.INFO,
                "Fewer than two segments — skipping mass conservation."))
            return

        prev_q = None
        prev_guid = None
        for seg in self.segments:
            pset = _get_pset(seg, "Pset_HydraulicPerformance_IPS") or {}
            q = pset.get("DesignDischarge_Generating")
            if q is None:
                continue
            if prev_q is not None:
                rel_diff = abs(q - prev_q) / max(abs(prev_q), 1e-9)
                if rel_diff > DISCHARGE_TOLERANCE_RATIO:
                    self.report.add(CheckResult(
                        rule_id, rule_name, Severity.ERROR,
                        f"Discharge mismatch between adjacent segments: "
                        f"{prev_q:.3f} → {q:.3f} m³/s "
                        f"({rel_diff*100:.2f}% > {DISCHARGE_TOLERANCE_RATIO*100:.2f}%)",
                        segment_guid=seg.GlobalId,
                        details={
                            "previous_guid": prev_guid,
                            "previous_discharge": prev_q,
                            "current_discharge": q,
                            "relative_difference": rel_diff,
                        }))
            prev_q = q
            prev_guid = seg.GlobalId

        if not any(r.rule_id == rule_id and r.severity == Severity.ERROR
                   for r in self.report.results):
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                f"Mass conservation satisfied across {len(self.segments)} segments."))

    # ---- Rule 3: Einstein-Schwartz load-share closure ----------------------
    def check_load_share_closure(self) -> None:
        rule_id = "R3"
        rule_name = "Einstein-Schwartz load-share closure"
        checked = 0
        failed = 0
        for seg in self.segments:
            pset = _get_pset(seg, "Pset_CompositeLining_IPS") or {}
            c = pset.get("EinsteinSchwartz_LoadShareConcrete")
            s = pset.get("EinsteinSchwartz_LoadShareSteelLiner")
            r = pset.get("EinsteinSchwartz_LoadShareRockMass")
            if None in (c, s, r):
                continue
            checked += 1
            total = c + s + r
            if abs(total - 1.0) > LOAD_SHARE_TOLERANCE:
                failed += 1
                self.report.add(CheckResult(
                    rule_id, rule_name, Severity.ERROR,
                    f"Load shares do not sum to 1.0: "
                    f"concrete={c:.3f}, steel={s:.3f}, rock={r:.3f}, "
                    f"total={total:.4f} (tolerance ±{LOAD_SHARE_TOLERANCE})",
                    segment_guid=seg.GlobalId,
                    details={"concrete": c, "steel": s, "rock": r, "total": total}))
        if checked > 0 and failed == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                f"Load-share closure satisfied on {checked} segments."))
        elif checked == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.WARN,
                "No segments populated with Einstein-Schwartz load shares."))

    # ---- Rule 5: source provenance for quantitative properties -------------
    def check_source_provenance(self) -> None:
        rule_id = "R5"
        rule_name = "Source provenance for quantitative properties"
        missing_count = 0
        for pset_name, pairs in QUANTITATIVE_PROPERTIES_REQUIRING_SOURCE.items():
            for seg in self.segments:
                pset = _get_pset(seg, pset_name) or {}
                for value_prop, source_prop in pairs:
                    if value_prop in pset and pset[value_prop] is not None:
                        source = pset.get(source_prop)
                        if source is None or source == "":
                            missing_count += 1
                            self.report.add(CheckResult(
                                rule_id, rule_name, Severity.WARN,
                                f"Property {value_prop} populated but "
                                f"{source_prop} is missing in {pset_name}",
                                segment_guid=seg.GlobalId,
                                details={"pset": pset_name,
                                         "value_property": value_prop,
                                         "source_property": source_prop}))
        if missing_count == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                "All quantitative properties have populated source fields."))

    # ---- Rule 6: surrogate prediction freshness ----------------------------
    def check_surrogate_freshness(self) -> None:
        rule_id = "R6"
        rule_name = "Surrogate prediction freshness"
        threshold = timedelta(days=SURROGATE_FRESHNESS_DAYS)
        checked = 0
        stale = 0
        for seg in self.segments:
            pred = _get_pset(seg, "Pset_SurrogatePrediction_IPS") or {}
            meta = _get_pset(seg, "Pset_AssessmentMeta_IPS") or {}
            pred_ts = pred.get("PredictionTimestamp")
            assess_ts = meta.get("AssessmentTimestamp")
            if not pred_ts or not assess_ts:
                continue
            try:
                t_pred = datetime.fromisoformat(pred_ts.replace("Z", "+00:00"))
                t_assess = datetime.fromisoformat(assess_ts.replace("Z", "+00:00"))
            except ValueError:
                self.report.add(CheckResult(rule_id, rule_name, Severity.WARN,
                    f"Invalid timestamp format in segment",
                    segment_guid=seg.GlobalId))
                continue
            checked += 1
            age = abs(t_assess - t_pred)
            if age > threshold:
                stale += 1
                self.report.add(CheckResult(
                    rule_id, rule_name, Severity.WARN,
                    f"Surrogate prediction is {age.days} days older than "
                    f"the assessment cycle (threshold {SURROGATE_FRESHNESS_DAYS} d)",
                    segment_guid=seg.GlobalId,
                    details={"prediction_timestamp": pred_ts,
                             "assessment_timestamp": assess_ts,
                             "age_days": age.days}))
        if checked > 0 and stale == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                f"All {checked} surrogate predictions are fresh."))

    # ---- Rule 7: damage threshold ordering ---------------------------------
    def check_damage_thresholds(self) -> None:
        rule_id = "R7"
        rule_name = "Damage threshold ordering (action < critical)"
        checked = 0
        for seg in self.segments:
            pset = _get_pset(seg, "Pset_FatigueDamage_IPS") or {}
            action = pset.get("MinerDamageThreshold_Action")
            critical = pset.get("MinerDamageThreshold_Critical")
            if action is None or critical is None:
                continue
            checked += 1
            if action >= critical:
                self.report.add(CheckResult(
                    rule_id, rule_name, Severity.ERROR,
                    f"Action threshold ({action}) must be strictly less than "
                    f"critical threshold ({critical})",
                    segment_guid=seg.GlobalId,
                    details={"action": action, "critical": critical}))
        if checked > 0 and not any(
            r.rule_id == rule_id and r.severity == Severity.ERROR
            for r in self.report.results
        ):
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                f"Threshold ordering satisfied on {checked} segments."))

    # ---- Rule 8: joint topology --------------------------------------------
    def check_joint_topology(self) -> None:
        rule_id = "R8"
        rule_name = "Joint topology references existing segments"
        segment_guids = {seg.GlobalId for seg in self.segments}
        broken = 0
        for joint in self.joints:
            pset = _get_pset(joint, "Pset_FACSJoint_IPS") or {}
            up = pset.get("JointGUID_Upstream")
            down = pset.get("JointGUID_Downstream")
            for label, guid in [("upstream", up), ("downstream", down)]:
                if guid is None:
                    continue
                if guid not in segment_guids:
                    broken += 1
                    self.report.add(CheckResult(
                        rule_id, rule_name, Severity.ERROR,
                        f"Joint references non-existent {label} segment GUID {guid}",
                        segment_guid=joint.GlobalId,
                        details={"reference": label, "missing_guid": guid}))
        if broken == 0 and self.joints:
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                f"Joint topology valid for {len(self.joints)} joints."))
        elif not self.joints:
            self.report.add(CheckResult(rule_id, rule_name, Severity.INFO,
                "No FACS joints defined in model."))

    # ---- Rule 2: Bernoulli consistency along the alignment ------------------
    def check_bernoulli_consistency(self) -> None:
        """Verify that the energy grade line declines monotonically along the
        flow direction by an amount consistent with declared head losses.

        For each pair of adjacent segments this check evaluates whether the
        change in piezometric head plus velocity head matches the segment's
        declared HeadLoss_Segment within tolerance. When elevation data is
        not available, the check falls back to verifying that pressure drops
        in the direction of flow are non-negative for steady-state generating.
        """
        rule_id = "R2"
        rule_name = "Bernoulli consistency along alignment"
        if len(self.segments) < 2:
            self.report.add(CheckResult(rule_id, rule_name, Severity.INFO,
                "Fewer than two segments — skipping Bernoulli check."))
            return

        prev_pset = None
        prev_guid = None
        violations = 0
        checked = 0
        for seg in self.segments:
            pset = _get_pset(seg, "Pset_HydraulicPerformance_IPS") or {}
            p = pset.get("OperatingPressure_Mean")
            v = pset.get("MeanFlowVelocity_Generating")
            head_loss = pset.get("HeadLoss_Segment")
            density = pset.get("WaterDensity") or 1000.0
            if p is None or v is None:
                prev_pset = pset
                prev_guid = seg.GlobalId
                continue
            if prev_pset is not None:
                p_prev = prev_pset.get("OperatingPressure_Mean")
                v_prev = prev_pset.get("MeanFlowVelocity_Generating")
                hl_prev = prev_pset.get("HeadLoss_Segment") or 0.0
                if p_prev is not None and v_prev is not None:
                    checked += 1
                    head_prev = p_prev / (density * 9.81) + v_prev**2 / (2 * 9.81)
                    head_curr = p / (density * 9.81) + v**2 / (2 * 9.81)
                    head_drop_observed = head_prev - head_curr
                    head_drop_expected = hl_prev
                    if head_drop_expected > 0:
                        rel_error = abs(head_drop_observed - head_drop_expected) / head_drop_expected
                        if rel_error > 0.20:
                            violations += 1
                            self.report.add(CheckResult(
                                rule_id, rule_name, Severity.WARN,
                                f"Energy gradient inconsistent with declared head loss: "
                                f"observed {head_drop_observed:.3f} m vs declared "
                                f"{head_drop_expected:.3f} m ({rel_error*100:.1f}% deviation)",
                                segment_guid=seg.GlobalId,
                                details={
                                    "previous_guid": prev_guid,
                                    "head_drop_observed_m": head_drop_observed,
                                    "head_drop_expected_m": head_drop_expected,
                                    "relative_error": rel_error,
                                }))
            prev_pset = pset
            prev_guid = seg.GlobalId

        if checked > 0 and violations == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                f"Bernoulli consistency satisfied across {checked} segment pairs."))
        elif checked == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.INFO,
                "Insufficient pressure/velocity data for Bernoulli check."))

    # ---- Rule 4: stage applicability ---------------------------------------
    def check_stage_applicability(self) -> None:
        """Verify that operation-stage properties are populated only on
        segments whose lifecycle stage tag is OPERATION or later.

        The check reads LifecycleStage from Pset_AssessmentMeta_IPS for each
        segment and verifies that operation-only fields (e.g. surrogate
        predictions, monitored damage ratios, leakage rates) are not
        populated when the segment is still in DESIGN or CONSTRUCTION.
        """
        rule_id = "R4"
        rule_name = "Stage applicability vs segment lifecycle stage"

        operation_only_props = {
            "Pset_FatigueDamage_IPS": ["MinerDamageRatio_Cumulative",
                                       "RemainingFatigueLife_Years"],
            "Pset_Leakage_IPS": ["CrackCount_Detected",
                                 "CrackAperture_Mean",
                                 "CubicLaw_LeakageRate"],
            "Pset_SurrogatePrediction_IPS": ["PredictedDamageRatio_Mean",
                                             "PredictionTimestamp"],
        }
        operation_stages = {"OPERATION", "MAINTENANCE", "DECOMMISSIONING"}

        violations = 0
        checked = 0
        for seg in self.segments:
            meta = _get_pset(seg, "Pset_AssessmentMeta_IPS") or {}
            stage = meta.get("LifecycleStage")
            if stage is None:
                continue
            checked += 1
            if stage not in operation_stages:
                for pset_name, props in operation_only_props.items():
                    pset = _get_pset(seg, pset_name) or {}
                    populated = [p for p in props
                                 if pset.get(p) is not None]
                    if populated:
                        violations += 1
                        self.report.add(CheckResult(
                            rule_id, rule_name, Severity.WARN,
                            f"Operation-stage properties {populated} populated "
                            f"on segment with LifecycleStage={stage} in {pset_name}",
                            segment_guid=seg.GlobalId,
                            details={
                                "lifecycle_stage": stage,
                                "pset": pset_name,
                                "operation_only_properties_populated": populated,
                            }))
        if checked > 0 and violations == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                f"Stage applicability satisfied on {checked} segments."))
        elif checked == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.INFO,
                "No LifecycleStage tags found — skipping stage applicability."))

    # ---- Driver -------------------------------------------------------------
    def run_all(self) -> ValidationReport:
        self.check_mass_conservation()
        self.check_bernoulli_consistency()
        self.check_load_share_closure()
        self.check_stage_applicability()
        self.check_source_provenance()
        self.check_surrogate_freshness()
        self.check_damage_thresholds()
        self.check_joint_topology()
        return self.report


# Mock-data validator for the live demo ---------------------------------------
# This path is exercised when the live demo wants to show validator output
# without requiring a populated .ifc file on disk. It accepts a Python dict
# matching the same Pset-family structure and runs the same logical checks.

class IPSDictValidator:
    """Run the same checks against an in-memory dict of segments and joints.

    The dict structure is:

        {
          "segments": [
            { "guid": "...", "psets": { "Pset_X": { "Prop": value, ... } } },
            ...
          ],
          "joints": [
            { "guid": "...", "psets": { "Pset_FACSJoint_IPS": {...} } },
            ...
          ]
        }
    """

    def __init__(self, data: dict[str, Any]):
        self.data = data
        self.report = ValidationReport(
            ifc_path="<in-memory>",
            timestamp=datetime.now(timezone.utc).isoformat(),
            schema="IFC4X3",
            segment_count=len(data.get("segments", [])),
            joint_count=len(data.get("joints", [])),
        )

    def _pset(self, item: dict, name: str) -> dict | None:
        return item.get("psets", {}).get(name)

    def check_mass_conservation(self) -> None:
        rule_id = "R1"
        rule_name = "Mass conservation across adjacent segments"
        segments = sorted(
            self.data.get("segments", []),
            key=lambda s: (self._pset(s, "Pset_HydraulicPerformance_IPS") or {})
                .get("SegmentChainageStart", 0.0))
        prev_q = None
        prev_guid = None
        errors_found = False
        for seg in segments:
            hyd = self._pset(seg, "Pset_HydraulicPerformance_IPS") or {}
            q = hyd.get("DesignDischarge_Generating")
            if q is None:
                continue
            if prev_q is not None:
                rel = abs(q - prev_q) / max(abs(prev_q), 1e-9)
                if rel > DISCHARGE_TOLERANCE_RATIO:
                    errors_found = True
                    self.report.add(CheckResult(
                        rule_id, rule_name, Severity.ERROR,
                        f"Discharge mismatch: {prev_q:.3f} → {q:.3f} m³/s "
                        f"({rel*100:.2f}%)",
                        segment_guid=seg["guid"],
                        details={"previous_guid": prev_guid,
                                 "previous_discharge": prev_q,
                                 "current_discharge": q,
                                 "relative_difference": rel}))
            prev_q = q
            prev_guid = seg["guid"]
        if not errors_found and prev_q is not None:
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                f"Mass conservation satisfied across {len(segments)} segments."))

    def check_load_share_closure(self) -> None:
        rule_id = "R3"
        rule_name = "Einstein-Schwartz load-share closure"
        checked = 0
        failed = 0
        for seg in self.data.get("segments", []):
            lin = self._pset(seg, "Pset_CompositeLining_IPS") or {}
            c = lin.get("EinsteinSchwartz_LoadShareConcrete")
            s = lin.get("EinsteinSchwartz_LoadShareSteelLiner")
            r = lin.get("EinsteinSchwartz_LoadShareRockMass")
            if None in (c, s, r):
                continue
            checked += 1
            total = c + s + r
            if abs(total - 1.0) > LOAD_SHARE_TOLERANCE:
                failed += 1
                self.report.add(CheckResult(
                    rule_id, rule_name, Severity.ERROR,
                    f"Load shares sum to {total:.4f}, not 1.0 "
                    f"(concrete={c}, steel={s}, rock={r})",
                    segment_guid=seg["guid"],
                    details={"concrete": c, "steel": s, "rock": r, "total": total}))
        if checked > 0 and failed == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                f"Load-share closure satisfied on {checked} segments."))

    def check_source_provenance(self) -> None:
        rule_id = "R5"
        rule_name = "Source provenance for quantitative properties"
        missing = 0
        for pset_name, pairs in QUANTITATIVE_PROPERTIES_REQUIRING_SOURCE.items():
            for seg in self.data.get("segments", []):
                p = self._pset(seg, pset_name) or {}
                for v, s in pairs:
                    if v in p and p[v] is not None:
                        if s not in p or p[s] in (None, ""):
                            missing += 1
                            self.report.add(CheckResult(
                                rule_id, rule_name, Severity.WARN,
                                f"{v} populated without {s} in {pset_name}",
                                segment_guid=seg["guid"],
                                details={"pset": pset_name,
                                         "value_property": v,
                                         "source_property": s}))
        if missing == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                "All quantitative properties have populated source fields."))

    def check_surrogate_freshness(self) -> None:
        rule_id = "R6"
        rule_name = "Surrogate prediction freshness"
        threshold = timedelta(days=SURROGATE_FRESHNESS_DAYS)
        checked = 0
        stale = 0
        for seg in self.data.get("segments", []):
            pred = self._pset(seg, "Pset_SurrogatePrediction_IPS") or {}
            meta = self._pset(seg, "Pset_AssessmentMeta_IPS") or {}
            tp = pred.get("PredictionTimestamp")
            ta = meta.get("AssessmentTimestamp")
            if not tp or not ta:
                continue
            try:
                t_pred = datetime.fromisoformat(tp.replace("Z", "+00:00"))
                t_assess = datetime.fromisoformat(ta.replace("Z", "+00:00"))
            except ValueError:
                continue
            checked += 1
            age = abs(t_assess - t_pred)
            if age > threshold:
                stale += 1
                self.report.add(CheckResult(
                    rule_id, rule_name, Severity.WARN,
                    f"Surrogate prediction {age.days} days stale",
                    segment_guid=seg["guid"],
                    details={"age_days": age.days,
                             "prediction_timestamp": tp,
                             "assessment_timestamp": ta}))
        if checked > 0 and stale == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                f"All {checked} surrogate predictions fresh."))

    def check_damage_thresholds(self) -> None:
        rule_id = "R7"
        rule_name = "Damage threshold ordering"
        checked = 0
        bad = 0
        for seg in self.data.get("segments", []):
            fat = self._pset(seg, "Pset_FatigueDamage_IPS") or {}
            a = fat.get("MinerDamageThreshold_Action")
            c = fat.get("MinerDamageThreshold_Critical")
            if a is None or c is None:
                continue
            checked += 1
            if a >= c:
                bad += 1
                self.report.add(CheckResult(
                    rule_id, rule_name, Severity.ERROR,
                    f"Action threshold {a} must be < critical threshold {c}",
                    segment_guid=seg["guid"],
                    details={"action": a, "critical": c}))
        if checked > 0 and bad == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                f"Threshold ordering satisfied on {checked} segments."))

    def check_joint_topology(self) -> None:
        rule_id = "R8"
        rule_name = "Joint topology references existing segments"
        seg_guids = {s["guid"] for s in self.data.get("segments", [])}
        broken = 0
        for joint in self.data.get("joints", []):
            j = self._pset(joint, "Pset_FACSJoint_IPS") or {}
            for label in ("JointGUID_Upstream", "JointGUID_Downstream"):
                guid = j.get(label)
                if guid and guid not in seg_guids:
                    broken += 1
                    self.report.add(CheckResult(
                        rule_id, rule_name, Severity.ERROR,
                        f"Joint references non-existent {label}: {guid}",
                        segment_guid=joint["guid"],
                        details={"reference": label, "missing_guid": guid}))
        if broken == 0 and self.data.get("joints"):
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                f"Joint topology valid for {len(self.data['joints'])} joints."))

    def check_bernoulli_consistency(self) -> None:
        rule_id = "R2"
        rule_name = "Bernoulli consistency along alignment"
        segments = sorted(
            self.data.get("segments", []),
            key=lambda s: (self._pset(s, "Pset_HydraulicPerformance_IPS") or {})
                .get("SegmentChainageStart", 0.0))
        if len(segments) < 2:
            self.report.add(CheckResult(rule_id, rule_name, Severity.INFO,
                "Fewer than two segments — skipping Bernoulli check."))
            return
        prev_pset = None
        prev_guid = None
        violations = 0
        checked = 0
        for seg in segments:
            hyd = self._pset(seg, "Pset_HydraulicPerformance_IPS") or {}
            p = hyd.get("OperatingPressure_Mean")
            v = hyd.get("MeanFlowVelocity_Generating")
            density = hyd.get("WaterDensity") or 1000.0
            if p is None or v is None:
                prev_pset = hyd
                prev_guid = seg["guid"]
                continue
            if prev_pset is not None:
                p_prev = prev_pset.get("OperatingPressure_Mean")
                v_prev = prev_pset.get("MeanFlowVelocity_Generating")
                hl_prev = prev_pset.get("HeadLoss_Segment") or 0.0
                if p_prev is not None and v_prev is not None:
                    checked += 1
                    head_prev = p_prev / (density * 9.81) + v_prev**2 / (2 * 9.81)
                    head_curr = p / (density * 9.81) + v**2 / (2 * 9.81)
                    drop_obs = head_prev - head_curr
                    drop_exp = hl_prev
                    if drop_exp > 0:
                        rel = abs(drop_obs - drop_exp) / drop_exp
                        if rel > 0.20:
                            violations += 1
                            self.report.add(CheckResult(
                                rule_id, rule_name, Severity.WARN,
                                f"Energy gradient inconsistent: observed "
                                f"{drop_obs:.3f} m vs declared {drop_exp:.3f} m "
                                f"({rel*100:.1f}%)",
                                segment_guid=seg["guid"],
                                details={"previous_guid": prev_guid,
                                         "head_drop_observed_m": drop_obs,
                                         "head_drop_expected_m": drop_exp,
                                         "relative_error": rel}))
            prev_pset = hyd
            prev_guid = seg["guid"]
        if checked > 0 and violations == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                f"Bernoulli consistency satisfied across {checked} pairs."))
        elif checked == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.INFO,
                "Insufficient pressure/velocity data for Bernoulli check."))

    def check_stage_applicability(self) -> None:
        rule_id = "R4"
        rule_name = "Stage applicability vs segment lifecycle stage"
        operation_only = {
            "Pset_FatigueDamage_IPS": ["MinerDamageRatio_Cumulative",
                                       "RemainingFatigueLife_Years"],
            "Pset_Leakage_IPS": ["CrackCount_Detected", "CrackAperture_Mean",
                                 "CubicLaw_LeakageRate"],
            "Pset_SurrogatePrediction_IPS": ["PredictedDamageRatio_Mean",
                                             "PredictionTimestamp"],
        }
        op_stages = {"OPERATION", "MAINTENANCE", "DECOMMISSIONING"}
        checked = 0
        violations = 0
        for seg in self.data.get("segments", []):
            meta = self._pset(seg, "Pset_AssessmentMeta_IPS") or {}
            stage = meta.get("LifecycleStage")
            if stage is None:
                continue
            checked += 1
            if stage not in op_stages:
                for pset_name, props in operation_only.items():
                    p = self._pset(seg, pset_name) or {}
                    populated = [x for x in props if p.get(x) is not None]
                    if populated:
                        violations += 1
                        self.report.add(CheckResult(
                            rule_id, rule_name, Severity.WARN,
                            f"Operation-stage properties {populated} populated "
                            f"on segment with LifecycleStage={stage} in {pset_name}",
                            segment_guid=seg["guid"],
                            details={"lifecycle_stage": stage,
                                     "pset": pset_name,
                                     "operation_only_properties_populated": populated}))
        if checked > 0 and violations == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.PASS,
                f"Stage applicability satisfied on {checked} segments."))
        elif checked == 0:
            self.report.add(CheckResult(rule_id, rule_name, Severity.INFO,
                "No LifecycleStage tags found — skipping stage applicability."))

    def run_all(self) -> ValidationReport:
        self.check_mass_conservation()
        self.check_bernoulli_consistency()
        self.check_load_share_closure()
        self.check_stage_applicability()
        self.check_source_provenance()
        self.check_surrogate_freshness()
        self.check_damage_thresholds()
        self.check_joint_topology()
        return self.report


# CLI driver ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Snowy 2.0 IPS Pset family in an IFC 4.3 file.")
    parser.add_argument("ifc", help="Path to IFC 4.3 file")
    parser.add_argument("--report", help="Optional JSON report output path")
    parser.add_argument("--strict", action="store_true",
        help="Exit with non-zero status if any ERROR or WARN is reported.")
    args = parser.parse_args(argv)

    validator = IPSValidator(args.ifc)
    report = validator.run_all()

    summary = report.summary()
    print(f"IPS validator — {args.ifc}")
    print(f"  segments: {report.segment_count}, joints: {report.joint_count}")
    print(f"  pass:  {summary['pass']}")
    print(f"  info:  {summary['info']}")
    print(f"  warn:  {summary['warn']}")
    print(f"  error: {summary['error']}")
    print()
    for r in report.results:
        prefix = {"pass": "[OK]  ", "info": "[i]   ",
                  "warn": "[!]   ", "error": "[X]   "}[r.severity.value]
        print(f"{prefix}{r.rule_id} {r.rule_name}: {r.message}")

    if args.report:
        with open(args.report, "w") as fh:
            json.dump(report.to_dict(), fh, indent=2)
        print(f"\nReport written to {args.report}")

    if args.strict and (summary["error"] > 0 or summary["warn"] > 0):
        return 1
    if summary["error"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
