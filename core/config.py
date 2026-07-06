from datetime import date

# ── Users & Roles ─────────────────────────────────────────────────────────────
USERS = {
    "admin":      {"password": "***REMOVED***",     "role": "admin",      "name": "Admin"},
    "production": {"password": "ri@prod2024",  "role": "production", "name": "Production Operator"},
    "dispatch":   {"password": "ri@disp2024",  "role": "dispatch",   "name": "Dispatch Operator"},
    "headoffice": {"password": "***REMOVED***2024",    "role": "headoffice", "name": "Head Office"},
    "viewer":     {"password": "ri@view2024",  "role": "viewer",     "name": "Viewer"},
    "quality":    {"password": "ri@quality",   "role": "quality",    "name": "Quality Control"},
}

# ── Raw materials: single source of truth ─────────────────────────────────────
# Every raw-material-driven view (DPR form, cost calculation, Supabase schema,
# RM Prices admin page) is generated from this list — adding/renaming a
# material for the next company duplicate means editing only this list plus
# the matching columns in supabase_schema.sql / core/db.py's SQLite fallback.
#   key          — internal identifier, also the DB column prefix
#   label        — human-readable name shown in forms/tables
#   unit         — "Bags" or "Kg" — what the operator actually enters
#   kg_per_unit  — conversion to kg for weight-% breakdown (1 if already kg)
RAW_MATERIALS = [
    {"key": "cement_ppc",   "label": "PPC Cement",    "unit": "Bags", "kg_per_unit": 50},
    {"key": "ggbs",         "label": "GGBS",          "unit": "Bags", "kg_per_unit": 50},
    {"key": "ht_wire_3mm",  "label": "HT Wire 3 mm",  "unit": "Kg",   "kg_per_unit": 1},
    {"key": "ht_wire_3_5mm","label": "HT Wire 3.5 mm","unit": "Kg",   "kg_per_unit": 1},
    {"key": "ht_wire_4mm",  "label": "HT Wire 4 mm",  "unit": "Kg",   "kg_per_unit": 1},
    {"key": "ht_wire_5_5mm","label": "HT Wire 5.5 mm","unit": "Kg",   "kg_per_unit": 1},
]

DEFAULT_RM_PRICES = {m["key"]: 0.0 for m in RAW_MATERIALS}
RM_LABELS = {m["key"]: f"{m['label']} (Rs./kg)" for m in RAW_MATERIALS}

# ── Product cost config ────────────────────────────────────────────────────────
# Formula: Total Cost = RM + Labour + Transport + Power + EMI + DG + Admin, then + Misc%
EMI_PER_ENTRY   = 20_000  # Rs. fixed per DPR entry — placeholder, confirm with admin
DG_PER_ENTRY    =  5_000  # Rs. fixed per DPR entry — placeholder, confirm with admin
ADMIN_PER_ENTRY =  8_000  # Rs. fixed per DPR entry — placeholder, confirm with admin
MISC_PCT        =   10.0  # % of all costs — placeholder, confirm with admin

HUME_PIPE_DIAMETERS_MM = [150, 200, 250, 300, 450, 600, 750, 900, 1000, 1200]
HUME_PIPE_CLASSES      = ["NP2", "NP3", "NP4"]
JOINT_TYPES             = ["Collar", "Socket & Spigot", "M/F"]

# Which Joint Types are actually manufactured for a given diameter+class —
# used to narrow the Joint Type dropdown per product on the Sales Order line
# (Joint Type is still a spec only, not a price driver). Rule, as confirmed:
#   NP2, 150-600mm  -> Collar or M/F
#   NP2, 750-1200mm -> M/F only
#   NP3, 150-600mm  -> Socket & Spigot or M/F
#   NP3, 750-1200mm -> M/F only
#   NP4, all sizes   -> M/F only
def _joint_types_for(diameter_mm, cls):
    if cls == "NP4" or diameter_mm > 600:
        return ["M/F"]
    if cls == "NP2":
        return ["Collar", "M/F"]
    return ["Socket & Spigot", "M/F"]  # NP3

# All selling_price / labour / transport / power values below are placeholders
# (0) — real rates must be entered via Admin > Product Cost Configuration
# before profit figures mean anything. Kept as one flat dict (not nested by
# diameter/class) so every product is independently editable there, same as
# Ecostructures' PRODUCT_CONFIG pattern.
def _blank_rates():
    return {
        "selling_price":       0.0,
        "labour_production":   0.0,
        "labour_loading":      0.0,
        "transport_per_block": 0.0,
        "power_per_block":     0.0,
    }

PRODUCT_CONFIG = {}
for _d in HUME_PIPE_DIAMETERS_MM:
    for _c in HUME_PIPE_CLASSES:
        _name = f"Hume Pipe {_d}mm {_c}"
        PRODUCT_CONFIG[_name] = {"display": _name, **_blank_rates()}

for _slab in ["Slab 7'", "Slab 8'", "Slab Design 7'"]:
    PRODUCT_CONFIG[_slab] = {"display": _slab, **_blank_rates()}

for _pillar in ["Pillar 8'", "Pillar 10'", "Pillar 12'", "Pillar 14'"]:
    PRODUCT_CONFIG[_pillar] = {"display": _pillar, **_blank_rates()}

PRODUCT_CONFIG["Fencing Pillar"] = {"display": "Fencing Pillar", **_blank_rates()}
PRODUCT_CONFIG["PSC Pole"]       = {"display": "PSC Pole", **_blank_rates()}

del _blank_rates, _d, _c, _name, _slab, _pillar

# ── SKUs vs. pricing keys ──────────────────────────────────────────────────────
# Joint Type doesn't change price, but a Collar pipe and an M/F pipe of the
# same diameter+class ARE physically different stock — so each Joint Type
# variant is its own SKU (used for DPR entry, Sales Order lines, Dispatch,
# and Inventory tracking), while PRODUCT_CONFIG above stays keyed by the base
# diameter+class name (so admin only sets one price per diameter+class, not
# once per joint type). SKU_TO_PRICING_KEY resolves a SKU back to the
# PRODUCT_CONFIG entry to charge/cost it against.
HUME_PIPE_JOINT_TYPES = {
    f"Hume Pipe {d}mm {c}": _joint_types_for(d, c)
    for d in HUME_PIPE_DIAMETERS_MM for c in HUME_PIPE_CLASSES
}

_PIPE_SKUS = [
    f"{base} ({joint})"
    for base, joints in HUME_PIPE_JOINT_TYPES.items()
    for joint in joints
]
_NON_PIPE_PRODUCTS = [p for p in PRODUCT_CONFIG if not p.startswith("Hume Pipe")]

SKU_TO_PRICING_KEY = {sku: sku.rsplit(" (", 1)[0] for sku in _PIPE_SKUS}
SKU_TO_PRICING_KEY.update({p: p for p in _NON_PIPE_PRODUCTS})

PRODUCTION_PRODUCTS = _PIPE_SKUS + _NON_PIPE_PRODUCTS
ORDER_PRODUCTS      = _PIPE_SKUS + _NON_PIPE_PRODUCTS
DISPATCH_PRODUCTS   = _PIPE_SKUS + _NON_PIPE_PRODUCTS

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

INVENTORY_PRODUCTS = [
    # canonical name, production name, dispatch/order name, opening qty
    # Built from the SKU list (not PRODUCT_CONFIG) so Collar and M/F pipes of
    # the same diameter+class are tracked as separate stock, even though they
    # share one price.
    (p, p, p, 0) for p in PRODUCTION_PRODUCTS
]

# Cement / GGBS / HT Wire bag & coil inventory: opening qty as of
# INVENTORY_ANCHOR_DATE. Current balance = opening + received (Gate Entry
# "In" log) - consumed (summed from Production Entry).
RM_INVENTORY_OPENING = {m["key"]: 0 for m in RAW_MATERIALS}

# ── Gate Entry (raw material / equipment / parts movement log) ───────────────
GATE_CATEGORIES = ["Raw Material", "Plant Equipment & Parts", "Miscellaneous Parts"]
GATE_DIRECTIONS = ["In", "Out"]
GATE_UNITS      = ["Ton", "CFT", "Nos", "Kg", "Litre", "Bags", "Other"]

# Nothing bulk/untracked for RI — cement, GGBS, and all HT wire gauges get a
# running balance via RM_INVENTORY_OPENING.
GATE_UNTRACKED_ITEMS = []

GATE_RM_TRACKED_ITEMS = list(RM_INVENTORY_OPENING.keys())

GATE_RM_ITEMS = GATE_UNTRACKED_ITEMS + GATE_RM_TRACKED_ITEMS + ["Other"]
