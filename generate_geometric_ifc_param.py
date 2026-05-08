"""
generate_geometric_ifc_param.py
================================

Parameterized geometric IFC4X3 tunnel model generator for the Snowy 2.0 IPS.

Every dimension, colour, and segment-level value is user-controllable via a
GeometryParams dataclass. The Streamlit dashboard exposes these as live UI
controls so users can change diameter, segment length, lining thickness,
joint size, and per-segment stationing before regenerating the IFC.

Usage — programmatic
--------------------
    from generate_geometric_ifc_param import (
        GeometryParams, SegmentSpec, build_parametric_geometric_ifc
    )

    params = GeometryParams(
        outer_radius=12.0,
        lining_thickness=1.5,
        display_length=150.0,
        display_gap=40.0,
        joint_radius=14.0,
        segments=[
            SegmentSpec(label="seg_1+250", chainage_start=1000.0, chainage_end=1500.0),
            SegmentSpec(label="seg_1+750", chainage_start=1500.0, chainage_end=2000.0),
            SegmentSpec(label="seg_2+250", chainage_start=2000.0, chainage_end=2500.0),
        ],
    )
    summary = build_parametric_geometric_ifc("snowy2_user.ifc", params,
                                              inject_errors=False)

Usage — CLI fallback
--------------------
    python generate_geometric_ifc_param.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ifcopenshell


# ----- Parameters -----------------------------------------------------------

@dataclass
class SegmentSpec:
    """Per-segment user-editable parameters."""
    label: str
    chainage_start: float
    chainage_end: float
    GSI: float = 60.0
    discharge_generating: float = 337.0
    discharge_pumping: float = 280.0
    miner_damage_ratio: float = 0.30
    miner_threshold_action: float = 0.5
    miner_threshold_critical: float = 1.0
    crack_count: int = 3
    crack_aperture_mm: float = 0.15
    load_share_concrete: float = 0.31
    load_share_steel: float = 0.29
    load_share_rock: float = 0.40
    lifecycle_stage: str = "OPERATION"


@dataclass
class GeometryParams:
    """All user-editable geometric and visual parameters for the IFC model."""
    outer_radius: float = 10.0
    lining_thickness: float = 1.2
    display_length: float = 120.0
    display_gap: float = 35.0
    joint_radius: float = 12.0
    joint_height: float = 4.0
    operating_pressure_mean: float = 4.92e6
    head_loss_per_segment: float = 7.1
    water_density: float = 999.7
    color_pass: tuple = (0.10, 0.65, 0.20)
    color_warn: tuple = (1.00, 0.55, 0.05)
    color_error: tuple = (0.95, 0.10, 0.10)
    color_joint: tuple = (0.10, 0.30, 0.90)
    project_name: str = "Snowy 2.0 IPS Geometric Validator Demo"
    site_name: str = "Synthetic Snowy 2.0 IPS Site"
    segments: list[SegmentSpec] = field(default_factory=lambda: [
        SegmentSpec("seg_1+250", 1000.0, 1500.0, GSI=58.0,
                    miner_damage_ratio=0.31, crack_count=4,
                    crack_aperture_mm=0.15,
                    load_share_concrete=0.32, load_share_steel=0.30,
                    load_share_rock=0.38),
        SegmentSpec("seg_1+750", 1500.0, 2000.0, GSI=62.0,
                    miner_damage_ratio=0.27, crack_count=2,
                    crack_aperture_mm=0.09,
                    load_share_concrete=0.30, load_share_steel=0.28,
                    load_share_rock=0.42),
        SegmentSpec("seg_2+250", 2000.0, 2500.0, GSI=60.0,
                    miner_damage_ratio=0.29, crack_count=5,
                    crack_aperture_mm=0.21,
                    load_share_concrete=0.31, load_share_steel=0.29,
                    load_share_rock=0.40),
    ])

    def validate(self) -> list[str]:
        """Return a list of validation problems for this parameter set.

        These are pre-build sanity checks that catch obvious user errors
        before invoking IfcOpenShell. They are not the same as the post-build
        IPS validator rules — those run on the produced IFC file.
        """
        problems = []
        if self.outer_radius <= self.lining_thickness:
            problems.append(
                f"Outer radius ({self.outer_radius:.2f}) must be greater than "
                f"lining thickness ({self.lining_thickness:.2f}).")
        if self.outer_radius <= 0.5:
            problems.append("Outer radius must be greater than 0.5 m.")
        if self.lining_thickness <= 0.05:
            problems.append("Lining thickness must be greater than 0.05 m.")
        if self.display_length <= 5.0:
            problems.append("Display length must be greater than 5 m.")
        if self.display_gap < 0.0:
            problems.append("Display gap cannot be negative.")
        if len(self.segments) < 2:
            problems.append("At least 2 segments are required.")
        for i, seg in enumerate(self.segments):
            if seg.chainage_end <= seg.chainage_start:
                problems.append(
                    f"Segment {i+1} '{seg.label}': chainage end ({seg.chainage_end}) "
                    f"must be greater than start ({seg.chainage_start}).")
        for i in range(1, len(self.segments)):
            prev = self.segments[i - 1]
            curr = self.segments[i]
            if abs(prev.chainage_end - curr.chainage_start) > 0.001:
                problems.append(
                    f"Chainage gap between segments {i} and {i+1}: "
                    f"{prev.label} ends at {prev.chainage_end}, "
                    f"{curr.label} starts at {curr.chainage_start}.")
        return problems


# ----- IFC helpers (same as upstream, parameterised where relevant) ---------

def _guid() -> str:
    return ifcopenshell.guid.new()


def _cartesian_point(f, xyz):
    return f.create_entity("IfcCartesianPoint",
                           Coordinates=tuple(float(x) for x in xyz))


def _direction(f, xyz):
    return f.create_entity("IfcDirection",
                           DirectionRatios=tuple(float(x) for x in xyz))


def _axis2placement3d(f, location, axis=(0.0, 0.0, 1.0),
                     ref_direction=(1.0, 0.0, 0.0)):
    return f.create_entity(
        "IfcAxis2Placement3D",
        Location=_cartesian_point(f, location),
        Axis=_direction(f, axis),
        RefDirection=_direction(f, ref_direction),
    )


def _local_placement(f, location=(0.0, 0.0, 0.0), relative_to=None,
                     axis=(0.0, 0.0, 1.0), ref_direction=(1.0, 0.0, 0.0)):
    return f.create_entity(
        "IfcLocalPlacement",
        PlacementRelTo=relative_to,
        RelativePlacement=_axis2placement3d(f, location, axis, ref_direction),
    )


def _owner_history(f):
    person = f.create_entity("IfcPerson", FamilyName="IPS Validator")
    org = f.create_entity("IfcOrganization", Name="IPS Demo")
    person_org = f.create_entity("IfcPersonAndOrganization",
                                 ThePerson=person, TheOrganization=org)
    app_org = f.create_entity("IfcOrganization", Name="IfcOpenShell Demo")
    app = f.create_entity("IfcApplication", ApplicationDeveloper=app_org,
                          Version="2.0",
                          ApplicationFullName="Parametric IPS IFC generator",
                          ApplicationIdentifier="IPS-GEN-PARAM")
    return f.create_entity("IfcOwnerHistory", OwningUser=person_org,
                           OwningApplication=app, ChangeAction="ADDED",
                           CreationDate=int(datetime.now(timezone.utc).timestamp()))


def _unit_assignment(f):
    length = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    area = f.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE")
    volume = f.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE")
    angle = f.create_entity("IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN")
    return f.create_entity("IfcUnitAssignment", Units=[length, area, volume, angle])


def _make_colour_style(f, name, rgb, transparency=0.0):
    colour = f.create_entity("IfcColourRgb", Name=name,
                             Red=float(rgb[0]), Green=float(rgb[1]),
                             Blue=float(rgb[2]))
    shading = f.create_entity("IfcSurfaceStyleShading", SurfaceColour=colour,
                              Transparency=float(transparency))
    return f.create_entity("IfcSurfaceStyle", Name=name, Side="BOTH",
                           Styles=[shading])


def _apply_style(f, solid, style, name):
    f.create_entity("IfcStyledItem", Item=solid, Styles=[style], Name=name)


def _make_hollow_segment_representation(f, context, length, outer_radius,
                                        lining_thickness, style=None,
                                        style_name="segment_style"):
    inner_radius = max(outer_radius - lining_thickness, 0.1)
    profile = f.create_entity(
        "IfcCircleHollowProfileDef",
        ProfileType="AREA",
        ProfileName="Circular tunnel lining profile",
        Position=f.create_entity(
            "IfcAxis2Placement2D",
            Location=f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0))),
        Radius=float(outer_radius),
        WallThickness=float(outer_radius - inner_radius),
    )
    solid = f.create_entity("IfcExtrudedAreaSolid", SweptArea=profile,
                            Position=_axis2placement3d(f, (0.0, 0.0, 0.0)),
                            ExtrudedDirection=_direction(f, (0.0, 0.0, 1.0)),
                            Depth=float(length))
    if style is not None:
        _apply_style(f, solid, style, style_name)
    shape = f.create_entity("IfcShapeRepresentation", ContextOfItems=context,
                            RepresentationIdentifier="Body",
                            RepresentationType="SweptSolid", Items=[solid])
    return f.create_entity("IfcProductDefinitionShape", Representations=[shape])


def _make_joint_representation(f, context, radius, height, style=None,
                               style_name="joint_style"):
    profile = f.create_entity(
        "IfcCircleProfileDef", ProfileType="AREA",
        ProfileName="Joint marker profile",
        Position=f.create_entity(
            "IfcAxis2Placement2D",
            Location=f.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0))),
        Radius=float(radius),
    )
    solid = f.create_entity("IfcExtrudedAreaSolid", SweptArea=profile,
                            Position=_axis2placement3d(f, (0.0, 0.0, -height / 2.0)),
                            ExtrudedDirection=_direction(f, (0.0, 0.0, 1.0)),
                            Depth=float(height))
    if style is not None:
        _apply_style(f, solid, style, style_name)
    shape = f.create_entity("IfcShapeRepresentation", ContextOfItems=context,
                            RepresentationIdentifier="Body",
                            RepresentationType="SweptSolid", Items=[solid])
    return f.create_entity("IfcProductDefinitionShape", Representations=[shape])


def _ifc_value(f, value):
    if isinstance(value, bool):
        return f.create_entity("IfcBoolean", wrappedValue=value)
    if isinstance(value, int):
        return f.create_entity("IfcInteger", wrappedValue=value)
    if isinstance(value, float):
        return f.create_entity("IfcReal", wrappedValue=value)
    return f.create_entity("IfcText", wrappedValue=str(value))


def _attach_pset(f, owner_history, product, name, props):
    ifc_props = [f.create_entity("IfcPropertySingleValue", Name=k,
                                 NominalValue=_ifc_value(f, v), Unit=None)
                 for k, v in props.items()]
    pset = f.create_entity("IfcPropertySet", GlobalId=_guid(),
                           OwnerHistory=owner_history, Name=name,
                           HasProperties=ifc_props)
    f.create_entity("IfcRelDefinesByProperties", GlobalId=_guid(),
                    OwnerHistory=owner_history, RelatedObjects=[product],
                    RelatingPropertyDefinition=pset)
    return pset


# ----- Pset construction from SegmentSpec -----------------------------------

def _segment_psets_from_spec(spec: SegmentSpec, index: int,
                             params: GeometryParams,
                             inject_errors: bool = False) -> dict[str, dict[str, Any]]:
    """Build the seven IPS Psets for one segment using the parameter dataclass."""
    g = 9.81
    pressure_start = (params.operating_pressure_mean
                      - index * params.head_loss_per_segment
                      * params.water_density * g)
    hydraulic = {
        "SegmentChainageStart": spec.chainage_start,
        "SegmentChainageEnd": spec.chainage_end,
        "InternalDiameter": (params.outer_radius - params.lining_thickness) * 2.0,
        "InternalDiameter_Source": "DESIGN",
        "TunnelLiningThickness": params.lining_thickness,
        "DesignDischarge_Generating": spec.discharge_generating,
        "DesignDischarge_Pumping": spec.discharge_pumping,
        "DesignDischarge_Source": "DESIGN",
        "MeanFlowVelocity_Generating": 10.16,
        "ManningRoughnessCoefficient": 0.012,
        "Manning_Source": "DESIGN",
        "HeadLoss_Segment": params.head_loss_per_segment,
        "OperatingPressure_Mean": pressure_start,
        "WaterDensity": params.water_density,
    }
    composite = {
        "RockMass_GSI": spec.GSI,
        "Rock_Source": "FIELD_MONITORING",
        "RockMass_DeformationModulus": 1.5e10,
        "EinsteinSchwartz_LoadShareConcrete": spec.load_share_concrete,
        "EinsteinSchwartz_LoadShareSteelLiner": spec.load_share_steel,
        "EinsteinSchwartz_LoadShareRockMass": spec.load_share_rock,
        "ES_Source": "FLAC3D",
    }
    fatigue = {
        "MinerDamageRatio_Cumulative": spec.miner_damage_ratio,
        "Miner_Source": "SURROGATE_DEEPONET",
        "MinerDamageThreshold_Action": spec.miner_threshold_action,
        "MinerDamageThreshold_Critical": spec.miner_threshold_critical,
        "RemainingFatigueLife_Years": 50.0,
    }
    leakage = {
        "CrackCount_Detected": spec.crack_count,
        "CrackAperture_Mean": spec.crack_aperture_mm,
        "CrackAperture_Source": "VLM_INFERRED",
        "CubicLaw_LeakageRate": 0.002,
        "Leakage_Source": "HAND_CALC",
    }
    surrogate = {
        "PredictionTimestamp": "2026-04-25T10:00:00+00:00",
        "PredictedDamageRatio_Mean": spec.miner_damage_ratio,
        "PredictedDamageRatio_StdDev": 0.04,
    }
    meta = {
        "AssessmentCycleID": "ASSESS-2026-Q2-001",
        "AssessmentTimestamp": "2026-05-08T14:00:00+00:00",
        "VerificationStatus": "PASSED",
        "LifecycleStage": spec.lifecycle_stage,
    }
    if inject_errors:
        if index == 0:
            hydraulic["OperatingPressure_Mean"] = 3.92e6
            composite["EinsteinSchwartz_LoadShareConcrete"] = 0.40
            hydraulic.pop("DesignDischarge_Source", None)
            surrogate["PredictionTimestamp"] = "2026-01-15T10:00:00+00:00"
        if index == 1:
            hydraulic["DesignDischarge_Generating"] = 250.0
        if index == 2:
            meta["LifecycleStage"] = "DESIGN"
            fatigue["MinerDamageThreshold_Action"] = 1.0
            fatigue["MinerDamageThreshold_Critical"] = 0.8
    return {
        "Pset_HydraulicPerformance_IPS": hydraulic,
        "Pset_CompositeLining_IPS": composite,
        "Pset_FatigueDamage_IPS": fatigue,
        "Pset_Leakage_IPS": leakage,
        "Pset_SurrogatePrediction_IPS": surrogate,
        "Pset_AssessmentMeta_IPS": meta,
    }


# ----- Main parameterised builder -------------------------------------------

def build_parametric_geometric_ifc(output_path,
                                    params: GeometryParams,
                                    inject_errors: bool = False) -> dict[str, Any]:
    """Build a geometric IFC4X3 model using the supplied parameters."""
    problems = params.validate()
    if problems:
        raise ValueError(
            "Cannot build IFC — parameter validation failed:\n  - "
            + "\n  - ".join(problems))

    f = ifcopenshell.file(schema="IFC4X3")
    owner = _owner_history(f)

    project = f.create_entity("IfcProject", GlobalId=_guid(), OwnerHistory=owner,
                              Name=params.project_name,
                              UnitsInContext=_unit_assignment(f))
    context = f.create_entity("IfcGeometricRepresentationContext",
                              ContextIdentifier="Model", ContextType="Model",
                              CoordinateSpaceDimension=3, Precision=1.0e-5,
                              WorldCoordinateSystem=_axis2placement3d(f, (0.0, 0.0, 0.0)))
    project.RepresentationContexts = [context]
    site = f.create_entity("IfcSite", GlobalId=_guid(), OwnerHistory=owner,
                           Name=params.site_name,
                           ObjectPlacement=_local_placement(f))
    f.create_entity("IfcRelAggregates", GlobalId=_guid(), OwnerHistory=owner,
                    RelatingObject=project, RelatedObjects=[site])

    pass_style = _make_colour_style(f, "PASS_GREEN", params.color_pass)
    warn_style = _make_colour_style(f, "WARN_ORANGE", params.color_warn)
    error_style = _make_colour_style(f, "ERROR_RED", params.color_error)
    joint_style = _make_colour_style(f, "JOINT_BLUE", params.color_joint)

    products = []
    segment_guids = []
    alignment_seg_guids = []

    # Create a parent IfcAlignment so the IfcAlignmentSegment shadows have a
    # canonical owner. This is what the IPS validator queries for.
    alignment = f.create_entity(
        "IfcAlignment", GlobalId=_guid(), OwnerHistory=owner,
        Name="IPS_Alignment", ObjectPlacement=_local_placement(f))
    f.create_entity("IfcRelAggregates", GlobalId=_guid(), OwnerHistory=owner,
                    RelatingObject=site, RelatedObjects=[alignment])

    for i, spec in enumerate(params.segments):
        display_x = i * (params.display_length + params.display_gap)

        if not inject_errors:
            seg_style = pass_style
            status = "PASS"
            note = "Clean demonstration segment"
        else:
            if i == 0:
                seg_style = error_style
                status = "ERROR"
                note = "R2 R3 R5 R6 injected"
            elif i == 1:
                seg_style = warn_style
                status = "WARN"
                note = "R1 discharge mismatch with adjacent segment"
            else:
                seg_style = error_style
                status = "ERROR"
                note = "R4 R7 injected"

        placement = _local_placement(
            f, location=(display_x, 0.0, 0.0),
            relative_to=site.ObjectPlacement,
            axis=(1.0, 0.0, 0.0), ref_direction=(0.0, 1.0, 0.0))

        # Visual proxy — what the user sees in the IFC viewer
        segment = f.create_entity(
            "IfcBuildingElementProxy", GlobalId=_guid(), OwnerHistory=owner,
            Name=f"Tunnel Segment {spec.label}",
            ObjectType="IPS_TUNNEL_SEGMENT", ObjectPlacement=placement,
            Representation=_make_hollow_segment_representation(
                f, context, length=params.display_length,
                outer_radius=params.outer_radius,
                lining_thickness=params.lining_thickness,
                style=seg_style, style_name=f"{spec.label}_{status}"))
        segment_guids.append(segment.GlobalId)
        products.append(segment)

        # Canonical IfcAlignmentSegment shadow — what the validator queries
        align_seg = f.create_entity(
            "IfcAlignmentSegment", GlobalId=_guid(), OwnerHistory=owner,
            Name=spec.label,
            ObjectPlacement=_local_placement(f, location=(display_x, 0.0, 0.0),
                                             relative_to=site.ObjectPlacement))
        alignment_seg_guids.append(align_seg.GlobalId)
        f.create_entity("IfcRelAggregates", GlobalId=_guid(),
                        OwnerHistory=owner, RelatingObject=alignment,
                        RelatedObjects=[align_seg])

        _attach_pset(f, owner, segment, "Pset_IPSIdentity", {
            "IPS_GUID": spec.label, "SegmentIndex": i + 1,
            "DisplayStartX": display_x,
            "DisplayEndX": display_x + params.display_length,
            "RealChainageStart": spec.chainage_start,
            "RealChainageEnd": spec.chainage_end,
            "VisualValidationStatus": status,
            "VisualValidationNote": note,
            "AlignmentSegmentGUID": align_seg.GlobalId,
        })

        # Attach the seven IPS Psets to the canonical IfcAlignmentSegment
        # so the validator sees them. The visual proxy carries identity only.
        for pset_name, props in _segment_psets_from_spec(
                spec, i, params, inject_errors).items():
            _attach_pset(f, owner, align_seg, pset_name, props)

    joint_guids = []
    n = len(params.segments)
    for j in range(n - 1):
        upstream = alignment_seg_guids[j]
        if inject_errors and j == 0:
            downstream = "missing_segment_guid"
        else:
            downstream = alignment_seg_guids[j + 1]

        display_x = ((j + 1) * params.display_length
                     + (j + 0.5) * params.display_gap)
        real_station = (params.segments[j].chainage_end
                        + params.segments[j + 1].chainage_start) / 2.0

        joint_label = f"joint_{int(real_station)}"

        # Visual proxy joint — what the user sees in the IFC viewer
        joint = f.create_entity(
            "IfcBuildingElementProxy", GlobalId=_guid(), OwnerHistory=owner,
            Name=f"FACS Joint {joint_label}",
            ObjectType="IPS_FACS_JOINT",
            ObjectPlacement=_local_placement(
                f, location=(display_x, 0.0, 0.0),
                relative_to=site.ObjectPlacement,
                axis=(1.0, 0.0, 0.0), ref_direction=(0.0, 1.0, 0.0)),
            Representation=_make_joint_representation(
                f, context, radius=params.joint_radius,
                height=params.joint_height, style=joint_style,
                style_name=joint_label))
        joint_guids.append(joint.GlobalId)
        products.append(joint)

        # Canonical IfcDiscreteAccessory shadow — what R8 queries against
        joint_accessory = f.create_entity(
            "IfcDiscreteAccessory", GlobalId=_guid(), OwnerHistory=owner,
            Name=joint_label,
            ObjectPlacement=_local_placement(f, location=(display_x, 0.0, 0.0),
                                             relative_to=site.ObjectPlacement))

        _attach_pset(f, owner, joint_accessory, "Pset_FACSJoint_IPS", {
            "JointGUID_Upstream": upstream,
            "JointGUID_Downstream": downstream,
            "JointStationing": real_station,
            "JointLabel": joint_label,
            "DisplayX": display_x,
        })

    f.create_entity("IfcRelContainedInSpatialStructure", GlobalId=_guid(),
                    OwnerHistory=owner, RelatedElements=products,
                    RelatingStructure=site)

    output_path = Path(output_path)
    f.write(str(output_path))
    return {
        "output_path": output_path.as_posix(),
        "schema": f.schema,
        "segments": len(segment_guids),
        "joints": len(joint_guids),
        "inject_errors": inject_errors,
        "file_size_bytes": output_path.stat().st_size,
        "outer_radius": params.outer_radius,
        "lining_thickness": params.lining_thickness,
        "display_length": params.display_length,
        "display_gap": params.display_gap,
        "joint_radius": params.joint_radius,
        "total_real_chainage_m": (params.segments[-1].chainage_end
                                   - params.segments[0].chainage_start),
        "total_display_extent_m": ((len(params.segments) - 1)
                                    * (params.display_length + params.display_gap)
                                    + params.display_length),
    }


if __name__ == "__main__":
    p = GeometryParams()
    print(build_parametric_geometric_ifc("snowy2_param_clean.ifc", p, False))
    print(build_parametric_geometric_ifc("snowy2_param_faulty.ifc", p, True))
