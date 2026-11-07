"""File names for the FINAL Salesforce CSVs (the upload with every email and field), one rule for every ICP.

User, 2026-11-07:
    Competitive_{brand name}       Competitive Events
    Other_sources_{brand name}     Other Sources (associations, directories, trades)
    Buy_side_{brand name}          BuySide P&S (buyers found by company search)
    Sell_side_{brand name}         Products & Services (sellers found by company search)
Decisions taken with the user the same day:
  * brand = a SHORT brand with no spaces, one per ICP (EVCharge, Solar, Mining, Highways): letters and digits
    only. It lives in config/icps/<icp>/icp.yaml as `salesforce_brand`, so it is written once.
  * a scope with more than one main upload adds `_<Part>`: Other_sources_EVCharge_Trades.csv,
    Other_sources_EVCharge_Associations.csv (part: letters and digits only).
  * companion files keep the base name and add " - <label>": Buy_side_EVCharge - LinkedIn only.csv,
    Buy_side_EVCharge - duplicates removed.csv. So every file of one delivery sorts together.
  * applies to exports from 2026-11-07 on; files built earlier keep their names.

    import sf_naming
    sf_naming.filename("buy_side", "EVCharge")                 -> "Buy_side_EVCharge.csv"
    sf_naming.filename("other_sources", "EVCharge", "Trades")  -> "Other_sources_EVCharge_Trades.csv"
    sf_naming.companion("out/Buy_side_EVCharge.csv", "LinkedIn only") -> "out/Buy_side_EVCharge - LinkedIn only.csv"
    sf_naming.check("Buy_side_EVCharge.csv")                   -> raises NameError_ when it breaks the rule
"""

import os
import re

SCOPES = {
    "competitive": "Competitive",
    "other_sources": "Other_sources",
    "buy_side": "Buy_side",
    "sell_side": "Sell_side",
}
TOKEN = re.compile(r"[A-Za-z0-9]+")
MAIN = re.compile(r"^(Competitive|Other_sources|Buy_side|Sell_side)_([A-Za-z0-9]+)(?:_([A-Za-z0-9]+))?\.csv$")


class NameError_(ValueError):
    """A Salesforce file name that breaks the naming rule."""


def _token(kind, value):
    if not value or not TOKEN.fullmatch(value):
        raise NameError_(f"{kind} {value!r} must be letters and digits only, no spaces "
                         f"(short brand, e.g. EVCharge / Solar / Mining)")
    return value


def filename(scope, brand, part=""):
    """The main Salesforce CSV name for a scope + brand (+ part when a scope has several uploads)."""
    if scope not in SCOPES:
        raise NameError_(f"scope {scope!r} is not one of {sorted(SCOPES)}")
    name = f"{SCOPES[scope]}_{_token('brand', brand)}"
    if part:
        name += "_" + _token("part", part)
    return name + ".csv"


def companion(path, label):
    """A companion file of a main Salesforce CSV: same base name + " - <label>"."""
    stem, ext = os.path.splitext(path)
    return f"{stem} - {label}{ext or '.csv'}"


def check(path):
    """Raise NameError_ unless the file name (not the folder) follows the rule. -> (prefix, brand, part)."""
    m = MAIN.fullmatch(os.path.basename(path))
    if not m:
        raise NameError_(
            f"{os.path.basename(path)!r} breaks the Salesforce file naming rule: use "
            f"Competitive_<Brand>.csv / Other_sources_<Brand>.csv / Buy_side_<Brand>.csv / "
            f"Sell_side_<Brand>.csv (optional _<Part>), brand letters and digits only")
    return m.group(1), m.group(2), m.group(3) or ""
