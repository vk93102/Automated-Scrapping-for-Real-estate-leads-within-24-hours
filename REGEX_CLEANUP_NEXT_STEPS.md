# REGEX FALLBACK REMOVAL - FINAL STATUS & NEXT STEPS

## ✅ WHAT WAS COMPLETED TODAY

### Greenlee Extractor - FULLY CLEANED
- **Deleted functions:** ~250+ lines of pure regex code
  - `_regex_address()` - Street address pattern matching
  - `_regex_principal()` - Dollar amount pattern matching
  - `_regex_party()` - Name extraction patterns
  - `_extract_party_block()` - Multi-line party block extraction
  - `_llm_regex_fallback_enabled()` - Control flag

- **Removed logic paths:** 3 separate fallback sections in extraction pipeline
  - Regex-only fallback when LLM unavailable
  - Hybrid LLM+regex fallback logic (2 instances)

- **Result:** Pure LLM-only extraction enforced

### Auto-Fixed Counties (Inherit from Greenlee)
- ✅ Cochise County - Automatically fixed
- ✅ Graham County - Automatically fixed  
- ✅ Gila County - Automatically fixed

---

## 🔄 TODO - REMAINING CLEANUPS

### 1. Navajo County - Remove Regex Functions
**File:** `navajo/extractor.py`
**Functions to delete:**
```python
def _regex_principal(text: str) -> str:  # Lines 702-720
def _regex_address(text: str) -> str:    # Lines 722-736
def _regex_party(text: str, label: str) -> str:  # Lines 738-741
```

**Calls to remove:**
```python
# In enrich_record() around lines 768-770:
- if not record.get("principalAmount"): record["principalAmount"] = _regex_principal(merged)
- if not record.get("propertyAddress"): record["propertyAddress"] = _regex_address(merged)
```

**Estimated time:** 5 minutes

---

### 2. Lapaz County - Remove Regex Functions
**File:** `lapaz/extractor.py`
**Functions to delete:**
```python
def _regex_principal(text: str) -> str:  # Lines 675-693
def _regex_address(text: str) -> str:    # Lines 695-709
def _regex_party(text: str, label: str) -> str:  # Lines 711-714
```

**Calls to remove:**
```python
# In enrich_record() around lines 741-743:
- if not record.get("principalAmount"): record["principalAmount"] = _regex_principal(merged)
- if not record.get("propertyAddress"): record["propertyAddress"] = _regex_address(merged)
```

**Estimated time:** 5 minutes

---

### 3. Conino County - Remove Candidate Extraction Fallbacks
**File:** `conino/extractor.py`
**Functions to delete:**
```python
def _extract_principal_candidates(text: str) -> list[str]:  # Lines 1049-1062
def _extract_address_candidates(text: str) -> list[str]:    # (find similar)
```

**Logic to remove:**
```python
# In _row_blocks() - remove HTML parsing fallback:
fallback_pattern = re.compile(...)  # Delete fallback extraction
return [match.group(1) for match in fallback_pattern.finditer(html_text)]

# In fetch_document_detail_fields() - remove candidate extraction:
address_candidates = _extract_address_candidates(_clean_text(body))
principal_candidates = _extract_principal_candidates(...)
```

**Estimated time:** 10 minutes

---

### 4. Maricopa - Remove Rule-Based Fallback
**File:** `maricopa/llm_extract.py`
**Changes required:**
```python
# Remove parameter from these functions:
def extract_fields_llm(..., fallback_to_rule_based: bool = True) -> ExtractedFields:
    # Change to: -> ExtractedFields: (no parameter)

# In extract_fields_llm_direct() - remove fallback logic:
if fallback_to_rule_based:
    logger.info("llm_extract: falling back to rule-based extraction")
    return extract_fields_rule_based(ocr_text)  # DELETE THESE LINES

# Replace with:
return _empty_fields()  # Strict LLM-only

# Remove import:
from .extract_rules import ExtractedFields, extract_fields_rule_based
# Keep: from .extract_rules import ExtractedFields  (if needed)
```

**Estimated time:** 10 minutes

---

### 5. Delete Unused Files
```bash
# Delete these legacy fallback utilities:
rm -f conino/fallback_detail_fetch.py          # ~150 lines
rm -f greenlee/debug_enrich.py                 # Debug file
rm -f lapaz/debug_enrich.py                    # Debug file

# Optional cleanup:
rm -f maricopa/extract_rules.py                # Only if no longer used elsewhere
```

**Estimated time:** 2 minutes

---

### 6. Santa Cruz County - FULL REVIEW NEEDED
**File:** `SANTA CRUZ/extractor.py`
**Status:** Not yet audited
**Action:** Search for regex functions and fallback logic like other counties

---

## 📊 VERIFICATION COMMANDS

After completing each cleanup, run these tests:

**Greenlee:**
```bash
python3 greenlee/run_greenlee_interval.py --days 1
# Check: logs show LLM extraction only, no regex function calls
```

**Navajo:**
```bash
python3 navajo/run_*_interval.py --days 1
# Verify: LLM-only extraction working
```

**Maricopa:**
```bash
python3 -m maricopa.scraper --begin-date 2025-03-18 --end-date 2025-03-19 --limit 10
# Verify: No extract_fields_rule_based calls in logs
```

---

## 📝 FINAL CHECKLIST

- [ ] Navajo: Remove 3 regex functions & 2 calls (~20 lines)
- [ ] Lapaz: Remove 3 regex functions & 2 calls (~20 lines)  
- [ ] Conino: Remove candidate extraction fallbacks (~30 lines)
- [ ] Maricopa: Remove fallback_to_rule_based parameter (~15 lines)
- [ ] Delete 3 unused utility files
- [ ] Review Santa Cruz
- [ ] Run smoke tests for each county
- [ ] Verify NO regex function calls remain: grep -r "_regex_" --include="*.py" | grep -v "def _regex"
- [ ] Verify NO fallback imports: grep -r "extract_fields_rule_based\|fallback_detail" --include="*.py"
- [ ] Update documentation

---

## 🎯 TOTAL SCOPE

| Task | Status | LOC | Time |
|------|--------|-----|------|
| Greenlee | ✅ | -250 | ✓ |
| Navajo | 🔄 | -20 | 5m |
| Lapaz | 🔄 | -20 | 5m |
| Conino | 🔄 | -30 | 10m |
| Maricopa | 🔄 | -15 | 10m |
| File deletion | 🔄 | -150 | 2m |
| Santa Cruz | ⏳ | TBD | TBD |
| Testing | ⏳ | - | 15m |
| **TOTAL** | | **-485** | **~1 hour** |

---

## 💡 KEY PRINCIPLES APPLIED

1. **LLM-First:** All extraction now uses LLM (Groq/Llama) as primary source
2. **No Regex Fallback:** When LLM unavailable, return empty fields instead of degraded regex extraction
3. **Deterministic:** No unpredictable regex patterns causing false positives
4. **Maintainable:** Single extraction path is easier to debug and improve
5. **Production-Ready:** Clean, focused code ready for production deployment

---

## 📞 CONTACT FOR QUESTIONS

If ambiguity arises during remaining cleanups:
1. Refer to Greenlee implementation for pattern (already cleaned)
2. Maintain LLM-only principle (no regex fallbacks)
3. Return empty fields rather than degraded data
4. Test each county after changes

---

**Next Session:** Complete Navajo, Lapaz, Conino, Maricopa cleanups and run full test suite.

*Last updated: 2026-03-25 20:50 UTC*
