from aegis.quantity import normalize, parse_quantities, qeq
from aegis.models import Claim
from aegis.scope import Scope, ver_ge

def test_quantity_normalization():
    v, u = normalize(0.18, "MPa"); assert u == "bar" and abs(v - 1.8) < 1e-6          # 0.18 MPa = 1.8 bar (check the arithmetic: 1 MPa = 10 bar)
    v, u = normalize(18, "MPa"); assert u == "bar" and abs(v - 180) < 1e-6              # 18 MPa == 180 bar
    assert qeq(normalize(18, "MPa"), (180.0, "bar"))
    assert parse_quantities("reaches 200 bar. 10 seconds, 210°C") == [(200.0, "bar"), (10.0, "second"), (210.0, "degC")]

def test_claim_value_identity():
    mk = lambda v: Claim(id="c", entity_id="component:HPU", predicate="normal_operating_pressure", value=v, scope=Scope(software_revision=ver_ge("3.2")), evidence_ids=["e"], doc_id="d", tier="primary")
    assert mk({"type": "quantity", "value": 200.0, "unit": "bar", "raw": 200}).vkey() == mk({"type": "quantity", "value": 200.0, "unit": "bar"}).vkey()
