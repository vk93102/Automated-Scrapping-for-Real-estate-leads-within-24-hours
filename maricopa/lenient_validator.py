"""
Lenient field validation - only rejects COMPLETELY empty/garbage records
Allows partial data through to be improved by LLM/enrichment
"""
import re


def validate_fields_lenient(record: dict) -> tuple:
    """
    Lenient validation - only reject records with NO meaningful data.
    
    Returns: (is_valid: bool, issues: list)
    """
    issues = []
    
    # Safely get field values (None-safe)
    trustor_1_first = (record.get('trustor_1_first_name') or '').strip()
    trustor_1_last = (record.get('trustor_1_last_name') or '').strip()
    trustor_2_first = (record.get('trustor_2_first_name') or '').strip()
    trustor_2_last = (record.get('trustor_2_last_name') or '').strip()
    address = (record.get('property_address') or '').strip()
    city = (record.get('city') or '').strip()
    state = (record.get('state') or '').strip()
    sale_date = (record.get('sale_date') or '').strip()
    balance = (record.get('original_principal_balance') or '').strip()
    
    # Check if record is COMPLETELY empty (reject only this case)
    all_fields = [trustor_1_first, trustor_1_last, trustor_2_first, trustor_2_last,
                  address, city, state, sale_date, balance]
    if not any(all_fields):
        return False, ['ALL FIELDS EMPTY - Completely void record']
    
    # Only validate fields that are present (lenient approach)
    
    # Borrower name check (if present)
    if trustor_1_first or trustor_1_last:
        combined_name = f"{trustor_1_first} {trustor_1_last}".upper()
        # Reject obvious junk patterns
        if any(junk in combined_name for junk in ['UNKNOWN', 'NONE', 'N/A', 'XXX', '000', '999']):
            issues.append('Borrower 1: Garbage value detected')
    
    # Address check (if present)
    if address:
        if len(address) < 5 or len(address) > 500:
            issues.append(f'Address: Invalid length ({len(address)} chars)')
        elif not any(c.isdigit() for c in address):
            # No house number - suspicious
            issues.append('Address: Missing house number')
    
    # City check (if present)
    if city:
        if len(city) < 2 or len(city) > 50:
            issues.append(f'City: Invalid length ({len(city)} chars)')
        # Allow any city name for now
    
    # State check (if present)
    if state and state != 'AZ':
        issues.append(f'State: Expected AZ, got {state}')
    
    # Date check (if present)
    if sale_date:
        # Should be MM/DD/YYYY format
        if not re.match(r'^\d{1,2}/\d{1,2}/\d{4}$', sale_date):
            issues.append(f'Sale Date: Invalid format: {sale_date} (expected MM/DD/YYYY)')
    
    # Balance check (if present)
    if balance:
        try:
            bal_float = float(balance)
            # Realistic range: $1K to $1B
            if bal_float < 1000 or bal_float > 1_000_000_000:
                issues.append(f'Balance: Outside realistic range (${bal_float:,.0f})')
        except ValueError:
            issues.append(f'Balance: Non-numeric value: {balance}')
    
    # If we found critical issues (not just missing optional fields), reject
    critical_issues = [i for i in issues if 'garbage' in i.lower() or 'junk' in i.lower()]
    if critical_issues:
        return False, issues
    
    # For now, accept records with partial data (lenient)
    return len(issues) == 0, issues
