#!/usr/bin/env python3
"""Add datasheet URLs to resistors in the database."""
import json
import sys
import os
from collections import defaultdict

# Database path
DB_PATH = "/home/alf/OpenConverters/TAS/data/resistors.ndjson"

# Manufacturer URL patterns
URL_PATTERNS = {
    "Vishay": {
        "pattern": "https://www.vishay.com/docs/{id}/{mpn}.pdf",
        "series_ids": {
            "CRCW": "20024",
            "TNPW": "28776",
            "WSK": "30174",  # WSK series
        }
    },
    "Yageo": {
        "pattern": "https://www.yageo.com/upload/media/product/productdatasheet/{mpn}.pdf"
    },
    "Panasonic": {
        "pattern": "https://industrial.panasonic.com/cdbs/www-data/pdf/{series}.pdf",
        "series_map": {
            "ERJ-M1WS": "ABC0000",
            "ERA-3AEB": "ABC0000",
        }
    },
    "Bourns": {
        "pattern": "https://www.bourns.com/docs/{series}.pdf"
    },
    "ROHM": {
        "pattern": "https://fscdn.rohm.com/en/products/databook/datasheet/{mpn}.pdf"
    },
    "Wurth": {
        "pattern": "https://www.we-online.com/catalog/datasheet/{mpn}.pdf"
    },
    "TE": {
        "pattern": "https://www.te.com/commerce/DocumentDelivery/DDEController?Action=srchrtrv&DocNm={mpn}&DocType=Data+Sheet&DocLang=English"
    },
    "Susumu": {
        "pattern": "https://www.susumu.co.jp/common/pdf/n_catalog/{series}.pdf"
    },
    "Ohmite": {
        "pattern": "https://www.ohmite.com/assets/docs/{mpn}.pdf"
    },
    "Ametherm": {
        "pattern": "https://www.ametherm.com/datasheet/{series}.pdf"
    },
    "TT": {
        "pattern": "https://www.ttelectronics.com/datasheets/{mpn}.pdf"
    },
}

def detect_manufacturer(part_number, series):
    """Detect manufacturer from part number prefix."""
    part_upper = part_number.upper()
    series_upper = series.upper()
    
    # Vishay
    if any(part_upper.startswith(prefix) for prefix in ['CRCW', 'TNPW', 'WSK', 'WSL', 'DALE']):
        return "Vishay"
    
    # Yageo
    if any(part_upper.startswith(prefix) for prefix in ['RC', 'RL', 'RT', 'AC']):
        return "Yageo"
    
    # Panasonic
    if any(part_upper.startswith(prefix) for prefix in ['ERJ', 'ERA']):
        return "Panasonic"
    
    # Bourns
    if any(part_upper.startswith(prefix) for prefix in ['CSS', 'CRL', 'PWR']):
        return "Bourns"
    
    # ROHM
    if any(part_upper.startswith(prefix) for prefix in ['ESR', 'MCR', 'SFR']):
        return "ROHM"
    
    # Wurth (starts with 5)
    if part_number.startswith('5') and len(part_number) >= 8:
        return "Wurth"
    
    # TE Connectivity
    if any(part_upper.startswith(prefix) for prefix in ['CRGP', 'RN', 'CPF']):
        return "TE"
    
    # Susumu
    if any(part_upper.startswith(prefix) for prefix in ['RG', 'RLG', 'LVK']):
        return "Susumu"
    
    # Ohmite
    if any(part_upper.startswith(prefix) for prefix in ['OX', 'OY', 'OXL']):
        return "Ohmite"
    
    # Ametherm
    if any(part_upper.startswith(prefix) for prefix in ['SL', 'MS']):
        return "Ametherm"
    
    # TT Electronics
    if any(part_upper.startswith(prefix) for prefix in ['LRMAP']):
        return "TT"
    
    # TDK
    if any(part_upper.startswith(prefix) for prefix in ['B573']):
        return "TDK"
    
    return None

def generate_url(part_number, series, manufacturer):
    """Generate datasheet URL for a resistor."""
    if manufacturer not in URL_PATTERNS:
        return None
    
    pattern = URL_PATTERNS[manufacturer]
    url_template = pattern["pattern"]
    
    if manufacturer == "Vishay":
        # Find series ID
        series_id = None
        for s, sid in pattern.get("series_ids", {}).items():
            if series.upper().startswith(s):
                series_id = sid
                break
        if not series_id:
            return None
        return url_template.format(id=series_id, mpn=part_number)
    
    elif manufacturer == "Panasonic":
        # Use series for Panasonic
        return url_template.format(series=series)
    
    elif manufacturer == "Bourns":
        return url_template.format(series=series)
    
    else:
        # Generic pattern using MPN - try both patterns
        try:
            return url_template.format(mpn=part_number)
        except KeyError:
            try:
                return url_template.format(series=series)
            except KeyError:
                return url_template

def main():
    # Read the backup file (original data is there)
    backup_path = DB_PATH + ".bak_task2"
    if not os.path.exists(backup_path):
        print(f"Error: Backup file not found: {backup_path}")
        sys.exit(1)
    
    # Load all resistors
    resistors = []
    with open(backup_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                resistors.append(json.loads(line))
    
    total = len(resistors)
    print(f"Loaded {total} resistors from backup")
    
    # Track statistics
    manufacturer_counts = defaultdict(int)
    urls_added = 0
    skipped = 0
    
    # Process each resistor
    for resistor in resistors:
        info = resistor.get("resistor", {}).get("manufacturerInfo", {})
        datasheet_info = info.get("datasheetInfo", {})
        part = datasheet_info.get("part", {})
        
        part_number = part.get("partNumber", "")
        series = part.get("series", "")
        
        # Detect manufacturer
        manufacturer = detect_manufacturer(part_number, series)
        
        if manufacturer:
            manufacturer_counts[manufacturer] += 1
            
            # Generate URL
            url = generate_url(part_number, series, manufacturer)
            
            if url:
                # Add URL to manufacturerInfo
                info["datasheetUrl"] = url
                urls_added += 1
            else:
                skipped += 1
        else:
            skipped += 1
    
    # Print statistics
    print(f"\nManufacturer breakdown:")
    for mfg, count in sorted(manufacturer_counts.items(), key=lambda x: -x[1]):
        print(f"  {mfg}: {count}")
    
    print(f"\nTotal: {total}")
    print(f"URLs added: {urls_added}")
    print(f"Skipped (no manufacturer match): {skipped}")
    print(f"Coverage: {urls_added/total*100:.1f}%")
    
    # Write to database
    with open(DB_PATH, 'w') as f:
        for resistor in resistors:
            f.write(json.dumps(resistor) + '\n')
    
    print(f"\nSaved {total} resistors to {DB_PATH}")
    return urls_added, total

if __name__ == "__main__":
    main()
