import math
from datetime import date

# ── Users & Roles ─────────────────────────────────────────────────────────────
USERS = {
    "admin":      {"password": "***REMOVED***",     "role": "admin",      "name": "Admin"},
    "production": {"password": "***REMOVED***",   "role": "production", "name": "Production Operator"},
    "headoffice": {"password": "***REMOVED***",        "role": "headoffice", "name": "Head Office"},
    "viewer":     {"password": "***REMOVED***",    "role": "viewer",     "name": "Viewer"},
    "factory":    {"password": "***REMOVED***",   "role": "factory",    "name": "Factory"},
}

# ── Raw materials: single source of truth ─────────────────────────────────────
# Nothing is entered per DPR batch anymore — every material below is a fixed
# per-unit figure set once on the product (Admin > Product Cost Configuration)
# and multiplied by Nos, since usage is known per product rather than measured
# batch to batch. Each still needs its own price (Admin > RM Prices).
#   key            — internal identifier, also the DB column prefix
#   label          — human-readable name
#   unit           — "m³" or "Kg" — what the per-unit figure is measured in
#   product_field  — the PRODUCT_CONFIG key holding the per-unit quantity
RAW_MATERIALS = [
    {"key": "concrete", "label": "Concrete", "unit": "m³", "product_field": "concrete_volume_m3"},
    {"key": "steel",    "label": "Steel", "unit": "Kg", "product_field": "steel_kg_per_unit"},
]

DEFAULT_RM_PRICES = {m["key"]: 0.0 for m in RAW_MATERIALS}
DEFAULT_RM_PRICES["concrete"] = 2500.0  # confirmed rate: Concrete Cost = Volume (m³) x 2500
RM_LABELS = {m["key"]: f"{m['label']} (Rs./{m['unit']})" for m in RAW_MATERIALS}

# ── Product cost config ────────────────────────────────────────────────────────
# Formula: Total Cost = RM (Concrete+Steel) + Production + Loading/Unloading
#                      + Welding + Jalli (cage welding) + EMI + DG + Power + Admin, then + Misc%
EMI_PER_ENTRY   = 20_000  # Rs. fixed per DPR entry — placeholder, confirm with admin
DG_PER_ENTRY    =  5_000  # Rs. fixed per DPR entry — placeholder, confirm with admin
POWER_PER_ENTRY =  1_000  # Rs. fixed per DPR entry (daily) — confirmed
ADMIN_PER_ENTRY =  1_500  # Rs. fixed per DPR entry (daily) — confirmed
MISC_PCT        =   20.0  # % of all costs — confirmed

# GST on Selling Price — 18%. How this factors into profit_pct is being
# confirmed with the client before wiring into calculate_production().
GST_PCT = 18.0

HUME_PIPE_DIAMETERS_MM = [150, 200, 250, 300, 450, 600, 750, 900, 1000, 1200]

# Pipe barrel = hollow cylinder. Volume (m³) = pi/4 x (OD^2 - ID^2) x Length,
# with ID/OD/Length all in metres. Length is fixed at 2.5m for every pipe;
# OD = ID + 2 x barrel thickness.
PIPE_LENGTH_M = 2.5


def _concrete_volume_m3(diameter_mm, thickness_mm):
    if not thickness_mm:
        return 0.0
    id_m = diameter_mm / 1000
    od_m = (diameter_mm + 2 * thickness_mm) / 1000
    return round(math.pi / 4 * (od_m ** 2 - id_m ** 2) * PIPE_LENGTH_M, 4)


# Barrel thickness (mm) per (class, diameter) — client-supplied engineering
# data. NP4 looks up NP3's thickness for the same diameter (and therefore
# gets the same concrete volume) since it's the same physical pipe — this
# covers NP4-150mm too even though it isn't listed separately below. Diameters
# genuinely missing from a class (e.g. NP2 above 600mm) default to thickness 0
# (concrete_volume_m3 = 0) until confirmed; fix in Admin before selling those.
BARREL_THICKNESS_MM = {
    ("NP2", 150): 25, ("NP2", 200): 25, ("NP2", 250): 25, ("NP2", 300): 30,
    ("NP2", 450): 35, ("NP2", 600): 45, ("NP2", 900): 55,

    ("NP3", 150): 30, ("NP3", 200): 30, ("NP3", 250): 30, ("NP3", 300): 40,
    ("NP3", 450): 75, ("NP3", 600): 85, ("NP3", 750): 90, ("NP3", 900): 100,
    ("NP3", 1000): 115, ("NP3", 1200): 120,

    ("NP4", 200): 30, ("NP4", 250): 30, ("NP4", 300): 40, ("NP4", 450): 75,
    ("NP4", 600): 85, ("NP4", 750): 90, ("NP4", 900): 100, ("NP4", 1000): 115,
    ("NP4", 1200): 120,
}

# NP2/NP3 are actually produced and stocked. NP4 is NOT — it's the exact same
# physical pipe as NP3, just sold/certified under a different class (and
# possibly a different price), so NP4 never appears as a DPR production
# option, but does appear as a sellable Sales Order / Dispatch product,
# drawing down the matching NP3 SKU's inventory when sold (see
# INVENTORY_PRODUCTS below).
HUME_PIPE_PRODUCTION_CLASSES = ["NP2", "NP3"]
HUME_PIPE_SALE_CLASSES       = ["NP2", "NP3", "NP4"]
NP4_SHARES_CLASS             = "NP3"

JOINT_TYPES = ["Collar", "Socket & Spigot", "M/F"]

# Which Joint Types are actually manufactured for a given diameter+class —
# used to narrow the Joint Type dropdown per product on the Sales Order line
# (Joint Type is still a spec only, not a price driver). Rule, as confirmed:
#   NP2, 150-600mm  -> Collar or M/F
#   NP2, 750-1200mm -> M/F only
#   NP3, 150-600mm  -> Socket & Spigot or M/F
#   NP3, 750-1200mm -> M/F only
def _joint_types_for(diameter_mm, cls):
    if diameter_mm > 600:
        return ["M/F"]
    if cls == "NP2":
        return ["Collar", "M/F"]
    return ["Socket & Spigot", "M/F"]  # NP3

# All rates below are placeholders (0) until entered via Admin, except
# concrete_volume_m3 for pipes, which is pre-computed from BARREL_THICKNESS_MM.
#
# No "transport" field — real transport cost is already tracked in the
# Dispatch module (truck, trip distance, diesel cost), so a second per-unit
# transport rate here would double-count it.
#
# production_cost / loading_unloading_cost: renamed from labour_production /
# labour_loading — same ₹/nos mechanic, clearer names.
# welding_cost / jalli_cost: flat ₹/nos rates (Jalli = cage welding), not raw
# materials — depend on the product, not on a priced quantity.
# concrete_volume_m3 / steel_kg_per_unit: fixed physical quantity per unit —
# usage per DPR entry = Nos x this figure, at the matching RM Prices rate
# (see RAW_MATERIALS above).
# No "power_per_block" — Power is now a flat POWER_PER_ENTRY (like EMI/DG/
# Admin), not a per-unit rate.
def _blank_rates():
    return {
        "selling_price":          0.0,
        "production_cost":        0.0,
        "loading_unloading_cost": 0.0,
        "welding_cost":           0.0,
        "jalli_cost":             0.0,
        "concrete_volume_m3":     0.0,
        "steel_kg_per_unit":      0.0,
    }

# For Hume Pipes, Production/Loading-Unloading/Welding/Jalli/Steel are the
# same for a given diameter regardless of class (NP2/NP3/NP4) or Joint Type
# (confirmed) — so those 5 rates are set ONCE per diameter here (Admin >
# Pipe Diameter Rates), instead of being duplicated/edited separately across
# every class+joint SKU. Only Selling Price and Concrete Volume genuinely
# vary by class (different price points / wall thickness), so those stay in
# PRODUCT_CONFIG, keyed by diameter+class as before.
_PIPE_DIAMETER_FIELDS = [
    "production_cost", "loading_unloading_cost",
    "welding_cost", "jalli_cost", "steel_kg_per_unit",
]

def _blank_diameter_rates():
    return {f: 0.0 for f in _PIPE_DIAMETER_FIELDS}

PIPE_DIAMETER_CONFIG = {d: _blank_diameter_rates() for d in HUME_PIPE_DIAMETERS_MM}

# pipe pricing key ("Hume Pipe {d}mm {c}") -> diameter, so calculate_production()
# knows which PIPE_DIAMETER_CONFIG row to pull the shared rates from.
PRICING_KEY_TO_DIAMETER_MM = {
    f"Hume Pipe {d}mm {c}": d
    for d in HUME_PIPE_DIAMETERS_MM for c in HUME_PIPE_SALE_CLASSES
}

PRODUCT_CONFIG = {}
for _d in HUME_PIPE_DIAMETERS_MM:
    for _c in HUME_PIPE_SALE_CLASSES:
        _name = f"Hume Pipe {_d}mm {_c}"
        _thickness_class = NP4_SHARES_CLASS if _c == "NP4" else _c
        _thickness = BARREL_THICKNESS_MM.get((_thickness_class, _d), 0)
        # Only selling_price + concrete_volume_m3 live here for pipes — the
        # other 6 rates come from PIPE_DIAMETER_CONFIG at calculation time.
        PRODUCT_CONFIG[_name] = {
            "display": _name,
            "selling_price": 0.0,
            "concrete_volume_m3": _concrete_volume_m3(_d, _thickness),
        }

for _slab in ["Slab 7'", "Slab 8'", "Slab Design 7'"]:
    PRODUCT_CONFIG[_slab] = {"display": _slab, **_blank_rates()}

for _pillar in ["Pillar 8'", "Pillar 10'", "Pillar 12'", "Pillar 14'"]:
    PRODUCT_CONFIG[_pillar] = {"display": _pillar, **_blank_rates()}

PRODUCT_CONFIG["Fencing Pillar"] = {"display": "Fencing Pillar", **_blank_rates()}
PRODUCT_CONFIG["PSC Pole"]       = {"display": "PSC Pole", **_blank_rates()}

del _blank_rates, _d, _c, _name, _slab, _pillar, _thickness_class, _thickness

# ── SKUs vs. pricing keys ──────────────────────────────────────────────────────
# Joint Type doesn't change price, but a Collar pipe and an M/F pipe of the
# same diameter+class ARE physically different stock — so each Joint Type
# variant is its own SKU (used for DPR entry, Sales Order lines, Dispatch,
# and Inventory tracking), while PRODUCT_CONFIG above stays keyed by the base
# diameter+class name (so admin only sets one price per diameter+class, not
# once per joint type). SKU_TO_PRICING_KEY resolves a SKU back to the
# PRODUCT_CONFIG entry to charge/cost it against.
#
# Built across HUME_PIPE_SALE_CLASSES (NP2/NP3/NP4) since all three are
# sellable — NP4 just isn't a production option (see PRODUCTION_PRODUCTS).
HUME_PIPE_JOINT_TYPES = {
    f"Hume Pipe {d}mm {c}": _joint_types_for(d, c)
    for d in HUME_PIPE_DIAMETERS_MM for c in HUME_PIPE_SALE_CLASSES
}

_PIPE_SKUS = [
    f"{base} ({joint})"
    for base, joints in HUME_PIPE_JOINT_TYPES.items()
    for joint in joints
]
_PIPE_SKUS_PRODUCTION = [
    f"Hume Pipe {d}mm {c} ({joint})"
    for d in HUME_PIPE_DIAMETERS_MM
    for c in HUME_PIPE_PRODUCTION_CLASSES
    for joint in _joint_types_for(d, c)
]
_NON_PIPE_PRODUCTS = [p for p in PRODUCT_CONFIG if not p.startswith("Hume Pipe")]

SKU_TO_PRICING_KEY = {sku: sku.rsplit(" (", 1)[0] for sku in _PIPE_SKUS}
SKU_TO_PRICING_KEY.update({p: p for p in _NON_PIPE_PRODUCTS})

# NP4 is not a DPR option — production is always logged as NP3 (or NP2).
PRODUCTION_PRODUCTS = _PIPE_SKUS_PRODUCTION + _NON_PIPE_PRODUCTS
# NP4 IS sellable — Sales Orders / Dispatch can select it, and it draws down
# the matching NP3 SKU's stock (see INVENTORY_PRODUCTS below).
ORDER_PRODUCTS    = _PIPE_SKUS + _NON_PIPE_PRODUCTS
DISPATCH_PRODUCTS = _PIPE_SKUS + _NON_PIPE_PRODUCTS

HUME_PIPE_PRODUCTS = list(_PIPE_SKUS)

TRUCKS    = ["2821", "1669", "4879", "8391", "Other"]
DRIVERS   = ["Peter","Ladhu","Islam","Bhadiya","Sukra","Debu","Kaila","Sahdeo","Tinku","Nimiya","Yashwant","Raghunath","Karan","Other"]
CLIENTS   = ["Other"]                               # TODO: seed with known clients as they come in

PAYMENT_MODES = ["Cash", "Bank Transfer", "Credit", "GPAY", "PhonePe", "Other"]

SALE_TYPES = ["Sale A", "Sale B"]

CLIENT_TYPES = ["Govt Contractor", "Private Contractor", "Retail", "Developer"]

FACTORIES = ["Rameshwaram Industries"]

# TODO: replace with real plant/unit names if production runs across more than one.
PLANTS = ["Main Plant"]

# ── Payables ──────────────────────────────────────────────────────────────────
VENDOR_CATEGORIES = {
    "raw_material": "🟡 Raw Material / Regular",
    "capex":        "🟢 Capex",
    "small_vendor": "⚪ Small Vendor",
}

# TODO: seed with real vendor names once known — left empty for a fresh company.
VENDORS = {
    "raw_material": [],
    "capex":        [],
    "small_vendor": [],
}

ALL_VENDORS = (
    VENDORS["raw_material"] + VENDORS["capex"] + VENDORS["small_vendor"]
)

VENDOR_CATEGORY_MAP = {
    v: cat for cat, vlist in VENDORS.items() for v in vlist
}

# TODO: add real loan/EMI obligations if applicable.
LOAN_OBLIGATIONS = []

# ── Inventory ─────────────────────────────────────────────────────────────────
# Opening stock as counted on INVENTORY_ANCHOR_DATE. Current balance for a
# product = opening + (production since anchor) - (dispatched since anchor).
# Set opening quantities below once a physical stock count is done — all
# start at 0 (fresh app, no history yet).
INVENTORY_ANCHOR_DATE = str(date.today())

# canonical name, production name, dispatch/order name(s), opening qty
# Built from the SKU list (not PRODUCT_CONFIG) so Collar and M/F pipes of the
# same diameter+class are tracked as separate stock, even though they share
# one price. dispatch/order name is a tuple when more than one sellable SKU
# should draw down this same row — specifically, an NP4 SKU is folded into
# its matching NP3 row (same diameter+joint) since NP4 isn't separately
# produced or stocked; selling it consumes NP3's physical stock.
INVENTORY_PRODUCTS = []
for _d in HUME_PIPE_DIAMETERS_MM:
    for _c in HUME_PIPE_PRODUCTION_CLASSES:
        for _joint in _joint_types_for(_d, _c):
            _sku = f"Hume Pipe {_d}mm {_c} ({_joint})"
            _disp_names = [_sku]
            if _c == NP4_SHARES_CLASS:
                _np4_sku = f"Hume Pipe {_d}mm NP4 ({_joint})"
                if _np4_sku in _PIPE_SKUS:
                    _disp_names.append(_np4_sku)
            INVENTORY_PRODUCTS.append(
                (_sku, _sku, tuple(_disp_names) if len(_disp_names) > 1 else _sku, 0)
            )
INVENTORY_PRODUCTS += [(p, p, p, 0) for p in _NON_PIPE_PRODUCTS]

del _d, _c, _joint, _sku, _disp_names, _np4_sku

# Steel inventory: opening qty as of INVENTORY_ANCHOR_DATE. Current balance =
# opening + received (Gate Entry "In" log) - consumed (computed from
# Production Entry: Nos x the product's fixed per-unit figure). Concrete
# isn't a separately purchased/stocked item (it's mixed on-site), so it has
# no inventory balance — it's cost-only. Jalli is cage welding (a labour/
# process cost, not a raw material), so it has no inventory balance either.
#
# Cement (PPC) and GGBS are tracked here too, for inventory reconciliation
# only — DPR now also asks for the day's total Cement/GGBS bags consumed
# (see views/dpr.py), separately from the per-product Concrete costing
# above. "Consumed" for these two comes from the rm_usage table (one row per
# DPR submission, not tied to any single product), not from a production
# table column.
RM_INVENTORY_OPENING = {"steel": 0, "cement_ppc": 0, "ggbs": 0}
CEMENT_GGBS_KG_PER_BAG = 50

# Labels for every RM_INVENTORY_OPENING key — covers RAW_MATERIALS entries
# (steel) plus the inventory-only entries (cement_ppc, ggbs) that aren't
# priced/costed materials, just tracked for stock reconciliation.
INVENTORY_MATERIAL_LABELS = {m["key"]: m["label"] for m in RAW_MATERIALS}
INVENTORY_MATERIAL_LABELS.update({"cement_ppc": "PPC Cement", "ggbs": "GGBS"})

# ── Gate Entry (raw material / equipment / parts movement log) ───────────────
GATE_CATEGORIES = ["Raw Material", "Plant Equipment & Parts", "Miscellaneous Parts"]
GATE_DIRECTIONS = ["In", "Out"]
GATE_UNITS      = ["Ton", "CFT", "Nos", "Kg", "Litre", "Bags", "Other"]

# Nothing bulk/untracked for RI — Steel and Jalli both get a running balance
# via RM_INVENTORY_OPENING.
GATE_UNTRACKED_ITEMS = []

GATE_RM_TRACKED_ITEMS = list(RM_INVENTORY_OPENING.keys())

GATE_RM_ITEMS = GATE_UNTRACKED_ITEMS + GATE_RM_TRACKED_ITEMS + ["Other"]
