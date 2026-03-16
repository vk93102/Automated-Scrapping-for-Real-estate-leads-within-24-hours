# VPS Deployment Guide — Real Estate Leads Scraper

End-to-end steps to deploy the Coconino + Gila + Maricopa cron pipeline on a fresh Ubuntu 22.04 VPS.

---

## 1. Provision the VPS

| Recommended spec | Minimum |
|---|---|
| 2 vCPU, 4 GB RAM | 1 vCPU, 2 GB RAM |
| 40 GB SSD | 20 GB SSD |
| Ubuntu 22.04 LTS | Ubuntu 20.04 LTS |

Popular providers: DigitalOcean ($24/mo Droplet), Linode/Akamai, Vultr, Hetzner (cheapest).

---

## 2. Initial Server Setup

```bash
# Log in as root then create a non-root sudo user
adduser deploy
usermod -aG sudo deploy
su - deploy
```

---

## 3. Install System Dependencies

```bash
sudo apt update && sudo apt upgrade -y

# Core build tools
sudo apt install -y \
  build-essential libssl-dev zlib1g-dev libbz2-dev \
  libreadline-dev libsqlite3-dev curl git wget \
  libffi-dev liblzma-dev libncurses5-dev libncursesw5-dev \
  xz-utils tk-dev

# OCR tools (Tesseract + PDF utilities)
sudo apt install -y tesseract-ocr poppler-utils

# Playwright system deps (Chromium headless)
sudo apt install -y \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
  libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
  libxdamage1 libxfixes3 libxrandr2 libgbm1 \
  libasound2 libpango-1.0-0 libpangocairo-1.0-0 \
  libgtk-3-0 libx11-xcb1 libxcb-dri3-0

# (Optional) PostgreSQL client only — if DB is remote Supabase
sudo apt install -y libpq-dev postgresql-client
```

---

## 4. Install pyenv + Python 3.10.13

```bash
# Install pyenv
curl https://pyenv.run | bash

# Add to ~/.bashrc (also add to ~/.profile and ~/.bash_profile)
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.bashrc
echo 'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.bashrc
echo 'eval "$(pyenv init -)"' >> ~/.bashrc
source ~/.bashrc

# Install Python 3.10.13
pyenv install 3.10.13
pyenv global 3.10.13

# Verify
python --version  # Should print: Python 3.10.13
```

---

## 5. Clone the Repository

```bash
cd ~
git clone https://github.com/YOUR_ORG/YOUR_REPO.git leads
cd leads
```

> If private repo: add a deploy key or use HTTPS with a Personal Access Token.

---

## 6. Install Python Packages

```bash
# Inside the repo root:
pip install --upgrade pip
pip install -r requirements.txt

# Install Playwright + its Chromium browser
playwright install chromium
playwright install-deps chromium   # installs missing OS libs automatically
```

---

## 7. Configure Environment Variables

```bash
cp .env.example .env    # if the file exists, otherwise create from scratch
nano .env
```

Required variables:

```dotenv
# Groq LLM API (for OCR extraction)
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx

# Supabase / Postgres (for Maricopa pipeline)
DATABASE_URL=postgresql://postgres.xxxx:password@aws-0-us-east-1.pooler.supabase.com:5432/postgres

# Optional proxy rotation
USE_PROXY=false
PROXY_LIST_PATH=/home/deploy/leads/proxies.txt

# Timezone
TZ=America/Phoenix
```

---

## 8. Fix Python Path in run_pipeline.sh

The script hardcodes the Python path. On the VPS it will be different — check and update:

```bash
# Find the actual path on this VPS
pyenv which python
# e.g. /home/deploy/.pyenv/versions/3.10.13/bin/python
```

Then open [run_pipeline.sh](run_pipeline.sh) and set:

```bash
PY_BIN="/home/deploy/.pyenv/versions/3.10.13/bin/python"
```

---

## 9. Test All Three Pipelines Manually

```bash
# 1. Test Maricopa pipeline (needs DATABASE_URL + GROQ_API_KEY)
bash run_pipeline.sh

# 2. Test unified Coconino + Gila cron (7-day rolling window by default)
python run_daily_leads.py

# 3. Test old Coconino standalone (optional — legacy path)
cd conino && bash run_coconino_cron.sh && cd ..
```

Check output directories:
```bash
ls -lh output/               # Maricopa output
ls -lh conino/output/        # Coconino CSVs/JSONs
ls -lh gila/output/          # Gila CSVs/JSONs
```

---

## 10. Install Cron Jobs

```bash
# Use the setup script (installs the unified leads cron)
bash scripts/setup_leads_cron.sh install

# Verify the crontab
crontab -l
```

Expected crontab on the VPS (update paths from `/Users/vishaljha/` to `/home/deploy/`):

```cron
# Maricopa County — every 10 minutes
*/10 * * * * cd /home/deploy/leads && /bin/bash run_pipeline.sh >> logs/cron_master.log 2>&1

# Coconino + Gila unified — every 15 minutes
*/15 * * * * cd /home/deploy/leads && /home/deploy/.pyenv/versions/3.10.13/bin/python run_daily_leads.py >> logs/leads_cron_master.log 2>&1
```

> The old `conino/run_coconino_cron.sh` cron entry is **redundant** — it is already covered by `run_daily_leads.py`. Remove it to avoid duplicate runs.

---

## 11. Set Up Log Rotation

```bash
sudo nano /etc/logrotate.d/leads
```

```
/home/deploy/leads/logs/*.log {
    daily
    rotate 30
    compress
    missingok
    notifempty
    sharedscripts
}
```

---

## 12. (Optional) systemd Service for the HTTP Server

If you also run the Flask/Gunicorn API server (`server.py` / `gunicorn.conf.py`):

```bash
sudo nano /etc/systemd/system/leads-api.service
```

```ini
[Unit]
Description=Real Estate Leads API
After=network.target

[Service]
User=deploy
WorkingDirectory=/home/deploy/leads
Environment="PATH=/home/deploy/.pyenv/versions/3.10.13/bin:/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=/home/deploy/leads/.env
ExecStart=/home/deploy/.pyenv/versions/3.10.13/bin/gunicorn -c gunicorn.conf.py maricopa_scraper.server:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable leads-api
sudo systemctl start leads-api
sudo systemctl status leads-api
```

---

## 13. Monitoring & Debugging

```bash
# Watch the unified cron log live
tail -f /home/deploy/leads/logs/leads_cron_master.log

# Watch the Maricopa cron log live
tail -f /home/deploy/leads/logs/cron_master.log

# Check old Coconino log (if old cron entry still active)
tail -f /home/deploy/leads/conino/output/cron.log

# See all currently running Python processes
pgrep -a python

# Check the lock file (if a run is stuck)
ls -lh /home/deploy/leads/tmp/run_daily_leads.lock

# Remove stale lock manually if a run died mid-way
rm /home/deploy/leads/tmp/run_daily_leads.lock
```

---

## 14. Firewall (Optional but Recommended)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 8080   # only if API server is exposed externally
sudo ufw enable
sudo ufw status
```

---

## 15. Keeping the Repo Up-to-date

```bash
cd /home/deploy/leads
git pull origin main
pip install -r requirements.txt  # in case deps changed
# cron jobs will auto-pick up changes on next run
```

---

## Quick Reference — Path Differences

| Path segment | macOS (local) | VPS (Ubuntu) |
|---|---|---|
| Repo root | `/Users/vishaljha/Automated-Scrapping-...` | `/home/deploy/leads` |
| Python binary | `/Users/vishaljha/.pyenv/versions/3.10.13/bin/python` | `/home/deploy/.pyenv/versions/3.10.13/bin/python` |
| pyenv root | `/Users/vishaljha/.pyenv` | `/home/deploy/.pyenv` |
