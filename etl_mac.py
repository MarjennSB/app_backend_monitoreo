import json
import os

input_file = r"C:\xampp\htdocs\SCANN_v2\MvpMonitoreo\mac-vendors-export.json"
output_file = r"C:\xampp\htdocs\SCANN_v2\MvpMonitoreo\modules\discovery\mac_vendors.json"

print(f"Leyendo {input_file}...")
with open(input_file, "r", encoding="utf-8") as f:
    data = json.load(f)

print(f"Limpiando {len(data)} registros...")
clean_data = {}
for item in data:
    mac = item.get("macPrefix")
    vendor = item.get("vendorName")
    if mac and vendor:
        # Aseguramos el formato XX:XX:XX en mayúsculas
        clean_mac = mac.upper().replace("-", ":")
        clean_data[clean_mac] = vendor

print(f"Guardando {len(clean_data)} registros limpios en {output_file}...")
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(clean_data, f, indent=2)

print("¡Listo! Optimización terminada.")
