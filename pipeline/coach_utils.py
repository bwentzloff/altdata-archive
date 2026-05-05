#!/usr/bin/env python3
"""
coach_utils.py — Utilities for extracting coaching staff from various sources.

Provides helper functions to extract coaches from:
  - Wikipedia season articles (team rosters/coaching staffs)
  - Official league websites
  - JSON/API sources

Coaching staff format:
  {
    "name": str,
    "role": str (e.g. "Head Coach", "Offensive Coordinator", "Defensive Coordinator"),
    "team": str (team abbreviation),
    "league": str (league name),
    "_year": int (season year),
    "_source": str (source URL or type),
  }
"""

import re
from typing import Optional
from bs4 import BeautifulSoup


def extract_coaches_from_wikipedia_roster(
    html: str,
    year: int,
    league: str,
    team_abbr: str = None,
) -> list[dict]:
    """
    Extract coaching staff from a Wikipedia season article roster section.
    
    Looks for tables containing coaching staff information, typically with
    rows like "Head Coach: Name" or tables with columns for role and name.
    
    Returns list of coach dicts with: name, role, team, league, _year, _source
    """
    coaches = []
    soup = BeautifulSoup(html, "html.parser")
    
    # Strategy 1: Look for text like "Head Coach: Name" or similar
    # Find paragraphs or list items that mention coaching roles
    for elem in soup.find_all(["p", "li", "td", "dd"]):
        text = elem.get_text(strip=True)
        
        # Match patterns like "Head Coach: John Smith" or "Head Coach – John Smith"
        # Also matches "Offensive Coordinator" etc.
        coach_pattern = r"(Head Coach|Offensive Coordinator|Defensive Coordinator|Special Teams Coach|Quarterbacks Coach|Running Backs Coach|Wide Receivers Coach|Offensive Line Coach|Defensive Line Coach|Linebackers Coach|Defensive Backs Coach|Coach)[:\s–\-]?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)"
        
        matches = re.finditer(coach_pattern, text, re.IGNORECASE)
        for match in matches:
            role = match.group(1).strip()
            name = match.group(2).strip()
            
            if name and len(name) > 2:  # Avoid very short matches
                coaches.append({
                    "name": name,
                    "role": role,
                    "team": team_abbr,
                    "league": league,
                    "_year": year,
                    "_source": "wikipedia",
                })
    
    # Strategy 2: Look for dedicated coaching staff tables
    # Tables often have columns like "Position", "Name" or "Role", "Name"
    for table in soup.find_all("table", {"class": "wikitable"}):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        
        # Check if this looks like a coaching table
        is_coaching_table = any(
            keyword in " ".join(headers)
            for keyword in ["coach", "staff", "position", "role"]
        )
        
        if not is_coaching_table:
            continue
        
        # Find position and name columns
        position_col = None
        name_col = None
        
        for i, header in enumerate(headers):
            if any(kw in header for kw in ["position", "role", "coach", "title"]):
                position_col = i
            if any(kw in header for kw in ["name", "coach"]):
                name_col = i
        
        if position_col is None or name_col is None:
            continue
        
        # Extract rows
        for row in table.find_all("tr")[1:]:  # Skip header
            cells = row.find_all("td")
            if len(cells) > max(position_col or 0, name_col or 0):
                try:
                    role = cells[position_col].get_text(strip=True) if position_col is not None else ""
                    name = cells[name_col].get_text(strip=True) if name_col is not None else ""
                    
                    # Skip if role doesn't look like a coaching position
                    if role and any(
                        kw in role.lower()
                        for kw in ["coach", "coordinator", "trainer", "manager", "director"]
                    ):
                        # Try to extract just the name part (remove citations, etc.)
                        name = re.sub(r"\[\d+\]", "", name).strip()
                        
                        if name and len(name) > 2:
                            coaches.append({
                                "name": name,
                                "role": role,
                                "team": team_abbr,
                                "league": league,
                                "_year": year,
                                "_source": "wikipedia_table",
                            })
                except (IndexError, AttributeError):
                    continue
    
    return coaches


def extract_coaches_from_team_section(
    html: str,
    year: int,
    league: str,
) -> dict[str, list[dict]]:
    """
    Extract coaching staff organized by team from a season article.
    
    Looks for sections labeled by team abbreviation (e.g. "## XFL Teams") and
    extracts coaching info for each team.
    
    Returns dict: {team_abbr: [coach_dict, ...]}
    """
    soup = BeautifulSoup(html, "html.parser")
    team_coaches: dict[str, list[dict]] = {}
    
    # This is a more complex parsing task that depends on the page structure.
    # For now, return empty dict — will implement based on specific Wikipedia layouts
    
    return team_coaches


def normalize_coach_role(role: str) -> str:
    """
    Normalize coaching role names to canonical forms.
    
    Examples:
      "Head Coach" → "Head Coach"
      "HC" → "Head Coach"
      "OC" → "Offensive Coordinator"
      "DC" → "Defensive Coordinator"
      "ST Coach" → "Special Teams Coach"
    """
    role = role.strip().title()
    
    role_map = {
        "Hc": "Head Coach",
        "Head": "Head Coach",
        "Oc": "Offensive Coordinator",
        "Offensive": "Offensive Coordinator",
        "Dc": "Defensive Coordinator",
        "Defensive": "Defensive Coordinator",
        "Qb Coach": "Quarterbacks Coach",
        "Quarterbacks Coach": "Quarterbacks Coach",
        "Rb Coach": "Running Backs Coach",
        "Running Backs Coach": "Running Backs Coach",
        "Wr Coach": "Wide Receivers Coach",
        "Wide Receivers Coach": "Wide Receivers Coach",
        "St Coach": "Special Teams Coach",
        "Special Teams Coach": "Special Teams Coach",
        "Ol Coach": "Offensive Line Coach",
        "Offensive Line Coach": "Offensive Line Coach",
        "Dl Coach": "Defensive Line Coach",
        "Defensive Line Coach": "Defensive Line Coach",
        "Lb Coach": "Linebackers Coach",
        "Linebackers Coach": "Linebackers Coach",
        "Db Coach": "Defensive Backs Coach",
        "Defensive Backs Coach": "Defensive Backs Coach",
    }
    
    return role_map.get(role, role)


def dedup_coaches(
    coaches: list[dict],
    by_name_role_team: bool = True,
) -> list[dict]:
    """
    Remove duplicate coach entries.
    
    If by_name_role_team=True, dedup by (name, role, team) combination.
    Otherwise dedup by (name, role) only.
    """
    seen = set()
    unique = []
    
    for coach in coaches:
        if by_name_role_team:
            key = (coach.get("name", ""), coach.get("role", ""), coach.get("team", ""))
        else:
            key = (coach.get("name", ""), coach.get("role", ""))
        
        if key not in seen:
            seen.add(key)
            unique.append(coach)
    
    return unique
