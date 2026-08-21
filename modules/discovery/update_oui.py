import urllib.request
import json
import os

URL = "http://standards-oui.ieee.org/oui/oui.txt"
# Ruta absoluta para asegurar que se guarde en la misma carpeta que el scanner
OUTPUT = os.path.join(os.path.dirname(__file__), "mac_vendors.json")

print("Descargando base de datos OUI de IEEE (puede tomar unos segundos)...")
try:
    response = urllib.request.urlopen(URL, timeout=10)
    text = response.read().decode('utf-8')
    
    vendors = {}
    for line in text.splitlines():
        if "(hex)" in line:
            parts = line.split("(hex)")
            mac_prefix = parts[0].strip().replace("-", ":").upper()
            vendor_name = parts[1].strip()
            vendors[mac_prefix] = vendor_name
            
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(vendors, f, indent=2)
        
    print(f"Descargados y procesados {len(vendors)} fabricantes con éxito en {OUTPUT}")
except Exception as e:
    print(f"Error descargando: {e}")
