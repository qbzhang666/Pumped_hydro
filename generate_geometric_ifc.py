"""Generate a simplified geometric IFC4X3 tunnel model for the Snowy 2.0 IPS validator.

Run:
    python generate_geometric_ifc.py

Outputs:
    snowy2_geometric_clean.ifc
    snowy2_geometric_faulty.ifc

Improvements:
- Larger visible tunnel radius for model-view demonstration
- Visible gaps between tunnel segments
- Individual segment objects: seg_1+250, seg_1+750, seg_2+250
- Joint markers between segments
- Colour styling:
    clean model: green segments
    faulty model: red/orange/green segment demonstration
- IPS property sets attached to each segment and joint
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple

try:
    import ifcopenshell
except ImportError as exc:
    raise SystemExit("Install IfcOpenShell first: pip install ifcopenshell") from exc


# ---------------------------------------------------------------------
# Basic IFC helpers
# ---------------------------------------------------------------------

def _guid() -> str:
    return ifcopenshell.guid.new()


def _cartesian_point(f, xyz: Tuple[float, float, float]):
    return f.create_entity(
        "IfcCartesianPoint",
        Coordinates=tuple(float(x) for x in xyz)
    )


def _direction(f, xyz: Tuple[float, float, float]):
    return f.create_entity(
        "IfcDirection",
        DirectionRatios=tuple(float(x) for x in xyz)
    )


def _axis2placement3d(
    f,
    location: Tuple[float, float, float],
    axis: Tuple[float, float, float] = (0.0, 0.0, 1.0),
    ref_direction: Tuple[float, float, float] = (1.0, 0.0, 0.0),
):
    return f.create_entity(
        "IfcAxis2Placement3D",
        Location=_cartesian_point(f, location),
        Axis=_direction(f, axis),
        RefDirection=_direction(f, ref_direction),
    )


def _local_placement(
    f,
    location=(0.0, 0.0, 0.0),
    relative_to=None,
    axis=(0.0, 0.0, 1.0),
    ref_direction=(1.0, 0.0, 0.0),
):
    return f.create_entity(
        "IfcLocalPlacement",
        PlacementRelTo=relative_to,
        RelativePlacement=_axis2placement3d(f, location, axis, ref_direction),
    )


def _owner_history(f):
    person = f.create_entity("IfcPerson", FamilyName="IPS Validator")
    org = f.create_entity("IfcOrganization", Name="Monash Tunnel Digital Twin Demo")
    person_org = f.create_entity(
        "IfcPersonAndOrganization",
        ThePerson=person,
        TheOrganization=org,
    )

    app_org = f.create_entity("IfcOrganization", Name="IfcOpenShell Demo")
    app = f.create_entity(
        "IfcApplication",
        ApplicationDeveloper=app_org,
        Version="2.0",
        ApplicationFullName="Snowy 2.0 IPS geometric IFC generator",
        ApplicationIdentifier="IPS-GEN",
    )

    return f.create_entity(
        "IfcOwnerHistory",
        OwningUser=person_org,
        OwningApplication=app,
        ChangeAction="ADDED",
        CreationDate=int(datetime.now(timezone.utc).timestamp()),
    )


def _unit_assignment(f):
    length = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    area = f.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE")
    volume = f.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE")
    angle = f.create_entity("IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN")

    return f.create_entity(
        "IfcUnitAssignment",
        Units=[length, area, volume, angle],
    )


# ---------------------------------------------------------------------
# Styling helpers
# ---------------------------------------------------------------------

def _make_colour_style(f, name: str, rgb: Tuple[float, float, float], transparency: float = 0.0):
    colour = f.create_entity(
        "IfcColourRgb",
        Name=name,
        Red=float(rgb[0]),
        Green=float(rgb[1]),
        Blue=float(rgb[2]),
    )

    shading = f.create_entity(
        "IfcSurfaceStyleShading",
        SurfaceColour=colour,
        Transparency=float(transparency),
    )

    style = f.create_entity(
        "IfcSurfaceStyle",
        Name=name,
        Side="BOTH",
        Styles=[shading],
    )

    return style


def _apply_style(f, solid, style, name: str):
    f.create_entity(
        "IfcStyledItem",
        Item=solid,
        Styles=[style],
        Name=name,
    )


# ---------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------

def _make_hollow_segment_representation(
    f,
    context,
    length: float,
    outer_radius: float,
    lining_thickness: float,
    style=None,
    style_name: str = "segment_style",
):
    """Create a hollow circular tunnel segment extruded along local Z."""
    inner_radius = max(outer_radius - lining_thickness, 0.1)

    profile = f.create_entity(
        "IfcCircleHollowProfileDef",
        ProfileType="AREA",
        ProfileName="Circular tunnel lining profile",
        Position=f.create_entity(
            "IfcAxis2Placement2D",
            Location=f.create_entity(
                "IfcCartesianPoint",
                Coordinates=(0.0, 0.0),
            ),
        ),
        Radius=float(outer_radius),
        WallThickness=float(outer_radius - inner_radius),
    )

    solid = f.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=profile,
        Position=_axis2placement3d(f, (0.0, 0.0, 0.0)),
        ExtrudedDirection=_direction(f, (0.0, 0.0, 1.0)),
        Depth=float(length),
    )

    if style is not None:
        _apply_style(f, solid, style, style_name)

    shape = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=context,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )

    return f.create_entity(
        "IfcProductDefinitionShape",
        Representations=[shape],
    )


def _make_joint_representation(
    f,
    context,
    radius=5.5,
    height=2.0,
    style=None,
    style_name: str = "joint_style",
):
    """Create a visible joint marker as a short solid cylinder."""
    profile = f.create_entity(
        "IfcCircleProfileDef",
        ProfileType="AREA",
        ProfileName="Joint marker profile",
        Position=f.create_entity(
            "IfcAxis2Placement2D",
            Location=f.create_entity(
                "IfcCartesianPoint",
                Coordinates=(0.0, 0.0),
            ),
        ),
        Radius=float(radius),
    )

    solid = f.create_entity(
        "IfcExtrudedAreaSolid",
        SweptArea=profile,
        Position=_axis2placement3d(f, (0.0, 0.0, -height / 2.0)),
        ExtrudedDirection=_direction(f, (0.0, 0.0, 1.0)),
        Depth=float(height),
    )

    if style is not None:
        _apply_style(f, solid, style, style_name)

    shape = f.create_entity(
        "IfcShapeRepresentation",
        ContextOfItems=context,
        RepresentationIdentifier="Body",
        RepresentationType="SweptSolid",
        Items=[solid],
    )

    return f.create_entity(
        "IfcProductDefinitionShape",
        Representations=[shape],
    )


# ---------------------------------------------------------------------
# Property-set helpers
# ---------------------------------------------------------------------

def _ifc_value(f, value: Any):
    if isinstance(value, bool):
        return f.create_entity("IfcBoolean", wrappedValue=value)
    if isinstance(value, int):
        return f.create_entity("IfcInteger", wrappedValue=value)
    if isinstance(value, float):
        return f.create_entity("IfcReal", wrappedValue=value)
    return f.create_entity("IfcText", wrappedValue=str(value))


def _attach_pset(f, owner_history, product, name: str, props: Dict[str, Any]):
    ifc_props = []

    for key, val in props.items():
        ifc_props.append(
            f.create_entity(
                "IfcPropertySingleValue",
                Name=key,
                NominalValue=_ifc_value(f, val),
                Unit=None,
            )
        )

    pset = f.create_entity(
        "IfcPropertySet",
        GlobalId=_guid(),
        OwnerHistory=owner_history,
        Name=name,
        HasProperties=ifc_props,
    )

    f.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=_guid(),
        OwnerHistory=owner_history,
        RelatedObjects=[product],
        RelatingPropertyDefinition=pset,
    )

    return pset


def _segment_psets(index: int, start: float, end: float, inject_errors: bool = False) -> Dict[str, Dict[str, Any]]:
    q = 337.0
    head_loss = 7.1
    rho = 999.7
    g = 9.81
    pressure_start = 4.92e6 - index * head_loss * rho * g
    stage = "OPERATION"

    hydraulic = {
        "SegmentChainageStart": start,
        "SegmentChainageEnd": end,
        "InternalDiameter": 6.5,
        "InternalDiameter_Source": "DESIGN",
        "TunnelLiningThickness": 0.35,
        "DesignDischarge_Generating": q,
        "DesignDischarge_Pumping": q,
        "DesignDischarge_Source": "DESIGN",
        "MeanFlowVelocity_Generating": 10.16,
        "ManningRoughnessCoefficient": 0.012,
        "Manning_Source": "DESIGN",
        "HeadLoss_Segment": head_loss,
        "OperatingPressure_Mean": pressure_start,
        "WaterDensity": rho,
    }

    composite = {
        "RockMass_GSI": [58.0, 62.0, 60.0][index],
        "Rock_Source": "FIELD_MONITORING",
        "RockMass_DeformationModulus": 1.5e10,
        "EinsteinSchwartz_LoadShareConcrete": [0.32, 0.30, 0.31][index],
        "EinsteinSchwartz_LoadShareSteelLiner": [0.30, 0.28, 0.29][index],
        "EinsteinSchwartz_LoadShareRockMass": [0.38, 0.42, 0.40][index],
        "ES_Source": "FLAC3D",
    }

    fatigue = {
        "MinerDamageRatio_Cumulative": [0.31, 0.27, 0.29][index],
        "Miner_Source": "SURROGATE_DEEPONET",
        "MinerDamageThreshold_Action": 0.5,
        "MinerDamageThreshold_Critical": 1.0,
        "RemainingFatigueLife_Years": [48.0, 52.0, 45.0][index],
    }

    leakage = {
        "CrackCount_Detected": [4, 2, 5][index],
        "CrackAperture_Mean": [0.15, 0.09, 0.21][index],
        "CubicLaw_LeakageRate": [0.002, 0.001, 0.003][index],
    }

    surrogate = {
        "PredictionTimestamp": "2026-04-25T10:00:00+00:00",
        "PredictedDamageRatio_Mean": [0.31, 0.27, 0.29][index],
        "PredictedDamageRatio_StdDev": 0.04,
    }

    meta = {
        "AssessmentCycleID": "ASSESS-2026-Q2-001",
        "AssessmentTimestamp": "2026-05-08T14:00:00+00:00",
        "VerificationStatus": "PASSED",
        "LifecycleStage": stage,
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


# ---------------------------------------------------------------------
# Main IFC builder
# ---------------------------------------------------------------------

def build_geometric_ifc(
    output_path: str | Path = "snowy2_geometric_clean.ifc",
    inject_errors: bool = False,
) -> Dict[str, Any]:

    f = ifcopenshell.file(schema="IFC4X3")
    owner = _owner_history(f)

    project = f.create_entity(
        "IfcProject",
        GlobalId=_guid(),
        OwnerHistory=owner,
        Name="Snowy 2.0 IPS Geometric Validator Demo",
        UnitsInContext=_unit_assignment(f),
    )

    context = f.create_entity(
        "IfcGeometricRepresentationContext",
        ContextIdentifier="Model",
        ContextType="Model",
        CoordinateSpaceDimension=3,
        Precision=1.0e-5,
        WorldCoordinateSystem=_axis2placement3d(f, (0.0, 0.0, 0.0)),
    )

    project.RepresentationContexts = [context]

    site = f.create_entity(
        "IfcSite",
        GlobalId=_guid(),
        OwnerHistory=owner,
        Name="Synthetic Snowy 2.0 IPS Site",
        ObjectPlacement=_local_placement(f),
    )

    f.create_entity(
        "IfcRelAggregates",
        GlobalId=_guid(),
        OwnerHistory=owner,
        RelatingObject=project,
        RelatedObjects=[site],
    )

    # Colours
    green_style = _make_colour_style(f, "PASS_GREEN", (0.10, 0.65, 0.20), transparency=0.0)
    orange_style = _make_colour_style(f, "WARN_ORANGE", (1.00, 0.55, 0.05), transparency=0.0)
    red_style = _make_colour_style(f, "ERROR_RED", (0.95, 0.10, 0.10), transparency=0.0)
    blue_style = _make_colour_style(f, "JOINT_BLUE", (0.10, 0.30, 0.90), transparency=0.0)

    products = []
    segment_guids = []

    segment_ids = ["seg_1+250", "seg_1+750", "seg_2+250"]
    real_starts = [1000.0, 1500.0, 2000.0]
    real_ends = [1500.0, 2000.0, 2500.0]

    # Display geometry settings.
    # These are deliberately exaggerated for easy viewing in KIT Model Viewer.
    display_length = 120.0
    display_gap = 35.0
    display_outer_radius = 10.0
    display_lining_thickness = 1.2

    for i, (seg_id, start, end) in enumerate(zip(segment_ids, real_starts, real_ends)):
        display_x = i * (display_length + display_gap)

        if not inject_errors:
            seg_style = green_style
            validation_status = "PASS"
            validation_note = "Clean demonstration segment"
        else:
            if i == 0:
                seg_style = red_style
                validation_status = "ERROR"
                validation_note = "R2, R3, R5, R6 injected on this segment"
            elif i == 1:
                seg_style = orange_style
                validation_status = "WARN"
                validation_note = "R1 discharge mismatch affects adjacent segment pair"
            else:
                seg_style = red_style
                validation_status = "ERROR"
                validation_note = "R4 and R7 injected on this segment"

        placement = _local_placement(
            f,
            location=(display_x, 0.0, 0.0),
            relative_to=site.ObjectPlacement,
            axis=(1.0, 0.0, 0.0),
            ref_direction=(0.0, 1.0, 0.0),
        )

        segment = f.create_entity(
            "IfcBuildingElementProxy",
            GlobalId=_guid(),
            OwnerHistory=owner,
            Name=f"Tunnel Segment {seg_id}",
            ObjectType="IPS_TUNNEL_SEGMENT",
            ObjectPlacement=placement,
            Representation=_make_hollow_segment_representation(
                f,
                context,
                length=display_length,
                outer_radius=display_outer_radius,
                lining_thickness=display_lining_thickness,
                style=seg_style,
                style_name=f"{seg_id}_{validation_status}",
            ),
        )

        segment_guids.append(segment.GlobalId)
        products.append(segment)

        _attach_pset(
            f,
            owner,
            segment,
            "Pset_IPSIdentity",
            {
                "IPS_GUID": seg_id,
                "SegmentIndex": i + 1,
                "DisplayStartX": display_x,
                "DisplayEndX": display_x + display_length,
                "RealChainageStart": start,
                "RealChainageEnd": end,
                "VisualValidationStatus": validation_status,
                "VisualValidationNote": validation_note,
            },
        )

        for pset_name, props in _segment_psets(i, start, end, inject_errors).items():
            _attach_pset(f, owner, segment, pset_name, props)

    # Joint markers between the display segments
    joint_specs = [
        (
            "joint_1500",
            display_length + display_gap / 2.0,
            1500.0,
            segment_guids[0],
            "missing_segment_guid" if inject_errors else segment_guids[1],
        ),
        (
            "joint_2000",
            2 * display_length + 1.5 * display_gap,
            2000.0,
            segment_guids[1],
            segment_guids[2],
        ),
    ]

    joint_guids = []

    for joint_id, display_x, real_station, upstream, downstream in joint_specs:
        joint = f.create_entity(
            "IfcBuildingElementProxy",
            GlobalId=_guid(),
            OwnerHistory=owner,
            Name=f"FACS Joint {joint_id}",
            ObjectType="IPS_FACS_JOINT",
            ObjectPlacement=_local_placement(
                f,
                location=(display_x, 0.0, 0.0),
                relative_to=site.ObjectPlacement,
                axis=(1.0, 0.0, 0.0),
                ref_direction=(0.0, 1.0, 0.0),
            ),
            Representation=_make_joint_representation(
                f,
                context,
                radius=12.0,
                height=4.0,
                style=blue_style,
                style_name=joint_id,
            ),
        )

        joint_guids.append(joint.GlobalId)
        products.append(joint)

        _attach_pset(
            f,
            owner,
            joint,
            "Pset_FACSJoint_IPS",
            {
                "JointGUID_Upstream": upstream,
                "JointGUID_Downstream": downstream,
                "JointStationing": real_station,
                "JointLabel": joint_id,
                "DisplayX": display_x,
            },
        )

    f.create_entity(
        "IfcRelContainedInSpatialStructure",
        GlobalId=_guid(),
        OwnerHistory=owner,
        RelatedElements=products,
        RelatingStructure=site,
    )

    output_path = Path(output_path)
    f.write(str(output_path))

    return {
        "output_path": output_path.as_posix(),
        "schema": f.schema,
        "segments": len(segment_guids),
        "joints": len(joint_guids),
        "inject_errors": inject_errors,
        "file_size_bytes": output_path.stat().st_size,
    }


if __name__ == "__main__":
    print(build_geometric_ifc("snowy2_geometric_clean.ifc", inject_errors=False))
    print(build_geometric_ifc("snowy2_geometric_faulty.ifc", inject_errors=True))