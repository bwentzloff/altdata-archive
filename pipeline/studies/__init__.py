"""Studies package — dynamic, data-driven articles with charts.

Each study is a module exposing:
  SLUG      : str   — url-safe identifier
  TITLE     : str   — headline
  SUBTITLE  : str   — short tagline
  CATEGORY  : str   — broad bucket (e.g., "Football")
  TAGS      : list[str]
  def compute(data_dir: Path) -> dict
      Returns a JSON-serializable payload with at minimum:
        {
          "headline_stats": [{"label": str, "value": str, "sub": str?}, ...],
          "charts":         [{"id": str, "type": str, "title": str,
                              "labels": [...], "datasets": [...],
                              "note": str?}, ...],
          "sections":       [{"heading": str, "html": str}, ...],
          "methodology":    str   (HTML allowed),
          "history_row":    {...}  flat dict appended to history file
        }
"""

from . import nfl_pipeline_leagues, qb_receiver_nomads, teammate_density_nfl, multi_league_same_year

# Ordered list — dictates the studies index ordering.
STUDIES = [nfl_pipeline_leagues, qb_receiver_nomads, teammate_density_nfl, multi_league_same_year]
