"""Original gold document for Gate M. Authored for this benchmark (CC0).

Fictional construction supply agreement. Not a real contract. Unique locators
are stable tokens used by gold questions.
"""
from __future__ import annotations

GOLD_FILENAME = "ben_gold_supply_agreement.pdf"
GOLD_TITLE = "BEN Gold Supply Agreement"

PAGES: list[str] = [
    # 1
    "\n".join(
        [
            "[[PAGE 1]]",
            "BEN Gold Supply Agreement",
            "Agreement ID: BEN-GOLD-SA-2026-001",
            "Buyer: Coastal Works Ltd, company number 515998001",
            "Supplier: Northwind Insulation Ltd, company number 514112889",
            "Effective date: 1 March 2026",
            "Governing law: State of Israel",
            "Project name: Netanya Logistics Annex",
            "This is a synthetic test contract for retrieval measurement only.",
        ]
    ),
    # 2
    "\n".join(
        [
            "[[PAGE 2]]",
            "Article 1. Scope and delivery place.",
            "This Agreement covers supply of mineral wool thermal insulation for the",
            "Netanya Logistics Annex. The named delivery place is 12 HaMelacha Street,",
            "Netanya. Standard lead time is 21 calendar days after a valid purchase order.",
            "Locator: DELIVERY-PLACE-HAMELACHA-12",
        ]
    ),
    # 3
    "\n".join(
        [
            "[[PAGE 3]]",
            "Article 2. Primary product specification.",
            "Primary product: mineral wool boards.",
            "Nominal density: 100 kg/m3.",
            "Declared thermal conductivity lambda: 0.035 W/mK.",
            "Thickness: 50 mm.",
            "Contract quantity: 10,000 square metres.",
            "SKU of the primary product: MW-50.",
            "Locator: SPEC-LAMBDA-0035",
        ]
    ),
    # 4
    "\n".join(
        [
            "[[PAGE 4]]",
            "Article 3. Unit price schedule (exclusive of VAT, ILS per m2).",
            "SKU | Description | Thickness | Unit price ILS",
            "MW-50 | Mineral wool board | 50 mm | 42.50",
            "MW-80 | Mineral wool board | 80 mm | 61.00",
            "MW-100 | Mineral wool board | 100 mm | 74.25",
            "VAT is 17 percent and is added on invoices.",
            "Locator: PRICE-TABLE-MW50-4250",
        ]
    ),
    # 5
    "\n".join(
        [
            "[[PAGE 5]]",
            "Article 4. Payment.",
            "Payment term: 30 days from invoice date.",
            "Retention: 5 percent held until practical completion.",
            "Article 5. Shipment completeness (main rule).",
            "Supplier shall not make partial shipments. Each purchase order ships complete.",
            "Locator: MAIN-RULE-NO-PARTIAL-SHIP",
        ]
    ),
    # 6
    "\n".join(
        [
            "[[PAGE 6]]",
            "Article 6. Certificates.",
            "Supplier shall furnish a fire-classification certificate to EN 13501-1 class A1",
            "with the first delivery. An ISO 9001 certificate is required.",
            "This Agreement does not mention ISO 14001.",
            "Locator: FIRE-CERT-EN13501-A1",
        ]
    ),
    # 7
    "\n".join(
        [
            "[[PAGE 7]]",
            "Article 7. Warranty.",
            "Warranty period is 24 months from delivery. Claims must be written within",
            "10 business days of discovery of a defect.",
            "Locator: WARRANTY-24-MONTHS",
        ]
    ),
    # 8
    "\n".join(
        [
            "[[PAGE 8]]",
            "Article 8. Delay.",
            "If delivery is delayed, the Buyer may issue a written notice. The parties",
            "shall meet within 5 business days. This Article does not set a liquidated",
            "damages amount per day.",
            "Locator: DELAY-NO-LD-RATE",
        ]
    ),
    # 9
    "\n".join(
        [
            "[[PAGE 9]]",
            "Article 9. Insurance.",
            "Supplier maintains product liability insurance of at least ILS 8,000,000",
            "per occurrence.",
            "Locator: INSURANCE-ILS-8000000",
        ]
    ),
    # 10
    "\n".join(
        [
            "[[PAGE 10]]",
            "Article 10. Contacts.",
            "Supplier commercial contact: Dana Levi, email dana.levi@northwind-gold.example",
            "No project manager is named in this Agreement.",
            "Locator: CONTACT-DANA-LEVI",
        ]
    ),
    # 11
    "\n".join(
        [
            "[[PAGE 11]]",
            "Article 11. Packaging and storage.",
            "Goods ship on pallets, maximum 1.2 tonnes per pallet, with weather-protected wrap.",
            "Storage: keep dry and off the ground.",
            "Locator: PALLET-MAX-1P2-TONNES",
        ]
    ),
    # 12
    "\n".join(
        [
            "[[PAGE 12]]",
            "Article 12. Inspection.",
            "Buyer inspects within 7 calendar days of delivery. Silence is not deemed acceptance.",
            "Locator: INSPECT-7-CALENDAR-DAYS",
        ]
    ),
    # 13
    "\n".join(
        [
            "[[PAGE 13]]",
            "Article 13. Change orders.",
            "Changes require a written change order. Extra MW-50 uses the Article 3 table price",
            "plus 8 percent if Buyer requests delivery in under 10 days.",
            "Locator: EXPEDITE-PLUS-8-PERCENT",
        ]
    ),
    # 14
    "\n".join(
        [
            "[[PAGE 14]]",
            "Article 14. Emergency partial-shipment exception.",
            "EXCEPTION-PARTIAL-SHIP-48H: Notwithstanding Article 5, emergency orders of",
            "200 m2 or less may ship partial within 48 hours. This exception does not apply",
            "to MW-100.",
            "Locator: EXCEPTION-PARTIAL-SHIP-48H",
        ]
    ),
    # 15
    "\n".join(
        [
            "[[PAGE 15]]",
            "Article 15. Site offload window.",
            "Site working hours for offload: Sunday to Thursday 07:00 to 15:00.",
            "Forklift is provided by Buyer.",
            "Locator: OFFLOAD-WINDOW-0715",
        ]
    ),
    # 16
    "\n".join(
        [
            "[[PAGE 16]]",
            "Article 16. Regional standard lead times (calendar days).",
            "Region | Standard days | Expedite days",
            "Tel Aviv District | 14 | 7",
            "Haifa District | 18 | 9",
            "Southern District | 25 | 12",
            "Locator: LEADTIME-TABLE-REGIONS",
        ]
    ),
    # 17
    "\n".join(
        [
            "[[PAGE 17]]",
            "Article 17. Thickness substitution.",
            "If Buyer orders MW-80 instead of MW-50, the contract quantity remains",
            "10,000 square metres unless a change order reduces it.",
            "Locator: SUBSTITUTION-MW80-QTY",
        ]
    ),
    # 18
    "\n".join(
        [
            "[[PAGE 18]]",
            "Article 18. Termination and survival.",
            "Either party may terminate for material breach after 14 days cure notice.",
            "Confidentiality survives for 3 years after termination.",
            "This document is the entire agreement.",
            "Locator: CURE-14-DAYS",
        ]
    ),
]


def page_text(page_number: int) -> str:
    return PAGES[page_number - 1]
