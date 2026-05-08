# Snowy 2.0 IPS IFC Validator + BIM Segment View

This package upgrades the previous Streamlit validator into a simple BIM-style workflow:

1. Generate simplified geometric tunnel IFC files using Python + IfcOpenShell.
2. Open/check the generated IFC files in BlenderBIM or another IFC viewer.
3. Upload the IFC files to Streamlit.
4. Validate IPS rules R1-R8.
5. Visualise tunnel segments as a BIM-style 3D model coloured by validation status.

## Files to add to your GitHub repository

Copy these files into the repository root:

- `streamlit_app.py`
- `generate_geometric_ifc.py`
- `requirements.txt`

Keep your existing files:

- `ips_validator.py`
- `generate_synthetic_ifc.py` if you still want the previous synthetic property-only generator
- existing `.ifc` files if desired

## Local setup

```bash
pip install -r requirements.txt
```

Generate the geometric IFC files:

```bash
python generate_geometric_ifc.py
```

This creates:

- `snowy2_geometric_clean.ifc`
- `snowy2_geometric_faulty.ifc`

Open these files in BlenderBIM/Solibri/BIMcollab/Blender with Bonsai/BlenderBIM to inspect the geometry and property sets.

## Run Streamlit locally

```bash
streamlit run streamlit_app.py
```

Upload either:

- `snowy2_geometric_clean.ifc` — should pass the rules
- `snowy2_geometric_faulty.ifc` — should trigger simulated R1-R8 issues

## Deploy through GitHub + Streamlit Cloud

1. Push the files to GitHub.
2. Go to Streamlit Community Cloud.
3. Select your repository.
4. Set the main file path to:

```text
streamlit_app.py
```

5. Deploy.

## Notes

- The generated IFC files are simplified research/demo BIM models, not contractor-issued design models.
- A real designed IFC should be exported from Revit, Bentley OpenTunnel/OpenRoads, Civil 3D/InfraWorks, Tekla, or another BIM platform.
- The faulty IFC intentionally contains injected issues to test the validator. It is not a failed real design model.
- The Streamlit BIM view uses the IFC segment property sets to reconstruct a lightweight 3D tunnel view. This is faster and more reliable in a web app than rendering full IFC triangulated geometry.
