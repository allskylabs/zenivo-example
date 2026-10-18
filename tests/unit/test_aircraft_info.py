import json

from src.analytics import aircraft_info
from src.analytics.aircraft_info import AircraftInfo, parse_airlines

AIRLINES_DAT = "\n".join([
    '3094,"Kalitta Air",\\N,"K4","CKS","CONNIE","United States","N"',
    '5926,"Old Name",\\N,"XX","ABC","OLD","Nowhere","N"',
    '5927,"New Name",\\N,"AB","ABC","NEW","Nowhere","Y"',
    '18118,"VietJet Air","VietJet","","VJC","VIETJETAIR","Vietnam","Y"',
])


def test_parse_airlines_prefers_active_and_skips_missing_iata():
    al = parse_airlines(AIRLINES_DAT)
    assert al["CKS"] == ("K4", "Kalitta Air")
    assert al["ABC"] == ("AB", "New Name")
    assert "VJC" not in al  # no IATA code in OpenFlights


def test_describe_uses_cache_and_queues_unknowns(tmp_path, monkeypatch):
    (tmp_path / "aircraft_db.json").write_text(json.dumps({
        "hex": {"888151": {"type": "A321", "reg": "VN-A639", "model": "Airbus A321 211SL", "ts": 1}},
        "route": {"VJC123": {"route": "VVNB-VVTS", "ts": 4102444800}},
    }))
    info = AircraftInfo(str(tmp_path))
    queued = []
    monkeypatch.setattr(info, "_enqueue", lambda kind, key: queued.append((kind, key)))
    info._airlines = parse_airlines(AIRLINES_DAT)

    d = info.describe("888151", "VJC123")
    assert d == {"type": "A321", "reg": "VN-A639", "model": "Airbus A321 211SL", "airline_iata": "VJ",
                 "airline": "VietJet Air", "route": "VVNB-VVTS"}  # VietJet from the override table
    assert queued == []

    d = info.describe("AA6CFA", "CKS524")
    assert d == {"airline_iata": "K4", "airline": "Kalitta Air"}
    assert queued == [("hex", "AA6CFA"), ("route", "CKS524")]

    queued.clear()
    assert info.describe("7801C9", "B6789") == {}  # registration-style callsign: no airline, no route lookup
    assert queued == [("hex", "7801C9")]


def test_lookup_hex_stores_type(tmp_path, monkeypatch):
    info = AircraftInfo(str(tmp_path))
    monkeypatch.setattr(aircraft_info, "_get", lambda url, timeout=8.0: json.dumps(
        {"ICAOTypeCode": "b77w", "Registration": "N771CK", "Manufacturer": "Boeing", "Type": "777 36NER"}).encode())
    info._lookup_hex("AA6CFA")
    info._save()
    saved = json.loads((tmp_path / "aircraft_db.json").read_text())
    assert saved["hex"]["AA6CFA"]["type"] == "B77W"
    assert saved["hex"]["AA6CFA"]["model"] == "Boeing 777 36NER"
