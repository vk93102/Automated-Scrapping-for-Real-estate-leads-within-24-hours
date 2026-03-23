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
