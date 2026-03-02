# dicer-reports

Performance reports for Dicer.ai × Otto Insurance campaigns.

## 📊 Latest Report

🔗 https://dicer-ai.github.io/dicer-reports/index.html

## Structure

```
scripts/
  generate_report.py   ← Single script to regenerate any report
reports/
  otto-performance-YYYY-MM-DD.html   ← Archived daily reports
index.html                           ← Latest report (always current)
```

## Usage

### Prerequisites
```bash
pip install -r requirements.txt   # no external deps — stdlib only
```

### Generate last 7 days (default)
```bash
python3 scripts/generate_report.py
```

### Custom date range
```bash
python3 scripts/generate_report.py --start 2026-02-23 --end 2026-03-01
```

### Full month
```bash
python3 scripts/generate_report.py \
  --start 2026-02-01 \
  --end 2026-02-28 \
  --label "February 2026" \
  --out reports/otto-performance-feb-2026.html
```

### Environment variables (optional — defaults already set in script)
```bash
export OTTO_API_KEY="35a5ec5a-39f3-4ed1-9b01-6ebc51f3b147"
export OTTO_API_URL="https://api.useotto.tech/external/dicer/index.php"
export MISCLASS_URL="https://attribution-check.dicer.ai/dicer_misclassified.json"
```

## What the report includes

- **3 tabs**: Overall / Otto Auto / Otto Home
- **Live data**: Pulled directly from Otto API on each run
- **Attribution-corrected**: Misclassified Dicer creatives automatically reclassified via `attribution-check.dicer.ai`
- **Top creatives**: Ranked by spend with embedded images and per-ad stats
- **Head-to-head table**: Dicer vs Non-Dicer on ROAS, CPC, CTR, CVR, conversions
- **Spend bar**: Visual budget allocation
- **Home underperformance alert**: Auto-fires when Dicer Home ROAS < Non-Dicer Home

## Daily automation (cron)

To run daily at 8am ET and push to GitHub Pages:
```bash
# Add to cron via PCC or system crontab
0 13 * * * cd /path/to/dicer-reports && python3 scripts/generate_report.py && git add -A && git commit -m "chore: daily report $(date +%F)" && git push
```

## Report history
| Report | Period | Link |
|--------|--------|------|
| L7D (Feb 23–Mar 1) | Feb 23 – Mar 1, 2026 | [View](https://dicer-ai.github.io/dicer-reports/reports/otto-performance-7d-2026-03-01.html) |
| L7D (Feb 23–Mar 2) | Feb 23 – Mar 2, 2026 | [View](https://dicer-ai.github.io/dicer-reports/reports/otto-performance-7d-2026-03-02.html) |
| February 2026 | Feb 1–28, 2026 | [View](https://dicer-ai.github.io/dicer-reports/reports/otto-performance-feb-2026.html) |
