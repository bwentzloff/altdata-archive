#!/usr/bin/env python3
"""
Quick script to clean up corrupted coach names extracted from Wikipedia.

The scraper captured entire Wikipedia paragraphs as coach names. This script
extracts just the actual coach names from the corrupted data.

The pattern is that Wikipedia lists coaches like "Name role: Name Role" or
biographical text that starts with the coach's name. We extract the first
capitalized words as the name.
"""

import json
import re
from pathlib import Path

RAW = Path(__file__).parent / "raw"
MERGED = Path(__file__).parent / "merged"

def extract_coach_name(corrupted_text: str) -> str:
    """
    Extract actual coach name from corrupted Wikipedia text.
    
    Patterns:
    - "June Jones" → "June Jones" (already good)
    - "Hamilton went 3–2..." → "Hamilton" (first capitalized word)
    - "Gilbride went..." → "Gilbride" (first capitalized word)
    - "son of famedHouston..." → extract from context
    """
    # If it looks already clean (no lowercase after uppercase), keep it
    if not re.search(r'[A-Z][a-z]+\s+[a-z]', corrupted_text):
        # Looks like just a name, keep as-is
        return corrupted_text.strip()
    
    # Strategy 1: Look for "Name Role:" or "Name—Role" or "Name –" pattern
    # (coach name at start, followed by role or colon)
    match = re.match(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*(?:went|was|had|is|'
                    r'joined|left|remain|who|but|and|the)', corrupted_text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    
    # Strategy 2: Take first two capitalized words
    match = re.match(r'^([A-Z][a-z]+)\s+([A-Z][a-z]+)', corrupted_text)
    if match:
        return f"{match.group(1)} {match.group(2)}".strip()
    
    # Strategy 3: Take first capitalized word
    match = re.match(r'^([A-Z][a-z]+)', corrupted_text)
    if match:
        return match.group(1).strip()
    
    # Fallback: return first 20 chars
    return corrupted_text[:20].strip()


def main():
    print("Cleaning up corrupted coach names...")
    
    # Load raw coaches
    coaches_file = RAW / "football_coaches.json"
    if not coaches_file.exists():
        print("No coaches file found")
        return
    
    coaches = json.loads(coaches_file.read_text())
    print(f"Loaded {len(coaches)} raw coaches")
    
    # Clean up full_name fields
    fixed_count = 0
    for coach in coaches:
        original = coach.get("full_name", "")
        cleaned = extract_coach_name(original)
        if cleaned != original:
            coach["full_name"] = cleaned
            fixed_count += 1
    
    print(f"Fixed {fixed_count} coach names")
    
    # Write back
    coaches_file.write_text(json.dumps(coaches, indent=2), encoding="utf-8")
    print(f"Wrote cleaned coaches back to {coaches_file}")
    
    # Now rebuild merged coaches
    print("\nRebuilding merged coaches...")
    from merge_players import main as merge_main
    merge_main()


if __name__ == "__main__":
    main()
