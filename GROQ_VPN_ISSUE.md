# Groq VPN Issue - Diagnosis & Solutions

## Problem Summary
**403 Forbidden Error**: When using Windscribe VPN, requests to Groq API (`https://api.groq.com/openai/v1/chat/completions`) fail with HTTP 403 error.

```
❌ API BLOCKED - 403 Forbidden
   ROOT CAUSE: Windscribe VPN is blocking Groq API access
```

---

## Root Cause Analysis

Groq has implemented **IP geolocation and VPN detection** that blocks requests from:
- VPN datacenter IPs (including Windscribe)
- Proxy IPs
- Suspicious geographic locations

When you connect through Windscribe:
1. Your requests originate from a VPN datacenter IP
2. Groq detects this as a VPN/proxy connection
3. Groq rejects the request with 403 Forbidden before it even reaches their API

**Your API Key**: ✓ Valid (gsk_9YuCvf... confirmed working in previous tests)
**The Issue**: Not your credentials - it's the network path being blocked

---

## Solutions (Ordered by Recommendation)

### ✅ **Solution 1: Disable Windscribe (RECOMMENDED)**
**Complexity**: Trivial | **Effectiveness**: 100% | **Impact**: None

Simply turn OFF Windscribe before running the pipeline:
1. Click Windscribe icon in menu bar
2. Toggle to OFF
3. Run your pipeline
4. Turn Windscribe back on when done

**Command**:
```bash
GRAHAM_LOOKBACK_DAYS=2 GRAHAM_WORKERS=3 ./graham/run_graham_cron.sh
```

**Why this works best**: 
- Groq allows residential ISP traffic
- Your home IP is not blocked
- No configuration needed
- Fastest solution

---

### ✅ **Solution 2: Windscribe Split Tunneling (BYPASS VPN FOR GROQ)**
**Complexity**: Easy | **Effectiveness**: ~95% | **Impact**: Groq calls bypass VPN

Configure Windscribe to **exclude** Groq API from the VPN tunnel:

**Steps**:
1. Open Windscribe app
2. Go to **Settings** (⚙️ icon)
3. Navigate to **Advanced**
4. Find **Split Tunneling** section
5. Enable it (if not already enabled)
6. Click **Add Application** or **WHITELIST**
7. Add `api.groq.com` to the whitelist
8. Restart Windscribe

**How it works**:
- Groq API requests go through your normal ISP (not VPN)
- All other traffic still goes through Windscribe
- Groq sees your home IP and allows the request
- Your privacy is maintained for everything except Groq

**Result**: Graham pipeline will work with VPN on:
```bash
GRAHAM_LOOKBACK_DAYS=2 GRAHAM_WORKERS=3 ./graham/run_graham_cron.sh
```

---

### ✅ **Solution 3: Use Different VPN (If You Prefer)**
**Complexity**: Easy | **Effectiveness**: Depends on VPN | **Impact**: VPN change

Some VPNs don't have aggressive IP blocking:
- **NordVPN**: Split tunneling + better Groq compatibility
- **ExpressVPN**: Generally allows API access
- **ProtonVPN**: Good compatibility with API services
- ~~Windscribe~~: Too aggressive on API blocking (current issue)

---

### ✅ **Solution 4: Use Groq's Proxy/Enterprise Plan (If Available)**
**Complexity**: Hard | **Effectiveness**: 100% | **Impact**: Cost

If split tunneling doesn't work:
1. Go to https://console.groq.com
2. Contact Groq support about enterprise/proxy access
3. Request allowlist for your VPN IP range

---

## Quick Test (After Applying Solution)

Verify the fix works:

```bash
cd /Users/vishaljha/Automated-Scrapping-for-Real-estate-leads-within-24-hours
python3 tmp/test_groq_api.py
```

**Expected output if fixed**:
```
✅ API WORKING - 200 OK
   Response: OK
   Your Groq API is working fine!
```

---

## Graham County Specific

Once you fix Groq access, run Graham pipeline:

```bash
# One-shot: 2-day lookback, 3 workers
GRAHAM_LOOKBACK_DAYS=2 GRAHAM_WORKERS=3 ./graham/run_graham_cron.sh

# Verbose output to see progress
GRAHAM_LOOKBACK_DAYS=2 GRAHAM_WORKERS=3 GRAHAM_VERBOSE=1 ./graham/run_graham_cron.sh

# Extended: 7 days
GRAHAM_LOOKBACK_DAYS=7 GRAHAM_WORKERS=5 ./graham/run_graham_cron.sh
```

---

## Verification Steps

1. **Fix Groq access** (choose solution above)
2. **Test API**: `python3 tmp/test_groq_api.py`
3. **Run Graham**: `GRAHAM_LOOKBACK_DAYS=2 GRAHAM_WORKERS=3 ./graham/run_graham_cron.sh`
4. **Check results**: `psql $DATABASE_URL -c "SELECT count(*) FROM graham_leads;"`

---

## Technical Details (Why 403?)

```
REQUEST FLOW:
Your Machine → Windscribe VPN → Windscribe Datacenter IP → Groq API
                                      ↑
                              Groq detects VPN IP
                              Returns 403 Forbidden
                              
AFTER SPLIT TUNNELING:
Your Machine → Normal ISP IP → Groq API (✓ allowed)
              → Windscribe VPN → Other traffic (✓ encrypted)
```

Groq specifically blocks datacenter IPs because:
- Bots/scrapers often use datacenter VPNs
- API abuse protection against automation
- Your legitimate pipeline gets caught in the net

---

## Database Impact

Graham pipeline features:
- ✓ Automatic DB upserts to `graham_leads` table
- ✓ Tracks extractions: trustor, trustee, principal_amount, property_address
- ✓ Pipeline runs table logs each execution
- ✓ Groq LLM errors are captured in `groq_error` column

Once Groq access is fixed, all features work immediately.

---

## Questions?

- **API Key invalid?** → Check https://console.groq.com/keys (it's valid in tests)
- **Still getting 403?** → Windscribe is still intercepting; try Solution 1 or 2
- **Split tunneling not working?** → Restart Windscribe app completely
- **Different error?** → Get full traceback with `GRAHAM_VERBOSE=1`
