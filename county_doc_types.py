"""Unified document types for Arizona county recording leads."""

# Key document types that indicate real estate/financial transactions
UNIFIED_LEAD_DOC_TYPES = {
    # Deed/Trust transactions
    "DEED",
    "DEED OF TRUST",
    "TRUST DEED",
    "MORTGAGE",
    
    # Notice/Sale
    "NOTICE OF SALE",
    "NOTICE OF TRUSTEE SALE", 
    "N/TR SALE",
    "NOTICE OF DEFAULT",
    "NOTICE OF REINSTATEMENT",
    "LIS PENDENS",
    
    # Liens/Judgments
    "LIEN",
    "JUDGMENT LIEN",
    "TAX LIEN",
    "JUDGMENT",
    
    # Foreclosure
    "FORECLOSURE",
    "DEED IN LIEU",
    "TRUSTEES DEED",
    "TREASURERS DEED",
}


# Foreclosure / distress-oriented document types.
#
# These are the highest-signal categories for pre-foreclosure outreach.
# (Some counties may not expose all of these as distinct dropdown values; in that
# case the pipeline will skip unavailable types.)
UNIFIED_FORECLOSURE_DOC_TYPES = {
    "NOTICE OF DEFAULT",
    "NOTICE OF TRUSTEE SALE",
    "NOTICE OF SALE",
    "NOTICE OF REINSTATEMENT",
    "LIS PENDENS",
    "FORECLOSURE",
    "DEED IN LIEU",
    # Post-sale / transfer docs (useful for analytics; typically lower outreach priority)
    "TRUSTEES DEED",
    "SHERIFFS DEED",
    "TREASURERS DEED",
}


# Coconino County (EagleWeb SelfService at eagleassessor.coconino.az.gov:8444)
# Supported document types for foreclosure/distress leads.
COCONINO_DOC_TYPES = {
    "LIS PENDENS",
    "LIS PENDENS RELEASE",
    "TRUSTEES DEED UPON SALE",
    "SHERIFFS DEED",
    "NOTICE OF TRUSTEES SALE",
    "TREASURERS DEED",
    "AMENDED STATE LIEN",
    "STATE LIEN",
    "STATE TAX LIEN",
    "RELEASE STATE TAX LIEN",
}
