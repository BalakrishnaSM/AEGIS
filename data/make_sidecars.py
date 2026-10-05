"""Generates data/transcriptions/*.json: the VLM STAND-IN for this environment (no API key available).
Each sidecar is authored by the assistant from VIEWING the image, is bound to the image by SHA-256, is marked
reviewed=false, and is cross-checked against Tesseract at ingest (raster_agreement). Replace with real VLM output when available."""
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1]); out = Path(__file__).parent / "transcriptions"; out.mkdir(exist_ok=True)
READER = "assistant-transcription (unreviewed; VLM stand-in)"
def sha(rel): return hashlib.sha256((root / rel).read_bytes()).hexdigest()
def q(v, u): return {"type": "quantity", "value": float(v), "unit": u}
def txt(v): return {"type": "text", "v": v}
def write(rel, regions, claims):
    d = {"source": rel, "sha256": sha(rel), "reader": READER, "reviewed": False,
         "regions": [{"id": k, "text": v} for k, v in regions.items()], "claims": claims}
    (out / (Path(rel).stem + ".json")).write_text(json.dumps(d, indent=1, ensure_ascii=False))

write("scans/scanned_appendix_calibration.pdf", {
  "r1": "Sensor Tag: PS-04A Location: HPU discharge line Calibration Date: 2026-01-09 Technician: R. Okafor",
  "r2": "Zero (0%) 0 bar 0.1 bar PASS Mid (50%) 100 bar 99.6 bar PASS Span (100%) 200 bar 199.4 bar PASS",
  "r3": "Note: this calibration record supersedes the field data sheet used prior to the PS-04 to PS-04A sensor change (see ECN-1042). Span reference target is 200 bar, consistent with the software revision 3.2 operating pressure setpoint.",
  "r4": "Prior sensor (PS-04) span reference target was 180 bar — for historical comparison only. Do not use the 180 bar figure to evaluate PS-04A.",
  "r5": "No calibration interval is specified for PS-04A on this form; recalibration frequency should be confirmed against the applicable maintenance schedule, which is not included in this binder."},
 [dict(region="r1", quote="Calibration Date: 2026-01-09", entity="component:PS-04A", predicate="last_calibrated", value={"type": "date", "v": "2026-01-09"}, critical_tokens=["2026-01-09"], instance=True),
  dict(region="r1", quote="Technician: R. Okafor", entity="component:PS-04A", predicate="performed_by", value={"type": "person", "v": "R. Okafor"}, critical_tokens=["Okafor"], instance=True),
  dict(region="r3", quote="Span reference target is 200 bar", entity="component:PS-04A", predicate="calibration_reference", value=q(200, "bar"), critical_tokens=["200 bar"], rev="ge:3.2"),
  dict(region="r4", quote="Prior sensor (PS-04) span reference target was 180 bar", entity="component:PS-04", predicate="calibration_reference", value=q(180, "bar"), critical_tokens=["PS-04", "180 bar"], rev="lt:3.2"),
  dict(region="r5", quote="No calibration interval is specified for PS-04A on this form", entity="component:PS-04A", predicate="calibration_interval", value={"type": "not_specified"}, critical_tokens=["PS-04A"])])

write("screenshots/screen_03_diagnostics.png", {
  "r1": "AEGIS SERIES-7 HCS — DIAGNOSTICS SW REV 3.2",
  "r2": "SENSOR DIAGNOSTICS Tag P.S.04-A Signal Type 4-20 mA Raw Reading 14.8 mA Scaled Value 200.3 bar Calibration Status WITHIN TOLERANCE Last Calibrated 2026-01-09 Firmware Rev. 3.2.1",
  "r3": "Note: this screen displays the sensor tag as printed on the physical unit label."},
 [dict(region="r2", quote="Tag P.S.04-A", entity="screen:diagnostics", entity_name="Diagnostics screen", predicate="displays_tag", value=txt("P.S.04-A"), critical_tokens=["P.S.04-A"], rev="eq:3.2", basis="instance_observation"),
  dict(region="r2", quote="Signal Type 4-20 mA", entity="component:PS-04A", predicate="signal_type", value=txt("4-20 mA"), critical_tokens=["4-20"], rev="eq:3.2", basis="instance_observation"),
  dict(region="r2", quote="Last Calibrated 2026-01-09", entity="component:PS-04A", predicate="last_calibrated", value={"type": "date", "v": "2026-01-09"}, critical_tokens=["2026-01-09"], rev="eq:3.2", basis="instance_observation")])

write("screenshots/screen_01_home.png", {
  "r1": "AEGIS SERIES-7 HCS — HOME SW REV 3.2 HPU DISCHARGE PRESSURE 200 bar STATUS: RUNNING — NORMAL",
  "r2": "STARTUP INTERLOCKS IV-21 (Isolation Valve) OPEN Emergency Stop RESET Maintenance Panel CLOSED Hydraulic Fluid Level NORMAL"},
 [dict(region="r1", quote="HPU DISCHARGE PRESSURE 200 bar", entity="component:HPU", predicate="observed_pressure", value=q(200, "bar"), critical_tokens=["200", "bar"], rev="eq:3.2", basis="instance_observation")])

write("screenshots/screen_02_alarms.png", {
  "r1": "AEGIS SERIES-7 HCS — ALARM LIST SW REV 3.2 A17 Hydraulic Pressure Low CLEARED A19 Pressure Sensor Signal Invalid CLEARED A17 Hydraulic Pressure Low ACTIVE A18 Hydraulic Pressure High INACTIVE",
  "r2": "Alarm A17 active for 00:00:14 — Shutdown Procedure 4.7 threshold: 00:00:10"}, [])

write("diagrams/system_diagram_electrical.png", {
  "r1": "Aegis Series-7 HCS — Electrical Schematic (AEG-DWG-E02) Power distribution and interlock wiring",
  "r2": "MAIN DISCONNECT Q1 CONTROL XFMR T1 480:120V PLC-03 24VDC POWER SUPPLY PLC-03 I/O RACK E-STOP SAFETY RELAY KA1",
  "r3": "SENSOR LOOP POWER 24VDC feeds pressure transducer loop (see wiring diagram AEG-DWG-W03) IV-21 VALVE DRIVER TB-7 REF: PRESSURE XDCR LOOP"},
 [dict(region="r2", quote="T1 480:120V", entity="component:T1", predicate="supply_voltage", value=txt("480:120 V"), critical_tokens=["480:120V"])])
print("wrote", sorted(p.name for p in out.glob("*.json")))
