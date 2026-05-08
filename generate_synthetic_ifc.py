"""
generate_synthetic_ifc.py
=========================

Generate a synthetic Snowy 2.0 IPS IFC 4.3 file with the seven-member Pset
family populated. The output is a real .ifc file that can be:

  * opened in any IFC viewer (Solibri, BIMcollab, BlenderBIM, IfcOpenShell-Bonsai)
  * validated with the IPSValidator class
  * used as a permanent test fixture for vendor-tool conformance

Usage
-----
    python generate_synthetic_ifc.py --output snowy2_ips_synthetic.ifc
    python generate_synthetic_ifc.py --output bad.ifc --inject-errors

The generator builds three IfcAlignmentSegment instances along the IPS
alignment (chainage 1+000 to 2+500), two FACS joints between them, and
attaches the seven Psets with realistic Snowy 2.0 design values. With
--inject-errors, deliberate consistency violations are introduced so the
validator has something to catch.

Author:  Eric's group, University of Melbourne
License: Apache-2.0
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import ifcopenshell
import ifcopenshell.api as api
import ifcopenshell.guid


def _new_guid() -> str:
    return ifcopenshell.guid.new()


def _make_project(model):
    """Create the project, units, and basic context structure."""
    project = api.run(
        "root.create_entity", model,
        ifc_class="IfcProject",
        name="Snowy 2.0 PSH — synthetic IPS test fixture",
    )
    api.run("unit.assign_unit", model)
    return project


def _make_alignment(model, project):
    """Create the IPS alignment as IfcAlignment under the project."""
    alignment = api.run(
        "root.create_entity", model,
        ifc_class="IfcAlignment",
        name="IPS_Alignment_2.7km",
    )
    api.run("aggregate.assign_object", model,
            products=[alignment], relating_object=project)
    return alignment


def _make_segment(model, alignment, name: str) -> object:
    """Create one IfcAlignmentSegment and aggregate it under the alignment."""
    segment = api.run(
        "root.create_entity", model,
        ifc_class="IfcAlignmentSegment",
        name=name,
    )
    api.run("aggregate.assign_object", model,
            products=[segment], relating_object=alignment)
    return segment


def _make_joint(model, project, name: str) -> object:
    """Create one FACS joint as IfcDiscreteAccessory.

    Joints are not spatially contained — they exist as project-scope
    accessories whose only meaningful relationship is to the upstream
    and downstream segments via the GUID references in Pset_FACSJoint_IPS.
    """
    joint = api.run(
        "root.create_entity", model,
        ifc_class="IfcDiscreteAccessory",
        name=name,
    )
    return joint


def _attach_pset(model, product, pset_name: str, properties: dict) -> None:
    """Attach a property set with the given properties to the product."""
    pset = api.run("pset.add_pset", model, product=product, name=pset_name)
    api.run("pset.edit_pset", model, pset=pset, properties=properties)


# Snowy 2.0 IPS realistic design values --------------------------------------
SNOWY2_BASE = {
    "diameter": 6.5,
    "discharge_gen": 337.0,
    "discharge_pump": 280.0,
    "manning": 0.012,
    "design_temp_K": 283.15,
    "water_density": 999.7,
    "water_kvisc": 1.31e-6,
    "static_pressure": 5.84e6,
    "operating_pressure_mean": 4.92e6,
    "operating_pressure_std": 0.18e6,
    "celerity": 1100.0,
    "joukowsky_rise": 11.18e6,
    "concrete_grade": 40.0,
    "concrete_E": 32.0e9,
    "steel_E": 2.1e11,
    "steel_yield": 690e6,
    "steel_thickness": 0.045,
    "steel_grade": "S690QL",
    "design_life_years": 100.0,
    "miner_threshold_action": 0.5,
    "miner_threshold_critical": 1.0,
}


def _hydraulic_pset(seg_data: dict, inject_errors: bool, seg_idx: int) -> dict:
    """Build the Pset_HydraulicPerformance_IPS dict for one segment.

    Operating pressure declines along the alignment by an amount equal to
    the segment head loss times rho*g, so that adjacent-segment differences
    satisfy Bernoulli consistency under the assumption of constant velocity
    (the alignment has uniform diameter, so velocity is identical and the
    velocity-head term cancels).
    """
    head_loss_per_segment = 0.0142 * seg_data["length"]
    pressure_drop_per_segment = (
        head_loss_per_segment * SNOWY2_BASE["water_density"] * 9.81
    )
    operating_pressure = (
        SNOWY2_BASE["operating_pressure_mean"]
        - seg_idx * pressure_drop_per_segment
    )

    p = {
        "SegmentChainageStart": seg_data["chainage_start"],
        "SegmentChainageEnd": seg_data["chainage_end"],
        "SegmentInclinationAngle": seg_data["inclination_rad"],
        "InternalDiameter": SNOWY2_BASE["diameter"],
        "InternalDiameter_Source": "DESIGN",
        "DesignDischarge_Generating": SNOWY2_BASE["discharge_gen"],
        "DesignDischarge_Pumping": SNOWY2_BASE["discharge_pump"],
        "DesignDischarge_Source": "DESIGN",
        "MeanFlowVelocity_Generating": 10.16,
        "MeanFlowVelocity_Pumping": 8.44,
        "ManningRoughnessCoefficient": SNOWY2_BASE["manning"],
        "Manning_Source": "DESIGN",
        "DarcyFrictionFactor": 0.014,
        "ReynoldsNumber_Generating": 5.04e7,
        "HeadLossPerUnitLength": 0.0142,
        "HeadLoss_Segment": head_loss_per_segment,
        "StaticPressure_Maximum": SNOWY2_BASE["static_pressure"],
        "OperatingPressure_Mean": operating_pressure,
        "OperatingPressure_StdDev": SNOWY2_BASE["operating_pressure_std"],
        "WaterTemperature_Design": SNOWY2_BASE["design_temp_K"],
        "WaterDensity": SNOWY2_BASE["water_density"],
        "WaterKinematicViscosity": SNOWY2_BASE["water_kvisc"],
    }
    if inject_errors and seg_idx == 1:
        p["DesignDischarge_Generating"] = 250.0
    if inject_errors and seg_idx == 0:
        del p["DesignDischarge_Source"]
        p["OperatingPressure_Mean"] = SNOWY2_BASE["operating_pressure_mean"] - 1e6
    return p


def _composite_lining_pset(seg_data: dict, inject_errors: bool, seg_idx: int) -> dict:
    """Build the Pset_CompositeLining_IPS dict."""
    if inject_errors and seg_idx == 0:
        c, s, r = 0.40, 0.30, 0.38
    else:
        c, s, r = (0.32, 0.30, 0.38) if seg_idx == 0 else \
                  (0.30, 0.28, 0.42) if seg_idx == 1 else \
                  (0.31, 0.29, 0.40)
    return {
        "RockMass_GSI": seg_data["GSI"],
        "RockMass_RMR": seg_data["RMR"],
        "RockMass_Q": seg_data["Q"],
        "RockMass_UCS": 120.0e6,
        "RockMass_DeformationModulus": seg_data["E_rock"],
        "RockMass_PoissonRatio": 0.25,
        "Rock_Source": "FIELD_MONITORING",
        "OverburdenDepth_Vertical": seg_data["overburden"],
        "OverburdenDepth_LateralMin": seg_data["overburden"] * 0.8,
        "NorwegianConfinement_FactorOfSafety": 1.45,
        "ConcreteLining_Thickness": 0.6,
        "ConcreteLining_GradeMPa": SNOWY2_BASE["concrete_grade"],
        "ConcreteLining_E": SNOWY2_BASE["concrete_E"],
        "SteelLiner_Thickness": SNOWY2_BASE["steel_thickness"],
        "SteelLiner_Grade": SNOWY2_BASE["steel_grade"],
        "SteelLiner_YieldStrength": SNOWY2_BASE["steel_yield"],
        "SteelLiner_E": SNOWY2_BASE["steel_E"],
        "EinsteinSchwartz_LoadShareConcrete": c,
        "EinsteinSchwartz_LoadShareSteelLiner": s,
        "EinsteinSchwartz_LoadShareRockMass": r,
        "ES_Source": "FLAC3D",
        "SteelLiner_HoopStress_Internal": 285e6,
        "SteelLiner_HoopStress_StdDev": 12e6,
        "SteelLiner_BucklingPressure_External": 8.2e6,
        "SteelLiner_BucklingFoS": 1.85,
    }


def _transient_pset(seg_data: dict) -> dict:
    return {
        "WaveCelerity_Composite": SNOWY2_BASE["celerity"],
        "Celerity_Source": "DESIGN",
        "JoukowskyPressureRise_Sudden": SNOWY2_BASE["joukowsky_rise"],
        "MichaudPressureRise_Linear": 3.5e6,
        "ClosureTime_Design": 8.0,
        "MOC_PressureEnvelopeMax": 7.2e6,
        "MOC_PressureEnvelopeMin": 1.8e6,
        "MOC_Source": "FLAC3D",
        "ColumnSeparationRisk": "LOW",
    }


def _fatigue_pset(seg_data: dict, inject_errors: bool, seg_idx: int) -> dict:
    p = {
        "DetailCategory_EN1993": "90",
        "StressRange_Design": 90e6,
        "StressRange_Source": "DESIGN",
        "DesignLifeCycles": 2000000,
        "DesignLifeYears": SNOWY2_BASE["design_life_years"],
        "CyclesPerDay_Generating": 4.0,
        "CyclesPerDay_Pumping": 2.0,
        "CyclesPerDay_TransientEvent": 0.1,
        "MinerDamageRatio_Cumulative": [0.31, 0.27, 0.29][seg_idx],
        "MinerDamageRatio_StdDev": 0.04,
        "Miner_Source": "SURROGATE_DEEPONET",
        "MinerDamageThreshold_Action": SNOWY2_BASE["miner_threshold_action"],
        "MinerDamageThreshold_Critical": SNOWY2_BASE["miner_threshold_critical"],
        "RemainingFatigueLife_Years": 65.0,
        "RemainingFatigueLife_StdDev": 8.0,
    }
    if inject_errors and seg_idx == 2:
        p["MinerDamageThreshold_Action"] = 1.0
        p["MinerDamageThreshold_Critical"] = 0.8
    return p


def _leakage_pset(seg_idx: int) -> dict:
    return {
        "CrackCount_Detected": [2, 0, 1][seg_idx],
        "CrackAperture_Mean": [0.42e-3, 0.0, 0.18e-3][seg_idx],
        "CrackAperture_StdDev": [0.05e-3, 0.0, 0.04e-3][seg_idx],
        "CrackAperture_Source": "VLM_INFERRED",
        "CrackLength_Total": [0.84, 0.0, 0.32][seg_idx],
        "CrackOrientation_Dominant": "LONGITUDINAL",
        "CubicLaw_LeakageRate": [1.2e-3, 0.0, 0.15e-3][seg_idx],
        "CubicLaw_LeakageRate_StdDev": [0.2e-3, 0.0, 0.04e-3][seg_idx],
        "Leakage_Source": "HAND_CALC",
        "LeakageThreshold_Investigation": 0.5e-3,
        "LeakageThreshold_Action": 2.0e-3,
    }


def _surrogate_pset(seg_idx: int, inject_errors: bool) -> dict:
    if inject_errors and seg_idx == 0:
        ts = "2026-01-15T10:00:00+00:00"
    else:
        ts = "2026-04-25T10:00:00+00:00"
    return {
        "SurrogateModelType": "DeepONet",
        "SurrogateModelVersion": "v3.2-2026-Q2",
        "SurrogateModelHash": "sha256:7a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d",
        "TrainingDatasetSize": 24000,
        "TrainingDatasetHash": "sha256:1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d",
        "InputScenario_DailyCycles": 4.0,
        "InputScenario_HeadAmplitude": 4.0e6,
        "InputScenario_GSI": [58.0, 62.0, 60.0][seg_idx],
        "PredictedDamageRatio_Mean": [0.31, 0.27, 0.29][seg_idx],
        "PredictedDamageRatio_StdDev": 0.04,
        "PredictedDamageRatio_CI95_Lower": [0.23, 0.19, 0.21][seg_idx],
        "PredictedDamageRatio_CI95_Upper": [0.39, 0.35, 0.37][seg_idx],
        "PredictedHoopStress_Mean": 285e6,
        "PredictedHoopStress_StdDev": 12e6,
        "InferenceTime_Milliseconds": 8.2,
        "GroundTruthComparison_FLAC3D": [0.32, 0.28, 0.30][seg_idx],
        "PredictionTimestamp": ts,
        "DistributionType": "Gaussian",
    }


def _meta_pset(seg_idx: int = 0, inject_errors: bool = False) -> dict:
    if inject_errors and seg_idx == 2:
        stage = "DESIGN"
    else:
        stage = "OPERATION"
    return {
        "AssessmentCycleID": "ASSESS-2026-Q2-001",
        "AssessmentTimestamp": "2026-05-08T14:00:00+00:00",
        "LifecycleStage": stage,
        "AssessmentBoundary": "IPS_FullScope",
        "VerificationStatus": "PENDING",
        "VerificationChecksRun": 0,
        "VerificationChecksPassed": 0,
        "IndependentReviewCompleted": False,
        "ReviewerIdentity": "Snowy Hydro Asset Engineering",
        "OpenNonConformanceCount": 0,
        "HumanReviewRequired": True,
    }


def _facs_joint_pset(upstream_guid: str, downstream_guid: str,
                     stationing: float, inject_broken_topology: bool) -> dict:
    if inject_broken_topology:
        downstream_guid = "missing_segment_guid"
    return {
        "JointGUID_Upstream": upstream_guid,
        "JointGUID_Downstream": downstream_guid,
        "JointStationing": stationing,
        "JointType": "WATERSTOP_HYDROPHILIC",
        "RotationalStiffness_Design": 1.8e9,
        "AxialStiffness_Design": 3.5e9,
        "JointOpening_Design": 1.5e-3,
        "JointOpening_Predicted": 1.2e-3,
        "JointOpening_Allowable": 3.0e-3,
        "Opening_Source": "FLAC3D",
        "WaterstopType": "Sika Hydrotite CJ",
        "WaterstopRatedPressure": 8.0e6,
        "JointHealthScore": 0.92,
    }


SEGMENT_DEFINITIONS = [
    {
        "name": "IPS-Sta-1+250", "chainage_start": 1000.0, "chainage_end": 1500.0,
        "length": 500.0, "inclination_rad": 0.6981,
        "GSI": 58.0, "RMR": 65.0, "Q": 8.0,
        "E_rock": 1.2e10, "overburden": 650.0,
    },
    {
        "name": "IPS-Sta-1+750", "chainage_start": 1500.0, "chainage_end": 2000.0,
        "length": 500.0, "inclination_rad": 0.6981,
        "GSI": 62.0, "RMR": 70.0, "Q": 12.0,
        "E_rock": 1.5e10, "overburden": 720.0,
    },
    {
        "name": "IPS-Sta-2+250", "chainage_start": 2000.0, "chainage_end": 2500.0,
        "length": 500.0, "inclination_rad": 0.6981,
        "GSI": 60.0, "RMR": 67.0, "Q": 10.0,
        "E_rock": 1.35e10, "overburden": 800.0,
    },
]


def build_synthetic_ifc(output_path: Path, inject_errors: bool = False) -> dict:
    """Build the synthetic IFC file and return a summary dict."""
    model = ifcopenshell.file(schema="IFC4X3")

    project = _make_project(model)
    alignment = _make_alignment(model, project)

    segments = []
    for idx, sd in enumerate(SEGMENT_DEFINITIONS):
        seg = _make_segment(model, alignment, sd["name"])
        _attach_pset(model, seg, "Pset_HydraulicPerformance_IPS",
                     _hydraulic_pset(sd, inject_errors, idx))
        _attach_pset(model, seg, "Pset_TransientAnalysis_IPS",
                     _transient_pset(sd))
        _attach_pset(model, seg, "Pset_CompositeLining_IPS",
                     _composite_lining_pset(sd, inject_errors, idx))
        _attach_pset(model, seg, "Pset_FatigueDamage_IPS",
                     _fatigue_pset(sd, inject_errors, idx))
        _attach_pset(model, seg, "Pset_Leakage_IPS",
                     _leakage_pset(idx))
        _attach_pset(model, seg, "Pset_SurrogatePrediction_IPS",
                     _surrogate_pset(idx, inject_errors))
        _attach_pset(model, seg, "Pset_AssessmentMeta_IPS",
                     _meta_pset(idx, inject_errors))
        segments.append(seg)

    joint1 = _make_joint(model, project, "FACS-Joint-1+500")
    _attach_pset(model, joint1, "Pset_FACSJoint_IPS",
                 _facs_joint_pset(segments[0].GlobalId,
                                  segments[1].GlobalId,
                                  1500.0,
                                  inject_broken_topology=inject_errors))

    joint2 = _make_joint(model, project, "FACS-Joint-2+000")
    _attach_pset(model, joint2, "Pset_FACSJoint_IPS",
                 _facs_joint_pset(segments[1].GlobalId,
                                  segments[2].GlobalId,
                                  2000.0,
                                  inject_broken_topology=False))

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model.write(str(output_path))

    summary = {
        "output_path": str(output_path),
        "schema": "IFC4X3",
        "segments": len(segments),
        "joints": 2,
        "inject_errors": inject_errors,
        "file_size_bytes": output_path.stat().st_size,
        "segment_guids": [s.GlobalId for s in segments],
        "joint_guids": [joint1.GlobalId, joint2.GlobalId],
    }
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate synthetic Snowy 2.0 IPS IFC 4.3 file.")
    parser.add_argument("--output", "-o", default="snowy2_ips_synthetic.ifc",
                        help="Output IFC file path")
    parser.add_argument("--inject-errors", action="store_true",
                        help="Inject realistic engineering errors for validator testing")
    args = parser.parse_args(argv)

    summary = build_synthetic_ifc(Path(args.output), args.inject_errors)

    print("Synthetic IFC generated.")
    print(f"  Output:     {summary['output_path']}")
    print(f"  Schema:     {summary['schema']}")
    print(f"  Segments:   {summary['segments']}")
    print(f"  Joints:     {summary['joints']}")
    print(f"  Errors:     {'INJECTED' if summary['inject_errors'] else 'none'}")
    print(f"  File size:  {summary['file_size_bytes']} bytes")
    print()
    print("  Segment GUIDs:")
    for g in summary["segment_guids"]:
        print(f"    {g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
