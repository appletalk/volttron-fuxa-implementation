#!/usr/bin/env python
"""Assemble a KMZ: place a COLLADA .dae at lat/lon with a KML <Model>.
   Usage: make_kmz.py model.dae out.kmz LAT LON [HEADING] [ALT] [NAME]
   altitudeMode=clampToGround, so the model origin (Z=0) sits on the terrain.
"""
import sys
import os
import zipfile
import xml.sax.saxutils as sx


def main():
    dae, kmz = sys.argv[1], sys.argv[2]
    lat, lon = float(sys.argv[3]), float(sys.argv[4])
    heading = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
    alt = float(sys.argv[6]) if len(sys.argv) > 6 else 0.0
    name = sys.argv[7] if len(sys.argv) > 7 else "Sunfield Solar"
    daename = os.path.basename(dae)
    kml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2">\n'
        '  <Placemark>\n'
        '    <name>%s</name>\n'
        '    <Model id="model_1">\n'
        '      <altitudeMode>clampToGround</altitudeMode>\n'
        '      <Location><longitude>%.8f</longitude><latitude>%.8f</latitude>'
        '<altitude>%.3f</altitude></Location>\n'
        '      <Orientation><heading>%.4f</heading><tilt>0</tilt><roll>0</roll></Orientation>\n'
        '      <Scale><x>1</x><y>1</y><z>1</z></Scale>\n'
        '      <Link><href>models/%s</href></Link>\n'
        '    </Model>\n'
        '  </Placemark>\n'
        '</kml>\n'
    ) % (sx.escape(name), lon, lat, alt, heading, daename)

    with zipfile.ZipFile(kmz, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml)
        z.write(dae, "models/%s" % daename)
    print("wrote %s: model at %.6f,%.6f heading %.1f" % (kmz, lat, lon, heading))


if __name__ == "__main__":
    main()
