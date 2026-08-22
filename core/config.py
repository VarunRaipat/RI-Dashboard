import math
import re

# ── Users & Roles ─────────────────────────────────────────────────────────────
# Credentials live in Streamlit secrets (`[users]` in .streamlit/secrets.toml,
# or the app's Secrets panel on Streamlit Cloud) — never hardcode them here.
# If secrets aren't configured, login fails closed (no default users).
USERS = {}

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
DEFAULT_RM_PRICES["concrete"] = 3500.0  # confirmed rate: Concrete Cost = Volume (m³) x 3500
RM_LABELS = {m["key"]: f"{m['label']} (Rs./{m['unit']})" for m in RAW_MATERIALS}

# ── Product cost config ────────────────────────────────────────────────────────
# Formula: Product (variable) Cost = RM (Concrete+Steel) + Production +
#          Loading/Unloading + Welding + Jalli (cage welding) + Misc%
#          (Misc is a % of RM cost only, not the whole variable cost).
# EMI/Power/Admin are whole-factory overheads that run regardless of which
# products (or how many) were made on a given day, so they are charged once
# per calendar day that has production — never split across product lines —
# and only ever appear in day/period-level totals (see
# core.calculations.daily_fixed_costs), not on individual DPR entries.
EMI_PER_DAY   = round(283_000 / 30, 2)  # Rs. 283,000/month EMI ÷ 30 days — confirmed
POWER_PER_DAY =  1_000  # Rs. 30,000/month power (incl. DG) ÷ 30 days — confirmed
ADMIN_PER_DAY =  15_000  # Rs. fixed per production day — confirmed
MISC_PCT      =   10.0  # % of raw material (Concrete+Steel) cost only — confirmed

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

# NP2 Collar and NP2 M/F (confirmed) are the exact same physical pipe cast —
# the joint is only fitted/specified at dispatch, not during production — so
# they share one production/inventory pool, with M/F as the canonical SKU
# that's produced and stocked. Collar stays a separate, selectable line item
# on Sales Order/Dispatch, but draws down the same M/F stock (see
# INVENTORY_PRODUCTS below). NP3's Socket & Spigot vs M/F remain genuinely
# separate physical SKUs — this fold does not apply to NP3.
NP2_JOINTS_SHARE_STOCK = {"Collar", "M/F"}
NP2_CANONICAL_JOINT    = "M/F"

def _production_joint_types_for(diameter_mm, cls):
    """Same as _joint_types_for, but collapses NP2 Collar into its canonical
    M/F SKU — Collar isn't a separate production option since it's the same
    casting."""
    joints = _joint_types_for(diameter_mm, cls)
    if cls == "NP2" and set(joints) >= NP2_JOINTS_SHARE_STOCK:
        return [NP2_CANONICAL_JOINT]
    return joints

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
# No "power_per_block" — Power is a factory-wide flat POWER_PER_DAY (like
# EMI/Admin), not a per-unit or per-product rate.
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

for _pillar in ["Pillar 8'", "Pillar 10'", "Pillar 12'"]:
    PRODUCT_CONFIG[_pillar] = {"display": _pillar, **_blank_rates()}

PRODUCT_CONFIG["Fencing Pillar"] = {"display": "Fencing Pillar", **_blank_rates()}
PRODUCT_CONFIG["PSC Pole"]       = {"display": "PSC Pole", **_blank_rates()}
PRODUCT_CONFIG["Boundary Wall"]  = {"display": "Boundary Wall", **_blank_rates()}  # quantity still entered as Nos.

# Selling Price unit shown in Admin > Product Cost Configuration — every
# product prices per nos except Boundary Wall, which is quoted Rs./sqft
# (quantity/production/dispatch tracking is unaffected, still counted in Nos).
SELLING_PRICE_UNIT = {"Boundary Wall": "sqft"}
def selling_price_unit(product: str) -> str:
    return SELLING_PRICE_UNIT.get(product, "nos")

del _blank_rates, _d, _c, _name, _slab, _pillar, _thickness_class, _thickness

# ── Boundary Wall — sold per sqft (rft x height) as one Sales Order line,
# but never itself cast or dispatched: production casts Slab + Pillar
# separately (priced above via Admin > Product Cost Configuration), and
# Dispatch draws down those two SKUs directly, not "Boundary Wall". These
# mappings only feed the Boundary Wall Calculator (Sales Orders page), which
# works out how many Slabs/Pillars a given rft+height needs, and their cost,
# to help set/sanity-check the quoted Rs./sqft rate — confirmed by client.
# Pillar sits 2ft below ground, so wall height = pillar length - 2ft.
# 12' wall (would need Pillar 14') isn't offered — Pillar 14' isn't a
# stocked product.
BOUNDARY_WALL_PILLAR_FOR_HEIGHT = {
    6:  "Pillar 8'",
    8:  "Pillar 10'",
    10: "Pillar 12'",
}
# Installation labour, Rs./rft — confirmed for 6'/8'/10'.
BOUNDARY_WALL_INSTALL_RATE_PER_RFT = {6: 95.0, 8: 90.0, 10: 95.0}
# Slab panel length (ft) -> pillar spacing along the wall (one slab course
# spans exactly one gap between two adjacent pillars).
BOUNDARY_WALL_SLAB_LENGTH_FT = {"Slab 7'": 7, "Slab 8'": 8}
# The SKUs a Boundary Wall DI is actually fulfilled with on Dispatch (see
# comment above) — used by core.visibility.di_dispatch_warnings so dispatching
# Slab/Pillar against a Boundary Wall Sales Order isn't flagged as a mismatch.
BOUNDARY_WALL_DISPATCH_SKUS = set(BOUNDARY_WALL_SLAB_LENGTH_FT) | set(BOUNDARY_WALL_PILLAR_FOR_HEIGHT.values())

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
    for joint in _production_joint_types_for(d, c)
]
_NON_PIPE_PRODUCTS = [p for p in PRODUCT_CONFIG if not p.startswith("Hume Pipe")]
# Boundary Wall is quoted per sqft on Sales Orders, but is never itself cast
# or dispatched — production casts Slab + Pillar separately, and Dispatch
# draws down those two SKUs directly (confirmed by client; see the
# BOUNDARY_WALL_* mappings above). So it's excluded from the production,
# dispatch, and finished-goods-inventory product lists (there's no stock
# for something never produced or dispatched) — it only stays in
# ORDER_PRODUCTS, since that's the only place it's actually sold as a line.
_NON_PIPE_PRODUCTION_DISPATCH = [p for p in _NON_PIPE_PRODUCTS if p != "Boundary Wall"]

SKU_TO_PRICING_KEY = {sku: sku.rsplit(" (", 1)[0] for sku in _PIPE_SKUS}
SKU_TO_PRICING_KEY.update({p: p for p in _NON_PIPE_PRODUCTS})

# NP4 is not a DPR option — production is always logged as NP3 (or NP2).
PRODUCTION_PRODUCTS = _PIPE_SKUS_PRODUCTION + _NON_PIPE_PRODUCTION_DISPATCH
# NP4 IS sellable — Sales Orders / Dispatch can select it, and it draws down
# the matching NP3 SKU's stock (see INVENTORY_PRODUCTS below).
ORDER_PRODUCTS    = _PIPE_SKUS + _NON_PIPE_PRODUCTS
DISPATCH_PRODUCTS = _PIPE_SKUS + _NON_PIPE_PRODUCTION_DISPATCH

HUME_PIPE_PRODUCTS = list(_PIPE_SKUS)

_PIPE_SKU_RE = re.compile(r"^Hume Pipe (\d+)mm (NP\d) \((.+)\)$")

def parse_pipe_sku(sku: str):
    """Split a Hume Pipe SKU ("Hume Pipe 300mm NP3 (Socket & Spigot)") into
    (diameter_mm: int, pipe_class: str, joint_type: str) for demand/size
    analysis. Returns None for non-pipe products (Slab/Pillar/etc.), which
    don't have this diameter+class+joint shape."""
    m = _PIPE_SKU_RE.match(str(sku or ""))
    return (int(m.group(1)), m.group(2), m.group(3)) if m else None

TRUCKS    = ["2821", "1669", "4879", "8391", "Other"]
DRIVERS   = ["Peter","Ladhu","Islam","Bhadiya","Sukra","Debu","Kaila","Sahdeo","Tinku","Nimiya","Yashwant","Raghunath","Karan","Other"]
CLIENTS   = ["Other"]                               # TODO: seed with known clients as they come in

PAYMENT_MODES = ["Cash", "Bank Transfer", "Credit", "GPAY", "PhonePe", "Other"]

SALE_TYPES = ["Sale A", "Sale B"]

# Floors for the auto-assigned Challan No. (core/sequencing.py's
# next_sequence_number). Each plant keeps its own physical challan book, so
# Challan No. runs as a separate sequence per (plant, Sale Type) — Pipe
# Factory challan 406 and Pole Factory challan 406 are two different
# challans in two different books, and neither plant's numbering is affected
# by how many challans the other one writes.
#
# A floor is only needed where the book was already in use before the app
# started recording it — the app then picks up from that number instead of
# from 1. Pipe Factory's Sale A book is the one sequence the app has full
# history for, so it has no floor and simply continues from its own max.
# Pipe Factory Sale B (356) was confirmed earlier; Pole Factory's Sale A
# (406) and Sale B (176) are the client's current paper positions, so the
# next challan the app assigns is 406/176 and it counts up from there.
# A (plant, sale_type) pair with no entry here just starts at 1.
CHALLAN_NO_START = {
    ("Pipe Factory", "Sale B"): 356,
    ("Pole Factory", "Sale A"): 406,
    ("Pole Factory", "Sale B"): 176,
}


def challan_no_start(plant, sale_type):
    return CHALLAN_NO_START.get((plant, sale_type), 1)


# DI No. is not split per plant — a DI is raised against a Sales Order, which
# can legitimately span both plants, so it stays one sequence per Sale Type.
DI_NO_START      = {"Sale B": 1560}

# One-time correction: a Sale A dispatch entry (URC Construction, 1-Apr-2026)
# has challan_no "212" — an out-of-sequence typo; the surrounding entries run
# 2-73 this FY (confirmed). Excluded from the auto-suggest max calculation so
# new Sale A challans suggest 74, 75, 76... instead of jumping to 213. The
# stored record itself is untouched and still shows "212".
CHALLAN_NO_IGNORE = {"Sale A": {212}}

CLIENT_TYPES = ["Govt Contractor", "Private Contractor", "Retail", "Developer", "Inter Company"]

PRODUCT_TYPES = ["Pipe", "Boundary Wall", "PSC Pole", "Fencing Post"]

FACTORIES = ["Rameshwaram Industries"]

QUOTATION_UNITS         = ["Nos", "Rft", "Cft", "Sqft"]
QUOTATION_STATUS        = ["Draft", "Sent", "Accepted", "Rejected", "Expired", "Converted"]
QUOTATION_VALIDITY_DAYS = 30

PLANTS = ["Pipe Factory", "Pole Factory"]

# Two physically separate plants (confirmed): every Hume Pipe SKU is cast at
# Pipe Factory; every other product (Slab, Pillar, Fencing Pillar, PSC Pole,
# Boundary Wall, and any admin-added custom product — those are always
# non-pipe, see sync_custom_products()) is cast at Pole Factory. Fixed 1:1
# mapping, not a per-entry choice, so plant is derived from the product
# instead of being a second independent field wherever a product/SKU already
# appears (Dispatch, Sales Orders, Quotations). Works for both a full SKU
# ("Hume Pipe 300mm NP2 (M/F)") and a bare pricing key ("Hume Pipe 300mm
# NP2") since SKU_TO_PRICING_KEY.get() falls back to the input unchanged.
def plant_for_product(product_or_sku: str) -> str:
    pricing_key = SKU_TO_PRICING_KEY.get(product_or_sku, product_or_sku)
    return "Pipe Factory" if str(pricing_key).startswith("Hume Pipe") else "Pole Factory"


def products_for_plant(products, plant: str):
    return [p for p in products if plant_for_product(p) == plant]

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

# ── Labour Liability ──────────────────────────────────────────────────────────
# Weekly amount owed to production labour, computed live from DPR/Dispatch
# data (nothing entered manually) — see views/liability.py. Cost heads are
# Production + Jalli + Welding + Repairing + Loading/Unloading. Repairing
# isn't a separate DPR field — it's always REPAIRING_PCT_OF_PRODUCTION% of
# that period's Production cost (confirmed rate, not measured per repair
# job). Loading/Unloading is costed off Dispatch quantity, not DPR Nos,
# since that labour happens when goods go out, not when they're cast.
REPAIRING_PCT_OF_PRODUCTION = 20.0

# ── Inventory ─────────────────────────────────────────────────────────────────
# Opening stock as counted on INVENTORY_ANCHOR_DATE. Current balance for a
# product = opening + (production since anchor) - (dispatched since anchor).
# Must stay a fixed date, not date.today() — this recomputing on every app
# restart/redeploy was silently excluding all earlier production/dispatch
# from the stock calculation each time. Update this only when a new physical
# stock count is done (and re-enter opening quantities to match).
INVENTORY_ANCHOR_DATE = "2026-07-01"

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
        for _joint in _production_joint_types_for(_d, _c):
            _sku = f"Hume Pipe {_d}mm {_c} ({_joint})"
            _prod_names = [_sku]
            _disp_names = [_sku]
            # NP2 Collar shares the M/F row's production+dispatch history —
            # it was never a separate physical pipe, just an earlier way of
            # specifying the same stock (see _production_joint_types_for).
            if _c == "NP2" and _joint == NP2_CANONICAL_JOINT and "Collar" in _joint_types_for(_d, _c):
                _collar_sku = f"Hume Pipe {_d}mm NP2 (Collar)"
                if _collar_sku in _PIPE_SKUS:
                    _prod_names.append(_collar_sku)
                    _disp_names.append(_collar_sku)
            if _c == NP4_SHARES_CLASS:
                _np4_sku = f"Hume Pipe {_d}mm NP4 ({_joint})"
                if _np4_sku in _PIPE_SKUS:
                    _disp_names.append(_np4_sku)
            INVENTORY_PRODUCTS.append((
                _sku,
                tuple(_prod_names) if len(_prod_names) > 1 else _sku,
                tuple(_disp_names) if len(_disp_names) > 1 else _sku,
                0,
            ))
INVENTORY_PRODUCTS += [(p, p, p, 0) for p in _NON_PIPE_PRODUCTION_DISPATCH]

del _d, _c, _joint, _sku, _prod_names, _disp_names, _collar_sku, _np4_sku

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
RM_INVENTORY_OPENING = {"cement_ppc": 82, "ggbs": 0, "sand": 0}
CEMENT_GGBS_KG_PER_BAG = 50

# Labels for every RM_INVENTORY_OPENING key — cement_ppc/ggbs/sand are
# inventory-only entries that aren't priced/costed materials, just tracked
# for stock reconciliation.
INVENTORY_MATERIAL_LABELS = {m["key"]: m["label"] for m in RAW_MATERIALS}
INVENTORY_MATERIAL_LABELS.update({"cement_ppc": "PPC Cement", "ggbs": "GGBS", "sand": "Sand"})

# ── Gate Entry (raw material / equipment / parts movement log) ───────────────
GATE_CATEGORIES = ["Raw Material", "Plant Equipment & Parts", "Miscellaneous Parts"]
GATE_DIRECTIONS = ["In", "Out"]
GATE_UNITS      = ["Ton", "CFT", "Nos", "Kg", "Litre", "Bags", "Other"]

# Steel is still loggable at the gate (for record-keeping) but no longer
# gets a running inventory balance — moved out of RM_INVENTORY_OPENING.
GATE_UNTRACKED_ITEMS = ["steel", "20mm Chips"]

GATE_RM_TRACKED_ITEMS = list(RM_INVENTORY_OPENING.keys())

GATE_RM_ITEMS = GATE_UNTRACKED_ITEMS + GATE_RM_TRACKED_ITEMS + ["Other"]

# ── Runtime-registered pipe diameters (admin-added, no code change) ──────────
# Every Hume Pipe diameter above was built into PRODUCT_CONFIG /
# HUME_PIPE_JOINT_TYPES / SKU_TO_PRICING_KEY / PRODUCTION_PRODUCTS /
# ORDER_PRODUCTS / DISPATCH_PRODUCTS / INVENTORY_PRODUCTS at import time, from
# HUME_PIPE_DIAMETERS_MM + BARREL_THICKNESS_MM. register_diameter() does the
# same construction for ONE new diameter, called at runtime (by
# core.db.sync_custom_diameters(), itself called from app.py on every rerun)
# so a diameter an admin adds via Admin > Product Config > Add New Diameter
# shows up everywhere immediately, in place, no deploy needed. Only the
# barrel thickness per class is admin-supplied (a physical fact about the
# pipe, same kind of number as a price) — the joint-type rules (>600mm is
# M/F-only, NP2 gets Collar/M-F, NP3 gets Socket&Spigot/M-F, NP2 Collar
# shares NP2 M/F stock, NP4 shares NP3 stock) are the existing generic
# functions above, so a new diameter behaves identically to a built-in one.
# Leaving a class's thickness at 0 means "not made at this diameter" —
# same convention as the gaps already in BARREL_THICKNESS_MM (e.g. NP2 above
# 900mm): the SKU still exists (sellable) but concrete_volume_m3 is 0 until
# a real thickness is entered.
def register_diameter(diameter_mm, np2_thickness_mm=0, np3_thickness_mm=0):
    diameter_mm = int(diameter_mm)
    if diameter_mm in HUME_PIPE_DIAMETERS_MM:
        return  # idempotent — safe to call every rerun

    HUME_PIPE_DIAMETERS_MM.append(diameter_mm)
    HUME_PIPE_DIAMETERS_MM.sort()

    if np2_thickness_mm:
        BARREL_THICKNESS_MM[("NP2", diameter_mm)] = np2_thickness_mm
    if np3_thickness_mm:
        BARREL_THICKNESS_MM[("NP3", diameter_mm)] = np3_thickness_mm

    PIPE_DIAMETER_CONFIG[diameter_mm] = _blank_diameter_rates()

    joint_types_by_base = {}
    for c in HUME_PIPE_SALE_CLASSES:
        name = f"Hume Pipe {diameter_mm}mm {c}"
        PRICING_KEY_TO_DIAMETER_MM[name] = diameter_mm
        thickness_class = NP4_SHARES_CLASS if c == "NP4" else c
        thickness = BARREL_THICKNESS_MM.get((thickness_class, diameter_mm), 0)
        PRODUCT_CONFIG[name] = {
            "display": name, "selling_price": 0.0,
            "concrete_volume_m3": _concrete_volume_m3(diameter_mm, thickness),
        }
        joints = _joint_types_for(diameter_mm, c)
        HUME_PIPE_JOINT_TYPES[name] = joints
        joint_types_by_base[name] = joints

    new_pipe_skus = [
        f"{base} ({joint})"
        for base, joints in joint_types_by_base.items()
        for joint in joints
    ]
    for sku in new_pipe_skus:
        SKU_TO_PRICING_KEY[sku] = sku.rsplit(" (", 1)[0]
    HUME_PIPE_PRODUCTS.extend(new_pipe_skus)
    ORDER_PRODUCTS.extend(new_pipe_skus)
    DISPATCH_PRODUCTS.extend(new_pipe_skus)

    for c in HUME_PIPE_PRODUCTION_CLASSES:
        for joint in _production_joint_types_for(diameter_mm, c):
            sku = f"Hume Pipe {diameter_mm}mm {c} ({joint})"
            PRODUCTION_PRODUCTS.append(sku)

            prod_names = [sku]
            disp_names = [sku]
            if c == "NP2" and joint == NP2_CANONICAL_JOINT and "Collar" in _joint_types_for(diameter_mm, c):
                collar_sku = f"Hume Pipe {diameter_mm}mm NP2 (Collar)"
                if collar_sku in new_pipe_skus:
                    prod_names.append(collar_sku)
                    disp_names.append(collar_sku)
            if c == NP4_SHARES_CLASS:
                np4_sku = f"Hume Pipe {diameter_mm}mm NP4 ({joint})"
                if np4_sku in new_pipe_skus:
                    disp_names.append(np4_sku)

            INVENTORY_PRODUCTS.append((
                sku,
                tuple(prod_names) if len(prod_names) > 1 else sku,
                tuple(disp_names) if len(disp_names) > 1 else sku,
                0,
            ))
