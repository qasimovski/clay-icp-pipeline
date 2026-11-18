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

BRAND COLUMNS - HARD RULE (user, 2026-11-18). Every Buy_side_, Sell_side_ and Other_sources_ file (the Labs
list schema; Competitive files have no such columns) fills, on EVERY row:
    Brand                 = the short brand, the same token as the file name (EVCharge, Solar)
    Brand Classification  = <Brand>_Buyer when the row's Classification is Buyer, <Brand>_Seller when Seller
                            (EVCharge_Buyer, EVCharge_Seller, Solar_Buyer, Solar_Seller; capital B / S)
Decisions taken with the user the same day: capitalised suffix for every brand; Brand = salesforce_brand, not a
spaced event name; all three scopes. Files built before 2026-11-18 left both columns blank (new exports only).

    sf_naming.brand_fields("EVCharge", "Buyer")   -> {"Brand": "EVCharge", "Brand Classification": "EVCharge_Buyer"}
    sf_naming.check_brand_columns(rows, "EVCharge") -> raises NameError_ on a blank / wrong Brand or Brand Classification
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


SIDES = {"buyer": "Buyer", "seller": "Seller"}
BRAND_SCOPES = ("Other_sources", "Buy_side", "Sell_side")      # files that carry Brand / Brand Classification


def brand_fields(brand, classification):
    """Brand + Brand Classification for one row (hard rule, user 2026-11-18). classification: Buyer / Seller."""
    side = SIDES.get((classification or "").strip().lower())
    if not side:
        raise NameError_(f"Classification {classification!r} must be Buyer or Seller to set Brand Classification")
    brand = _token("brand", brand)
    return {"Brand": brand, "Brand Classification": f"{brand}_{side}"}


def check_brand_columns(rows, brand):
    """Raise NameError_ unless every row has Brand == brand and Brand Classification == <brand>_<its side>.
    Run it on the rows of every Buy_side_ / Sell_side_ / Other_sources_ file before writing it. -> row count."""
    bad, n = [], 0
    for n, r in enumerate(rows, 1):
        try:
            want = brand_fields(brand, r.get("Classification"))
        except NameError_ as e:
            bad.append(f"row {n}: {e}")
        else:
            if r.get("Brand") != want["Brand"] or r.get("Brand Classification") != want["Brand Classification"]:
                bad.append(f"row {n}: Brand {r.get('Brand')!r} / Brand Classification "
                           f"{r.get('Brand Classification')!r}, want {want['Brand']!r} / {want['Brand Classification']!r}")
        if len(bad) >= 5:
            break
    if bad:
        raise NameError_("Brand columns break the rule (user 2026-11-18): " + "; ".join(bad))
    return n
