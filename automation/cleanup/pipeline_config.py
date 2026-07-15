"""Resolve entity-type + ICP config for the per-event Find-People passes.

Makes the seller/buyer people builds entity-agnostic (Exhibitors | Sponsors |
future Speakers) and ICP-agnostic (Labs | future verticals). Reads the same
config tree as template/render_build_prompt.py:

  config/entity-types/<entity>.yaml   -> table names (main, seller_people, buyer_people)
  config/icps/<icp>/people_search.yaml -> Find-People search config (builds, segments,
                                          location lists)

Usage:
    from pipeline_config import load
    cfg = load(entity="exhibitors", icp="labs")
    cfg.main_table            # 'Exhibitors_normalized'
    cfg.seller_people_table   # 'Sellers - People'
    cfg.buyer_people_table    # 'Buyers - People'
    cfg.seller_builds         # [{name, job_title_mode, job_titles, [experience]}, ...]
    cfg.seller_countries      # location list for the seller Find-People searches
    cfg.buyer_segments        # [{segment, exact, contains}, ...]
    cfg.buyer_countries       # 50-country location list for buyer searches

Seniority is defined once in people_fill_lib.js (SENIORITY_VALUES); it is the
same 11 levels for every entity/ICP here and is not re-declared per config.
"""
import os

try:
    import yaml
except ImportError:  # pragma: no cover
    raise SystemExit("pipeline_config needs PyYAML: pip install pyyaml")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# automation/cleanup -> automation -> <repo root>
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
CONFIG_DIR = os.path.join(REPO_ROOT, "config")

DEFAULT_ENTITY = os.environ.get("CLAY_PIPELINE_ENTITY", "exhibitors")
DEFAULT_ICP = os.environ.get("CLAY_PIPELINE_ICP", "labs")


def _load_yaml(path):
    if not os.path.exists(path):
        raise SystemExit(f"config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class PipelineConfig:
    def __init__(self, entity, icp):
        self.entity = entity
        self.icp = icp
        self.entity_cfg = _load_yaml(
            os.path.join(CONFIG_DIR, "entity-types", f"{entity}.yaml"))
        self.people_cfg = _load_yaml(
            os.path.join(CONFIG_DIR, "icps", icp, "people_search.yaml"))
        tables = self.entity_cfg.get("tables", {})
        self.main_table = tables["main"]
        # Saved Clay template names applied by apply_v1 / apply_lookup passes.
        self.templates = self.entity_cfg.get("templates", {})
        # people-table names default to the classic Exhibitors names if a config
        # predates the seller_people/buyer_people keys.
        self.seller_people_table = tables.get("seller_people", "Sellers - People")
        self.buyer_people_table = tables.get("buyer_people", "Buyers - People")
        seller = self.people_cfg.get("seller", {})
        buyer = self.people_cfg.get("buyer", {})
        self.seller_builds = seller.get("builds", [])
        self.seller_countries = seller.get("location_countries", [])
        self.buyer_segments = buyer.get("segments", [])
        self.buyer_countries = buyer.get("location_countries", [])

    def slug(self):
        """Short tag for per-entity state/log filenames (e.g. 'exhibitors_labs')."""
        return f"{self.entity}_{self.icp}"


def load(entity=None, icp=None):
    return PipelineConfig(entity or DEFAULT_ENTITY, icp or DEFAULT_ICP)


def add_cli_args(ap):
    """Attach --entity / --icp to an argparse parser (shared by the rollouts)."""
    ap.add_argument("--entity", default=DEFAULT_ENTITY,
                    help="entity type (config/entity-types/<entity>.yaml); default exhibitors")
    ap.add_argument("--icp", default=DEFAULT_ICP,
                    help="ICP (config/icps/<icp>/); default labs")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Inspect resolved pipeline config")
    add_cli_args(ap)
    a = ap.parse_args()
    c = load(a.entity, a.icp)
    print(f"entity={c.entity} icp={c.icp}")
    print(f"  main_table         = {c.main_table}")
    print(f"  seller_people_table= {c.seller_people_table}")
    print(f"  buyer_people_table = {c.buyer_people_table}")
    print(f"  seller_builds      = {[b['name'] for b in c.seller_builds]}")
    print(f"  seller_countries   = {len(c.seller_countries)}")
    print(f"  buyer_segments     = {len(c.buyer_segments)}")
    print(f"  buyer_countries    = {len(c.buyer_countries)}")
