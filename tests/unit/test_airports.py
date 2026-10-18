from src.analytics.airports import build

HEAD_A = '"id","ident","type","name","latitude_deg","longitude_deg","elevation_ft"\n'
HEAD_R = ('"id","airport_ref","airport_ident","length_ft","width_ft","surface","lighted","closed","le_ident",'
          '"le_latitude_deg","le_longitude_deg","le_elevation_ft","le_heading_degT","le_displaced_threshold_ft",'
          '"he_ident","he_latitude_deg","he_longitude_deg"\n')


def test_build_keeps_nearby_airports_and_runways_with_both_thresholds():
    airports = HEAD_A + "\n".join([
        '1,"VVNB","large_airport","Noi Bai International Airport",21.2212,105.807,39',
        '2,"VVGL","medium_airport","Gia Lam Airport",21.0405,105.8865,',
        '3,"FARM","small_airport","Some Strip",21.1,105.9,10',              # small, no runway: dropped
        '4,"HELI","heliport","Pad",21.0,105.9,10',                         # wrong type
        '5,"FAR1","large_airport","Far Away",10.8,106.6,10',               # > 450 km
    ])
    runways = HEAD_R + "\n".join([
        '9,1,"VVNB",10499,148,"CON",1,0,"11L",21.2255,105.792,40,107,,"29R",21.216499,105.822998',
        '10,2,"VVGL",6571,100,"ASP",0,0,"02",,,,,,"20",21.0487,105.89',     # one threshold missing
        '11,1,"VVNB",9000,148,"CON",1,1,"11R",21.22,105.78,40,107,,"29L",21.21,105.82',  # closed
    ])
    d = build(airports, runways, 21.0538, 105.9095)
    assert sorted(a["ident"] for a in d["airports"]) == ["VVGL", "VVNB"]
    assert len(d["runways"]) == 1
    rw = d["runways"][0]
    assert rw["ids"] == "11L/29R" and rw["lighted"] and abs(rw["width_m"] - 45.1) < 0.1
