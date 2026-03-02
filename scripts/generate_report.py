#!/usr/bin/env python3
"""
Dicer × Otto Performance Report Generator
==========================================
Generates a tabbed HTML report (Overall / Auto / Home) for any date window.
Pulls live data from Otto API and applies attribution corrections from
attribution-check.dicer.ai/dicer_misclassified.json.

Usage:
    # Last 7 days (default)
    python3 generate_report.py

    # Custom date range
    python3 generate_report.py --start 2026-02-23 --end 2026-03-01

    # Full month
    python3 generate_report.py --start 2026-02-01 --end 2026-02-28 --label "February 2026"

    # Output to specific file
    python3 generate_report.py --out ../reports/my-report.html

    # Skip attribution correction (faster, less accurate)
    python3 generate_report.py --no-attribution

Output:
    ../reports/otto-performance-YYYY-MM-DD.html  (default)
    ../index.html                                 (always updated as latest)

Environment variables (or set directly in config below):
    OTTO_API_KEY        Otto API bearer token
    OTTO_API_URL        Otto API endpoint
    MISCLASS_URL        Attribution check JSON URL
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.parse
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

# ─── CONFIG ─────────────────────────────────────────────────────────────────
OTTO_API_URL  = os.environ.get("OTTO_API_URL",  "https://api.useotto.tech/external/dicer/index.php")
OTTO_API_KEY  = os.environ.get("OTTO_API_KEY",  "35a5ec5a-39f3-4ed1-9b01-6ebc51f3b147")
MISCLASS_URL  = os.environ.get("MISCLASS_URL",  "https://attribution-check.dicer.ai/dicer_misclassified.json")
REPORTS_DIR   = Path(__file__).parent.parent / "reports"
INDEX_FILE    = Path(__file__).parent.parent / "index.html"
# ─────────────────────────────────────────────────────────────────────────────

def fetch_otto(start_date: str, end_date: str) -> list[dict]:
    """Fetch all ad rows from Otto API for the given date range."""
    payload = json.dumps({
        "key":        OTTO_API_KEY,
        "start_date": start_date,
        "end_date":   end_date,
    }).encode()
    req = urllib.request.Request(
        OTTO_API_URL,
        data=payload,
        headers={
            "Authorization": OTTO_API_KEY,
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    if not isinstance(data, list):
        raise ValueError(f"Unexpected Otto API response: {str(data)[:200]}")
    print(f"  Otto API: {len(data)} rows fetched ({start_date} → {end_date})")
    return data


def fetch_misclassified() -> set[str]:
    """Fetch misclassified network_ids from the attribution check endpoint."""
    try:
        with urllib.request.urlopen(MISCLASS_URL, timeout=15) as r:
            mc = json.loads(r.read())
        urls = {item["url"] for item in mc.get("misclassified_items", [])}
        print(f"  Attribution: {len(urls)} misclassified URLs loaded")
        return urls
    except Exception as e:
        print(f"  Attribution: fetch failed ({e}), skipping correction")
        return set()


def build_reclassify_nids(rows: list[dict], misclassified_urls: set[str]) -> set[str]:
    """Return set of network_ids to reclassify from Non-Dicer → Dicer."""
    by_url: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("thumbnail_url"):
            by_url[row["thumbnail_url"]].append(row)

    nids = set()
    matched = 0
    for url in misclassified_urls:
        if url in by_url:
            matched += 1
            for row in by_url[url]:
                if not (str(row.get("is_media_dicer", "0")) == "1" or row.get("is_dicer") is True):
                    nid = str(row.get("network_id", ""))
                    if nid:
                        nids.add(nid)

    print(f"  Attribution: {matched} misclassified URLs active in window → {len(nids)} ads reclassified")
    return nids


def is_dicer(row: dict, reclassify_nids: set[str]) -> bool:
    return (
        str(row.get("is_media_dicer", "0")) == "1"
        or row.get("is_dicer") is True
        or str(row.get("network_id", "")) in reclassify_nids
    )


def make_bucket() -> dict:
    return {"spend": 0.0, "revenue": 0.0, "clicks": 0.0,
            "impressions": 0.0, "conversions": 0.0, "rows": 0, "ads": set()}


def add_to_bucket(b: dict, row: dict) -> None:
    b["spend"]       += float(row.get("spend") or 0)
    b["revenue"]     += float(row.get("revenue") or 0)
    b["clicks"]      += float(row.get("clicks") or 0)
    b["impressions"] += float(row.get("impressions") or 0)
    b["conversions"] += float(row.get("form_conversions") or 0)
    b["rows"]        += 1
    nid = str(row.get("network_id", ""))
    if nid:
        b["ads"].add(nid)


def compute_stats(b: dict, total_spend: float) -> dict:
    s, r, c, i, cv = b["spend"], b["revenue"], b["clicks"], b["impressions"], b["conversions"]
    return {
        "spend":       s,
        "revenue":     r,
        "clicks":      c,
        "impressions": i,
        "conversions": cv,
        "ads":         len(b["ads"]),
        "profit":      r - s,
        "roas":        r / s   if s else 0,
        "cpc":         s / c   if c else 0,
        "ctr":         c / i * 100 if i else 0,
        "cvr":         cv / c * 100 if c else 0,
        "share":       s / total_spend * 100 if total_spend else 0,
    }


def aggregate(rows: list[dict], reclassify_nids: set[str]) -> dict:
    """Aggregate rows into overall/auto/home × dicer/non_dicer buckets."""
    tabs = {
        "overall": {"dicer": make_bucket(), "non_dicer": make_bucket()},
        "auto":    {"dicer": make_bucket(), "non_dicer": make_bucket()},
        "home":    {"dicer": make_bucket(), "non_dicer": make_bucket()},
    }
    reclassed_delta = make_bucket()

    for row in rows:
        d   = is_dicer(row, reclassify_nids)
        was = str(row.get("is_media_dicer", "0")) == "1" or row.get("is_dicer") is True
        key = "dicer" if d else "non_dicer"
        v   = str(row.get("vertical", "")).lower()

        add_to_bucket(tabs["overall"][key], row)
        if v in ("auto", "home"):
            add_to_bucket(tabs[v][key], row)
        if d and not was:
            add_to_bucket(reclassed_delta, row)

    result = {}
    for tab, buckets in tabs.items():
        ts = buckets["dicer"]["spend"] + buckets["non_dicer"]["spend"]
        result[tab] = {
            "dicer":         compute_stats(buckets["dicer"],     ts),
            "non_dicer":     compute_stats(buckets["non_dicer"], ts),
            "total_spend":   ts,
            "total_revenue": buckets["dicer"]["revenue"] + buckets["non_dicer"]["revenue"],
            "total_profit":  (buckets["dicer"]["revenue"] - buckets["dicer"]["spend"]) +
                             (buckets["non_dicer"]["revenue"] - buckets["non_dicer"]["spend"]),
            "total_ads":     len(buckets["dicer"]["ads"]) + len(buckets["non_dicer"]["ads"]),
        }

    result["correction"] = compute_stats(reclassed_delta,
                                         result["overall"]["total_spend"])
    result["correction"]["ads_count"] = len(reclassed_delta["ads"])
    return result


def top_ads(rows: list[dict], reclassify_nids: set[str],
            vertical: str | None = None, n: int = 5) -> list[dict]:
    """Return top N Dicer ads by spend for a given vertical (or all)."""
    by_ad: dict[str, dict] = defaultdict(
        lambda: {"spend": 0.0, "revenue": 0.0, "clicks": 0.0,
                 "impressions": 0.0, "conversions": 0.0, "thumb": "", "headline": ""}
    )
    for row in rows:
        if not is_dicer(row, reclassify_nids):
            continue
        v = str(row.get("vertical", "")).lower()
        if vertical and v != vertical:
            continue
        nid = str(row.get("network_id", ""))
        t   = by_ad[nid]
        t["spend"]       += float(row.get("spend") or 0)
        t["revenue"]     += float(row.get("revenue") or 0)
        t["clicks"]      += float(row.get("clicks") or 0)
        t["impressions"] += float(row.get("impressions") or 0)
        t["conversions"] += float(row.get("form_conversions") or 0)
        if not t["thumb"]:    t["thumb"]    = row.get("thumbnail_url", "")
        if not t["headline"]: t["headline"] = (row.get("headline") or "")[:70]

    ranked = sorted(by_ad.items(), key=lambda x: -x[1]["spend"])[:n]
    out = []
    for nid, t in ranked:
        s, r, c, i = t["spend"], t["revenue"], t["clicks"], t["impressions"]
        out.append({
            "nid":       nid,
            "spend":     s,
            "revenue":   r,
            "roas":      r / s   if s else 0,
            "cpc":       s / c   if c else 0,
            "ctr":       c / i * 100 if i else 0,
            "conv":      t["conversions"],
            "thumb":     t["thumb"],
            "headline":  t["headline"].replace("${region:capitalized}$", "[Region]")
                                      .replace("${region:capitalized}", "[Region]"),
        })
    return out


def fmt_money(v: float) -> str:
    return f"${v:,.0f}"

def fmt_pct(v: float, decimals: int = 1) -> str:
    return f"{v:.{decimals}f}%"

def fmt_roas(v: float) -> str:
    return f"{v:.2f}x"

def fmt_cpc(v: float) -> str:
    return f"${v:.2f}"

def roas_class(v: float) -> str:
    if v >= 1.4: return "rg"
    if v >= 1.1: return "ra"
    return "rr"


def render_scorecard(tag: str, roas_label: str, d: dict, hl_class: str = "hl") -> str:
    return f"""
    <div class="card {hl_class}">
      <div class="card-tag">{tag}</div>
      <div class="card-roas">{fmt_roas(d['roas'])}</div>
      <div class="card-roas-sub">{roas_label}</div>
      <div class="card-grid">
        <div class="cg-item"><div class="cg-label">Spend</div><div class="cg-val">{fmt_money(d['spend'])}</div></div>
        <div class="cg-item"><div class="cg-label">Revenue</div><div class="cg-val">{fmt_money(d['revenue'])}</div></div>
        <div class="cg-item"><div class="cg-label">CPC</div><div class="cg-val">{fmt_cpc(d['cpc'])}</div></div>
        <div class="cg-item"><div class="cg-label">CTR</div><div class="cg-val">{fmt_pct(d['ctr'],2)}</div></div>
        <div class="cg-item"><div class="cg-label">Conversions</div><div class="cg-val">{d['conversions']:,.0f}</div></div>
        <div class="cg-item"><div class="cg-label">Dicer Ads</div><div class="cg-val">{d['ads']}</div></div>
      </div>
    </div>"""


def render_ad_card(ad: dict, rank: int, badge_cls: str, rank_label: str = "") -> str:
    label = rank_label or f"#{rank}"
    badge_text = badge_cls.upper()
    roas_c = roas_class(ad["roas"])
    ctr_c  = "rg" if ad["ctr"] > 0.3 else ("ra" if ad["ctr"] > 0.1 else "rr")
    top_cls = f"top-{badge_cls}" if badge_cls != "overall" else "top"
    return f"""
    <div class="ad-card {top_cls}">
      <div class="ad-img-wrap">
        <img src="{ad['thumb']}" alt="Dicer Ad {rank}" loading="lazy"/>
        <div class="ad-overlay"></div>
        <div class="ad-rank">{label}</div>
        <div class="ad-dicer-badge {badge_cls}">{badge_text}</div>
      </div>
      <div class="ad-body">
        <div class="ad-headline">"{ad['headline']}"</div>
        <div class="ad-stats">
          <div class="as-item"><div class="as-label">ROAS</div><div class="as-val {roas_c}">{fmt_roas(ad['roas'])}</div></div>
          <div class="as-item"><div class="as-label">Spend</div><div class="as-val w">{fmt_money(ad['spend'])}</div></div>
          <div class="as-item"><div class="as-label">Revenue</div><div class="as-val w">{fmt_money(ad['revenue'])}</div></div>
          <div class="as-item"><div class="as-label">CTR</div><div class="as-val {ctr_c}">{fmt_pct(ad['ctr'],2)}</div></div>
          <div class="as-item"><div class="as-label">CPC</div><div class="as-val w">{fmt_cpc(ad['cpc'])}</div></div>
          <div class="as-item"><div class="as-label">Conv.</div><div class="as-val w">{ad['conv']:,.0f}</div></div>
        </div>
      </div>
    </div>"""


def generate_html(data: dict, top: dict, start: str, end: str, label: str,
                  correction: dict, misclass_count: int) -> str:
    ov  = data["overall"]
    au  = data["auto"]
    ho  = data["home"]
    d   = ov["dicer"]
    n   = ov["non_dicer"]
    corr = data["correction"]

    auto_d = au["dicer"]
    auto_n = au["non_dicer"]
    home_d = ho["dicer"]
    home_n = ho["non_dicer"]

    roas_delta_overall = d["roas"] - n["roas"]
    roas_delta_auto    = auto_d["roas"] - auto_n["roas"]
    roas_delta_home    = home_d["roas"] - home_n["roas"]
    home_underperform  = roas_delta_home < 0

    def badge_g(text): return f'<span class="badge g">{text}</span>'
    def badge_r(text): return f'<span class="badge r">{text}</span>'
    def badge_n_s(text): return f'<span class="badge n">{text}</span>'
    def badge_w(text): return f'<span class="badge warn">{text}</span>'
    def delta_roas(v):
        s = f"{v:+.2f}x"
        return badge_g(f"▲ {s}") if v >= 0 else badge_r(f"▼ {s}")
    def delta_cpc(d_cpc, n_cpc):
        pct = abs((d_cpc - n_cpc) / n_cpc * 100) if n_cpc else 0
        return badge_g(f"▼ {pct:.0f}% cheaper") if d_cpc < n_cpc else badge_r(f"▲ {pct:.0f}% more expensive")
    def delta_ctr(d_ctr, n_ctr):
        ratio = d_ctr / n_ctr if n_ctr else 0
        return badge_g(f"▲ {ratio:.1f}× higher") if ratio > 1 else badge_r(f"▼ {ratio:.1f}× lower")

    # Top ads HTML
    def ads_html(ads_list, badge_cls):
        html = ""
        labels = ["#1 — Top Spend", "#2", "#3 — Efficiency ⭐", "#4", "#5"]
        for i, ad in enumerate(ads_list[:5]):
            lbl = labels[i] if i < len(labels) else f"#{i+1}"
            if i == 2 and ad["roas"] >= 1.4:
                lbl = "#3 — Efficiency Star ⭐"
            html += render_ad_card(ad, i + 1, badge_cls, lbl)
        return html

    auto_ads_html = ads_html(top.get("auto", []), "auto")
    home_ads_html = ads_html(top.get("home", []), "home")
    overall_ads_html = ads_html(top.get("overall", []), "overall")

    home_alert_html = ""
    if home_underperform:
        home_alert_html = f"""
  <div class="home-alert">
    <div class="home-alert-icon">⚠️</div>
    <div class="home-alert-text">
      <strong>Dicer underperforms in Home this period.</strong>
      Non-Dicer is winning on ROAS ({fmt_roas(home_n['roas'])} vs {fmt_roas(home_d['roas'])}) and
      Dicer holds only <strong>{fmt_pct(home_d['share'])} spend share</strong> in this vertical.
      Only the top creative is profitable. Review home creative strategy.
    </div>
  </div>"""

    raw_dicer_spend = d["spend"] - corr["spend"]
    raw_dicer_share = raw_dicer_spend / ov["total_spend"] * 100 if ov["total_spend"] else 0

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dicer.ai × Otto — {label} Performance Report</title>
<style>
  :root {{
    --dicer:#7C5CF6;--dicer-light:rgba(124,92,246,0.1);--dicer-glow:rgba(124,92,246,0.22);
    --green:#10B981;--green-bg:rgba(16,185,129,0.12);
    --red:#F87171;--red-bg:rgba(248,113,113,0.12);
    --amber:#FBBF24;--amber-bg:rgba(251,191,36,0.12);
    --bg:#0C0C12;--surface:#15151E;--surface2:#1C1C28;--border:#252535;
    --text:#EEEEF5;--muted:#7070A0;
    --auto:#3B82F6;--auto-bg:rgba(59,130,246,0.1);
    --home:#10B981;--home-bg:rgba(16,185,129,0.1);
  }}
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',sans-serif;background:var(--bg);color:var(--text);}}
  .hero{{background:linear-gradient(160deg,#12122A 0%,#0E1A3A 60%,#1A0E3A 100%);border-bottom:1px solid var(--border);padding:40px 56px 36px;position:relative;overflow:hidden;}}
  .hero::after{{content:'';position:absolute;top:-80px;right:-80px;width:400px;height:400px;background:radial-gradient(circle,rgba(124,92,246,0.1) 0%,transparent 65%);pointer-events:none;}}
  .hero-top{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:28px;flex-wrap:wrap;gap:16px;}}
  .logo-group{{display:flex;align-items:center;gap:14px;}}
  .logo-mark{{width:48px;height:48px;background:var(--dicer);border-radius:14px;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:18px;color:#fff;box-shadow:0 4px 20px var(--dicer-glow);}}
  .logo-name{{font-size:24px;font-weight:800;color:#fff;letter-spacing:-0.5px;}}
  .logo-sub{{font-size:12px;color:var(--muted);margin-top:2px;}}
  .report-info{{text-align:right;}}
  .report-label{{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);}}
  .report-period{{font-size:22px;font-weight:800;color:#fff;margin-top:4px;letter-spacing:-0.5px;}}
  .report-sub{{font-size:12px;color:var(--muted);margin-top:3px;}}
  .hero-divider{{height:1px;background:linear-gradient(90deg,transparent,rgba(124,92,246,0.5),transparent);margin:0 0 28px;}}
  .hero-kpis{{display:flex;gap:40px;flex-wrap:wrap;}}
  .hero-kpi{{display:flex;flex-direction:column;gap:3px;}}
  .hero-kpi-label{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--muted);}}
  .hero-kpi-val{{font-size:30px;font-weight:900;letter-spacing:-1.5px;color:#fff;line-height:1;}}
  .hero-kpi-val.g{{color:#34D399;}}.hero-kpi-val.p{{color:#C4B5FD;}}.hero-kpi-val.w{{color:#FBBF24;}}
  .hero-kpi-sub{{font-size:11px;color:var(--muted);}}
  .attr-banner{{background:linear-gradient(135deg,rgba(251,191,36,0.08),rgba(251,191,36,0.03));border-top:1px solid rgba(251,191,36,0.25);border-bottom:1px solid rgba(251,191,36,0.25);padding:12px 56px;display:flex;align-items:center;gap:12px;}}
  .attr-banner-text{{font-size:12px;color:rgba(251,191,36,0.8);line-height:1.5;}}
  .attr-banner-text strong{{color:#FBBF24;}}
  .tabs-bar{{display:flex;gap:0;border-bottom:1px solid var(--border);background:var(--surface);padding:0 56px;}}
  .tab-btn{{padding:16px 28px;font-size:13px;font-weight:700;color:var(--muted);cursor:pointer;border:none;background:transparent;border-bottom:3px solid transparent;transition:all .2s;display:flex;align-items:center;gap:8px;margin-bottom:-1px;}}
  .tab-btn:hover{{color:var(--text);}}
  .tab-btn.active{{color:#fff;border-bottom-color:var(--dicer);}}
  .tab-btn.active.auto-tab{{border-bottom-color:var(--auto);color:#93C5FD;}}
  .tab-btn.active.home-tab{{border-bottom-color:var(--home);color:#6EE7B7;}}
  .tab-pill{{font-size:10px;font-weight:800;padding:2px 7px;border-radius:10px;text-transform:uppercase;letter-spacing:0.5px;}}
  .tab-btn.active .tab-pill{{background:var(--dicer);color:#fff;}}
  .tab-btn.active.auto-tab .tab-pill{{background:var(--auto);color:#fff;}}
  .tab-btn.active.home-tab .tab-pill{{background:var(--home);color:#fff;}}
  .tab-btn:not(.active) .tab-pill{{background:var(--surface2);color:var(--muted);}}
  .tab-pane{{display:none;}}.tab-pane.active{{display:block;}}
  .wrap{{max-width:1160px;margin:0 auto;padding:44px 56px 80px;}}
  .section-head{{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:2px;color:var(--muted);margin-bottom:18px;display:flex;align-items:center;gap:10px;}}
  .section-head::after{{content:'';flex:1;height:1px;background:var(--border);}}
  .sh-pill{{font-size:10px;font-weight:800;padding:2px 8px;border-radius:8px;text-transform:uppercase;}}
  .sh-pill.auto{{background:var(--auto-bg);color:var(--auto);}}.sh-pill.home{{background:var(--home-bg);color:var(--home);}}
  .cards3{{display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-bottom:44px;}}
  .card{{background:var(--surface);border:1px solid var(--border);border-radius:18px;padding:26px;position:relative;overflow:hidden;}}
  .card.hl{{border-color:var(--dicer);background:linear-gradient(135deg,var(--surface) 0%,var(--dicer-light) 100%);}}
  .card.hl::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#7C5CF6,#B28FFF);}}
  .card.hl-auto{{border-color:var(--auto);background:linear-gradient(135deg,var(--surface) 0%,var(--auto-bg) 100%);}}
  .card.hl-auto::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#3B82F6,#93C5FD);}}
  .card.hl-home{{border-color:var(--home);background:linear-gradient(135deg,var(--surface) 0%,var(--home-bg) 100%);}}
  .card.hl-home::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#10B981,#6EE7B7);}}
  .card-tag{{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin-bottom:14px;}}
  .card.hl .card-tag{{color:#A78BFA;}}.card.hl-auto .card-tag{{color:#93C5FD;}}.card.hl-home .card-tag{{color:#6EE7B7;}}
  .card-roas{{font-size:52px;font-weight:900;letter-spacing:-3px;color:#fff;line-height:1;}}
  .card.hl .card-roas{{color:#C4B5FD;}}.card.hl-auto .card-roas{{color:#93C5FD;}}.card.hl-home .card-roas{{color:#6EE7B7;}}
  .card-roas-sub{{font-size:12px;color:var(--muted);margin:4px 0 18px;}}
  .card-grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;border-top:1px solid var(--border);padding-top:18px;}}
  .cg-item{{display:flex;flex-direction:column;gap:2px;}}
  .cg-label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;}}
  .cg-val{{font-size:16px;font-weight:800;color:#fff;}}
  .tbl-wrap{{background:var(--surface);border:1px solid var(--border);border-radius:18px;overflow:hidden;margin-bottom:44px;}}
  table{{width:100%;border-collapse:collapse;}}
  thead tr{{background:var(--surface2);}}
  th{{padding:13px 20px;font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:1px;color:var(--muted);text-align:left;}}
  th.dc{{color:#A78BFA;}}th.dc-auto{{color:#93C5FD;}}th.dc-home{{color:#6EE7B7;}}
  tbody tr{{border-top:1px solid var(--border);transition:background .15s;}}
  tbody tr:hover{{background:var(--surface2);}}
  td{{padding:13px 20px;font-size:14px;}}
  td.mn{{font-size:13px;color:var(--muted);font-weight:500;}}
  td.dv{{font-weight:800;color:#C4B5FD;}}td.dv-auto{{font-weight:800;color:#93C5FD;}}td.dv-home{{font-weight:800;color:#6EE7B7;}}
  .badge{{display:inline-flex;align-items:center;padding:3px 9px;border-radius:8px;font-size:11px;font-weight:800;}}
  .badge.g{{background:var(--green-bg);color:var(--green);}}.badge.r{{background:var(--red-bg);color:var(--red);}}
  .badge.n{{background:rgba(255,255,255,0.06);color:var(--muted);}}.badge.warn{{background:var(--amber-bg);color:var(--amber);}}
  .spend-bar-box{{background:var(--surface);border:1px solid var(--border);border-radius:18px;padding:26px 30px;margin-bottom:44px;}}
  .spend-bar-title{{font-size:15px;font-weight:700;color:#fff;margin-bottom:20px;}}
  .bar-track{{height:36px;background:var(--surface2);border-radius:10px;overflow:hidden;display:flex;}}
  .bar-d{{display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:800;color:#fff;}}
  .bar-d.overall{{background:linear-gradient(90deg,#6C47FF,#9B6FFF);}}.bar-d.auto{{background:linear-gradient(90deg,#1D4ED8,#3B82F6);}}.bar-d.home{{background:linear-gradient(90deg,#059669,#10B981);}}
  .bar-n{{background:rgba(255,255,255,0.12);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:rgba(255,255,255,0.5);flex:1;}}
  .bar-legend{{display:flex;gap:24px;margin-top:16px;flex-wrap:wrap;align-items:center;}}
  .bl-item{{display:flex;align-items:center;gap:8px;font-size:13px;}}
  .bl-dot{{width:12px;height:12px;border-radius:3px;}}
  .bl-dot.overall{{background:var(--dicer);}}.bl-dot.auto{{background:var(--auto);}}.bl-dot.home{{background:var(--home);}}.bl-dot.n{{background:rgba(255,255,255,0.2);}}
  .bl-text{{color:var(--muted);}}.bl-val{{font-weight:700;color:#fff;margin-left:4px;}}
  .correction-box{{background:var(--surface);border:1px solid rgba(251,191,36,0.3);border-radius:18px;padding:26px 30px;margin-bottom:44px;}}
  .correction-title{{font-size:15px;font-weight:700;color:#fff;margin-bottom:20px;}}
  .correction-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:16px;}}
  .cbox{{background:var(--surface2);border-radius:12px;padding:16px 18px;}}
  .cbox-label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px;}}
  .cbox-val{{font-size:22px;font-weight:900;color:#FBBF24;letter-spacing:-1px;}}
  .cbox-sub{{font-size:11px;color:var(--muted);margin-top:3px;}}
  .correction-note{{font-size:12px;color:var(--muted);line-height:1.65;}}
  .correction-note code{{color:#A78BFA;background:rgba(124,92,246,0.1);padding:1px 5px;border-radius:4px;}}
  .ads-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:22px;margin-bottom:44px;}}
  .ad-card{{background:var(--surface);border:1px solid var(--border);border-radius:18px;overflow:hidden;transition:transform .2s,box-shadow .2s;}}
  .ad-card:hover{{transform:translateY(-4px);box-shadow:0 16px 48px var(--dicer-glow);}}
  .ad-card.top{{border-color:var(--dicer);}}.ad-card.top::before,.ad-card.top-auto::before,.ad-card.top-home::before{{content:'';display:block;height:3px;}}
  .ad-card.top::before{{background:linear-gradient(90deg,#6C47FF,#B28FFF);}}.ad-card.top-auto{{border-color:var(--auto);}}.ad-card.top-auto::before{{background:linear-gradient(90deg,#1D4ED8,#3B82F6);}}.ad-card.top-home{{border-color:var(--home);}}.ad-card.top-home::before{{background:linear-gradient(90deg,#059669,#10B981);}}
  .ad-img-wrap{{position:relative;background:var(--surface2);overflow:hidden;}}
  .ad-img-wrap img{{width:100%;display:block;aspect-ratio:4/3;object-fit:cover;}}
  .ad-overlay{{position:absolute;inset:0;background:linear-gradient(to bottom,transparent 50%,rgba(0,0,0,0.7) 100%);}}
  .ad-rank{{position:absolute;top:10px;left:10px;background:rgba(0,0,0,0.8);backdrop-filter:blur(8px);color:#fff;font-size:10px;font-weight:800;padding:4px 10px;border-radius:20px;text-transform:uppercase;}}
  .ad-dicer-badge{{position:absolute;top:10px;right:10px;color:#fff;font-size:10px;font-weight:700;padding:3px 8px;border-radius:6px;}}
  .ad-dicer-badge.overall{{background:var(--dicer);}}.ad-dicer-badge.auto{{background:var(--auto);}}.ad-dicer-badge.home{{background:var(--home);}}
  .ad-body{{padding:16px 18px;}}.ad-headline{{font-size:12px;color:var(--muted);margin-bottom:10px;font-style:italic;line-height:1.4;}}
  .ad-stats{{display:grid;grid-template-columns:1fr 1fr;gap:10px;}}
  .as-item{{display:flex;flex-direction:column;gap:2px;}}
  .as-label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.5px;}}
  .as-val{{font-size:16px;font-weight:900;}}.as-val.rg{{color:#34D399;}}.as-val.ra{{color:#FBBF24;}}.as-val.rr{{color:#F87171;}}.as-val.w{{color:#fff;}}
  .home-alert{{background:var(--amber-bg);border:1px solid rgba(251,191,36,0.3);border-radius:14px;padding:20px 24px;margin-bottom:44px;display:flex;gap:14px;align-items:flex-start;}}
  .home-alert-icon{{font-size:22px;flex-shrink:0;}}.home-alert-text{{font-size:13px;color:rgba(251,191,36,0.85);line-height:1.7;}}.home-alert-text strong{{color:#FBBF24;}}
  .footer{{margin-top:60px;padding-top:24px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;}}
  .footer-brand{{font-size:13px;color:var(--muted);}}.footer-brand strong{{color:var(--dicer);}}.footer-note{{font-size:11px;color:var(--muted);}}
  @media(max-width:768px){{.hero,.wrap,.attr-banner,.tabs-bar{{padding-left:20px;padding-right:20px;}}.cards3,.ads-grid,.correction-grid{{grid-template-columns:1fr;}}.tab-btn{{padding:12px 14px;font-size:12px;}}}}
</style>
</head>
<body>
<div class="hero">
  <div class="hero-top">
    <div class="logo-group">
      <div class="logo-mark">Di</div>
      <div><div class="logo-name">Dicer.ai</div><div class="logo-sub">AI-Powered Creative Intelligence Platform</div></div>
    </div>
    <div class="report-info">
      <div class="report-label">Performance Report</div>
      <div class="report-period">{label}</div>
      <div class="report-sub">Otto Auto &amp; Home Insurance · Native · Attribution-Corrected · Direct Otto API</div>
    </div>
  </div>
  <div class="hero-divider"></div>
  <div class="hero-kpis">
    <div class="hero-kpi"><div class="hero-kpi-label">Dicer ROAS (Overall)</div><div class="hero-kpi-val g">{fmt_roas(d['roas'])}</div><div class="hero-kpi-sub">{roas_delta_overall:+.2f}x vs Non-Dicer ▲</div></div>
    <div class="hero-kpi"><div class="hero-kpi-label">Total Dicer Spend</div><div class="hero-kpi-val p">{fmt_money(d['spend'])}</div><div class="hero-kpi-sub">{fmt_pct(d['share'])} of portfolio</div></div>
    <div class="hero-kpi"><div class="hero-kpi-label">Portfolio Spend</div><div class="hero-kpi-val p">{fmt_money(ov['total_spend'])}</div><div class="hero-kpi-sub">{fmt_money(ov['total_revenue'])} revenue</div></div>
    <div class="hero-kpi"><div class="hero-kpi-label">Auto ROAS (Dicer)</div><div class="hero-kpi-val g">{fmt_roas(auto_d['roas'])}</div><div class="hero-kpi-sub">vs {fmt_roas(auto_n['roas'])} Non-Dicer</div></div>
    <div class="hero-kpi"><div class="hero-kpi-label">Home ROAS (Dicer)</div><div class="hero-kpi-val {'g' if not home_underperform else 'w'}">{fmt_roas(home_d['roas'])}</div><div class="hero-kpi-sub">vs {fmt_roas(home_n['roas'])} Non-Dicer</div></div>
  </div>
</div>
<div class="attr-banner">
  <span style="font-size:16px">⚠️</span>
  <div class="attr-banner-text">
    <strong>Attribution-corrected.</strong> {corr['ads_count']} misattributed creatives reclassified Non-Dicer → Dicer via <strong>attribution-check.dicer.ai</strong>. Adds <strong>+{fmt_money(corr['spend'])} spend</strong> to Dicer. Raw baseline: {fmt_money(raw_dicer_spend)} ({fmt_pct(raw_dicer_share)}).
  </div>
</div>
<div class="tabs-bar">
  <button class="tab-btn active" onclick="switchTab('overall',this)">🌐 Overall <span class="tab-pill">All Verticals</span></button>
  <button class="tab-btn auto-tab" onclick="switchTab('auto',this)">🚗 Otto Auto <span class="tab-pill">Auto</span></button>
  <button class="tab-btn home-tab" onclick="switchTab('home',this)">🏠 Otto Home <span class="tab-pill">Home</span></button>
</div>

<div id="tab-overall" class="tab-pane active"><div class="wrap">
  <div class="section-head">Performance Scorecards — All Verticals</div>
  <div class="cards3">
    {render_scorecard("🎯 Dicer.ai — All", "Return on Ad Spend", d, "hl")}
    {render_scorecard("Non-Dicer — All", "Return on Ad Spend", n, "")}
    <div class="card"><div class="card-tag">📊 Portfolio Total</div><div class="card-roas">{fmt_roas(ov['total_revenue']/ov['total_spend'] if ov['total_spend'] else 0)}</div><div class="card-roas-sub">Blended ROAS</div><div class="card-grid">
      <div class="cg-item"><div class="cg-label">Total Spend</div><div class="cg-val">{fmt_money(ov['total_spend'])}</div></div>
      <div class="cg-item"><div class="cg-label">Total Revenue</div><div class="cg-val">{fmt_money(ov['total_revenue'])}</div></div>
      <div class="cg-item"><div class="cg-label">Net Profit</div><div class="cg-val">{fmt_money(ov['total_profit'])}</div></div>
      <div class="cg-item"><div class="cg-label">Total Ads</div><div class="cg-val">{ov['total_ads']}</div></div>
      <div class="cg-item"><div class="cg-label">Dicer Ads</div><div class="cg-val">{d['ads']}</div></div>
      <div class="cg-item"><div class="cg-label">Dicer Share</div><div class="cg-val">{fmt_pct(d['share'])}</div></div>
    </div></div>
  </div>
  <div class="section-head">Head-to-Head — Overall</div>
  <div class="tbl-wrap"><table>
    <thead><tr><th>Metric</th><th class="dc">🎯 Dicer.ai</th><th>Non-Dicer</th><th>Delta</th></tr></thead>
    <tbody>
      <tr><td class="mn">ROAS</td><td class="dv">{fmt_roas(d['roas'])}</td><td>{fmt_roas(n['roas'])}</td><td>{delta_roas(roas_delta_overall)}</td></tr>
      <tr><td class="mn">CPC</td><td class="dv">{fmt_cpc(d['cpc'])}</td><td>{fmt_cpc(n['cpc'])}</td><td>{delta_cpc(d['cpc'],n['cpc'])}</td></tr>
      <tr><td class="mn">CTR</td><td class="dv">{fmt_pct(d['ctr'],2)}</td><td>{fmt_pct(n['ctr'],2)}</td><td>{delta_ctr(d['ctr'],n['ctr'])}</td></tr>
      <tr><td class="mn">Spend</td><td class="dv">{fmt_money(d['spend'])}</td><td>{fmt_money(n['spend'])}</td><td>{badge_n_s(fmt_pct(d['share']) + ' share')}</td></tr>
      <tr><td class="mn">Revenue</td><td class="dv">{fmt_money(d['revenue'])}</td><td>{fmt_money(n['revenue'])}</td><td>{badge_g('+' + fmt_money(d['profit']) + ' profit')}</td></tr>
      <tr><td class="mn">Conversions</td><td class="dv">{d['conversions']:,.0f}</td><td>{n['conversions']:,.0f}</td><td>{badge_n_s(fmt_pct(d['conversions']/(d['conversions']+n['conversions'])*100) + ' of total')}</td></tr>
      <tr><td class="mn">CVR</td><td class="dv">{fmt_pct(d['cvr'],1)}</td><td>{fmt_pct(n['cvr'],1)}</td><td>{delta_roas(d['cvr']-n['cvr']).replace('x','pp')}</td></tr>
    </tbody>
  </table></div>
  <div class="section-head">Budget Allocation</div>
  <div class="spend-bar-box">
    <div class="spend-bar-title">Spend Distribution — {fmt_money(ov['total_spend'])} total ({start} → {end})</div>
    <div class="bar-track"><div class="bar-d overall" style="width:{d['share']:.1f}%">Dicer {fmt_pct(d['share'])}</div><div class="bar-n">Non-Dicer {fmt_pct(n['share'])}</div></div>
    <div class="bar-legend">
      <div class="bl-item"><div class="bl-dot overall"></div><span class="bl-text">Dicer.ai (corrected)</span><span class="bl-val">{fmt_money(d['spend'])}</span></div>
      <div class="bl-item"><div class="bl-dot n"></div><span class="bl-text">Non-Dicer</span><span class="bl-val">{fmt_money(n['spend'])}</span></div>
      <div class="bl-item" style="margin-left:auto"><span class="bl-text">⚡ Dicer:</span><span class="bl-val" style="color:#34D399">{d['ctr']/n['ctr']:.1f}× more clicks per impression dollar</span></div>
    </div>
  </div>
  <div class="section-head">Attribution Correction</div>
  <div class="correction-box">
    <div class="correction-title">🔧 Misclassified Creatives Reclassified → Dicer</div>
    <div class="correction-grid">
      <div class="cbox"><div class="cbox-label">Ads Reclassified</div><div class="cbox-val">{corr['ads_count']}</div><div class="cbox-sub">of {misclass_count} misclassified URLs checked</div></div>
      <div class="cbox"><div class="cbox-label">Spend Moved to Dicer</div><div class="cbox-val">+{fmt_money(corr['spend'])}</div><div class="cbox-sub">{fmt_money(raw_dicer_spend)} → {fmt_money(d['spend'])}</div></div>
      <div class="cbox"><div class="cbox-label">Share Corrected</div><div class="cbox-val">+{d['share']-raw_dicer_share:.1f}pp</div><div class="cbox-sub">{fmt_pct(raw_dicer_share)} → {fmt_pct(d['share'])}</div></div>
      <div class="cbox"><div class="cbox-label">Clicks Moved</div><div class="cbox-val">{corr['clicks']:,.0f}</div><div class="cbox-sub">+{corr['conversions']:,.0f} conversions</div></div>
    </div>
    <div class="correction-note">Source: <code>attribution-check.dicer.ai/dicer_misclassified.json</code> · URL inconsistency + visual similarity API matching.</div>
  </div>
  <div class="section-head">🎯 Top Dicer Creatives — All Verticals</div>
  <div class="ads-grid">{overall_ads_html}</div>
  <div class="footer">
    <div class="footer-brand">Generated by <strong>Dicer.ai</strong> · Source: Otto API + attribution-check.dicer.ai · Generated {date.today().isoformat()}</div>
    <div class="footer-note">{ov['total_ads']} ads · {d['ads']} Dicer / {n['ads']} Non-Dicer · {start} → {end}</div>
  </div>
</div></div>

<div id="tab-auto" class="tab-pane"><div class="wrap">
  <div class="section-head">Performance Scorecards <span class="sh-pill auto">Otto Auto Insurance</span></div>
  <div class="cards3">
    {render_scorecard("🚗 Dicer.ai — Auto", "ROAS · Auto vertical", auto_d, "hl-auto")}
    {render_scorecard("Non-Dicer — Auto", "ROAS · Auto vertical", auto_n, "")}
    <div class="card"><div class="card-tag">🚗 Auto Total</div><div class="card-roas">{fmt_roas(au['total_revenue']/au['total_spend'] if au['total_spend'] else 0)}</div><div class="card-roas-sub">Blended · auto vertical</div><div class="card-grid">
      <div class="cg-item"><div class="cg-label">Total Spend</div><div class="cg-val">{fmt_money(au['total_spend'])}</div></div>
      <div class="cg-item"><div class="cg-label">Total Revenue</div><div class="cg-val">{fmt_money(au['total_revenue'])}</div></div>
      <div class="cg-item"><div class="cg-label">Net Profit</div><div class="cg-val">{fmt_money(au['total_profit'])}</div></div>
      <div class="cg-item"><div class="cg-label">Total Ads</div><div class="cg-val">{au['total_ads']}</div></div>
      <div class="cg-item"><div class="cg-label">Dicer Share</div><div class="cg-val">{fmt_pct(auto_d['share'])}</div></div>
      <div class="cg-item"><div class="cg-label">% of Portfolio</div><div class="cg-val">{fmt_pct(au['total_spend']/ov['total_spend']*100 if ov['total_spend'] else 0)}</div></div>
    </div></div>
  </div>
  <div class="section-head">Head-to-Head <span class="sh-pill auto">Auto</span></div>
  <div class="tbl-wrap"><table>
    <thead><tr><th>Metric</th><th class="dc-auto">🚗 Dicer Auto</th><th>Non-Dicer Auto</th><th>Delta</th></tr></thead>
    <tbody>
      <tr><td class="mn">ROAS</td><td class="dv-auto">{fmt_roas(auto_d['roas'])}</td><td>{fmt_roas(auto_n['roas'])}</td><td>{delta_roas(roas_delta_auto)}</td></tr>
      <tr><td class="mn">CPC</td><td class="dv-auto">{fmt_cpc(auto_d['cpc'])}</td><td>{fmt_cpc(auto_n['cpc'])}</td><td>{delta_cpc(auto_d['cpc'],auto_n['cpc'])}</td></tr>
      <tr><td class="mn">CTR</td><td class="dv-auto">{fmt_pct(auto_d['ctr'],2)}</td><td>{fmt_pct(auto_n['ctr'],2)}</td><td>{delta_ctr(auto_d['ctr'],auto_n['ctr'])}</td></tr>
      <tr><td class="mn">Spend</td><td class="dv-auto">{fmt_money(auto_d['spend'])}</td><td>{fmt_money(auto_n['spend'])}</td><td>{badge_n_s(fmt_pct(auto_d['share']) + ' share')}</td></tr>
      <tr><td class="mn">Revenue</td><td class="dv-auto">{fmt_money(auto_d['revenue'])}</td><td>{fmt_money(auto_n['revenue'])}</td><td>{badge_g('+' + fmt_money(auto_d['profit']) + ' profit')}</td></tr>
      <tr><td class="mn">Conversions</td><td class="dv-auto">{auto_d['conversions']:,.0f}</td><td>{auto_n['conversions']:,.0f}</td><td>{badge_n_s(fmt_pct(auto_d['conversions']/(auto_d['conversions']+auto_n['conversions'])*100 if (auto_d['conversions']+auto_n['conversions']) else 0) + ' of auto total')}</td></tr>
    </tbody>
  </table></div>
  <div class="section-head">Budget Allocation <span class="sh-pill auto">Auto</span></div>
  <div class="spend-bar-box">
    <div class="spend-bar-title">Auto Spend Distribution — {fmt_money(au['total_spend'])} total</div>
    <div class="bar-track"><div class="bar-d auto" style="width:{auto_d['share']:.1f}%">Dicer {fmt_pct(auto_d['share'])}</div><div class="bar-n">Non-Dicer {fmt_pct(auto_n['share'])}</div></div>
    <div class="bar-legend">
      <div class="bl-item"><div class="bl-dot auto"></div><span class="bl-text">Dicer Auto</span><span class="bl-val">{fmt_money(auto_d['spend'])}</span></div>
      <div class="bl-item"><div class="bl-dot n"></div><span class="bl-text">Non-Dicer Auto</span><span class="bl-val">{fmt_money(auto_n['spend'])}</span></div>
    </div>
  </div>
  <div class="section-head">🎯 Top Dicer Auto Creatives</div>
  <div class="ads-grid">{auto_ads_html}</div>
  <div class="footer">
    <div class="footer-brand">Generated by <strong>Dicer.ai</strong> · Otto Auto Insurance · {start} → {end}</div>
    <div class="footer-note">{au['total_ads']} ads · {auto_d['ads']} Dicer / {auto_n['ads']} Non-Dicer · Direct Otto API</div>
  </div>
</div></div>

<div id="tab-home" class="tab-pane"><div class="wrap">
  <div class="section-head">Performance Scorecards <span class="sh-pill home">Otto Home Insurance</span></div>
  {home_alert_html}
  <div class="cards3">
    {render_scorecard("🏠 Dicer.ai — Home", "ROAS · Home vertical", home_d, "hl-home")}
    {render_scorecard("Non-Dicer — Home", "ROAS · Home vertical", home_n, "")}
    <div class="card"><div class="card-tag">🏠 Home Total</div><div class="card-roas">{fmt_roas(ho['total_revenue']/ho['total_spend'] if ho['total_spend'] else 0)}</div><div class="card-roas-sub">Blended · home vertical</div><div class="card-grid">
      <div class="cg-item"><div class="cg-label">Total Spend</div><div class="cg-val">{fmt_money(ho['total_spend'])}</div></div>
      <div class="cg-item"><div class="cg-label">Total Revenue</div><div class="cg-val">{fmt_money(ho['total_revenue'])}</div></div>
      <div class="cg-item"><div class="cg-label">Net Profit</div><div class="cg-val">{fmt_money(ho['total_profit'])}</div></div>
      <div class="cg-item"><div class="cg-label">Total Ads</div><div class="cg-val">{ho['total_ads']}</div></div>
      <div class="cg-item"><div class="cg-label">Dicer Share</div><div class="cg-val">{fmt_pct(home_d['share'])}</div></div>
      <div class="cg-item"><div class="cg-label">% of Portfolio</div><div class="cg-val">{fmt_pct(ho['total_spend']/ov['total_spend']*100 if ov['total_spend'] else 0)}</div></div>
    </div></div>
  </div>
  <div class="section-head">Head-to-Head <span class="sh-pill home">Home</span></div>
  <div class="tbl-wrap"><table>
    <thead><tr><th>Metric</th><th class="dc-home">🏠 Dicer Home</th><th>Non-Dicer Home</th><th>Delta</th></tr></thead>
    <tbody>
      <tr><td class="mn">ROAS</td><td class="dv-home">{fmt_roas(home_d['roas'])}</td><td>{fmt_roas(home_n['roas'])}</td><td>{delta_roas(roas_delta_home)}</td></tr>
      <tr><td class="mn">CPC</td><td class="dv-home">{fmt_cpc(home_d['cpc'])}</td><td>{fmt_cpc(home_n['cpc'])}</td><td>{delta_cpc(home_d['cpc'],home_n['cpc'])}</td></tr>
      <tr><td class="mn">CTR</td><td class="dv-home">{fmt_pct(home_d['ctr'],2)}</td><td>{fmt_pct(home_n['ctr'],2)}</td><td>{delta_ctr(home_d['ctr'],home_n['ctr'])}</td></tr>
      <tr><td class="mn">Spend</td><td class="dv-home">{fmt_money(home_d['spend'])}</td><td>{fmt_money(home_n['spend'])}</td><td>{badge_w(fmt_pct(home_d['share']) + ' share only')}</td></tr>
      <tr><td class="mn">Revenue</td><td class="dv-home">{fmt_money(home_d['revenue'])}</td><td>{fmt_money(home_n['revenue'])}</td><td>{badge_g('+' + fmt_money(home_d['profit']) + ' profit') if home_d['profit'] > 0 else badge_r(fmt_money(home_d['profit']) + ' loss')}</td></tr>
      <tr><td class="mn">Conversions</td><td class="dv-home">{home_d['conversions']:,.0f}</td><td>{home_n['conversions']:,.0f}</td><td>{badge_r(fmt_pct(home_d['conversions']/(home_d['conversions']+home_n['conversions'])*100 if (home_d['conversions']+home_n['conversions']) else 0) + ' of home total')}</td></tr>
    </tbody>
  </table></div>
  <div class="section-head">🏠 Top Dicer Home Creatives</div>
  <div class="ads-grid">{home_ads_html}</div>
  <div class="footer">
    <div class="footer-brand">Generated by <strong>Dicer.ai</strong> · Otto Home Insurance · {start} → {end}</div>
    <div class="footer-note">{ho['total_ads']} ads · {home_d['ads']} Dicer / {home_n['ads']} Non-Dicer · Direct Otto API</div>
  </div>
</div></div>

<script>
function switchTab(name,btn){{
  document.querySelectorAll('.tab-pane').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  btn.classList.add('active');
}}
</script>
</body></html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate Dicer × Otto performance report")
    parser.add_argument("--start",          default=None, help="Start date YYYY-MM-DD (default: 7 days ago)")
    parser.add_argument("--end",            default=None, help="End date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--label",          default=None, help="Report label, e.g. 'February 2026'")
    parser.add_argument("--out",            default=None, help="Output HTML file path")
    parser.add_argument("--no-attribution", action="store_true", help="Skip attribution correction")
    args = parser.parse_args()

    today     = date.today()
    end_date  = args.end   or (today - timedelta(days=1)).isoformat()
    start_date = args.start or (today - timedelta(days=7)).isoformat()
    label     = args.label  or f"{start_date} – {end_date}"

    print(f"\n📊 Dicer × Otto Report Generator")
    print(f"   Period: {start_date} → {end_date}  ({label})")

    print("\n[1/4] Fetching Otto API data...")
    rows = fetch_otto(start_date, end_date)

    print("\n[2/4] Fetching attribution corrections...")
    if args.no_attribution:
        misclass_urls = set()
        print("  Skipped (--no-attribution)")
    else:
        misclass_urls = fetch_misclassified()

    print("\n[3/4] Building reclassification map...")
    reclassify_nids = build_reclassify_nids(rows, misclass_urls)

    print("\n[4/4] Aggregating and rendering...")
    data  = aggregate(rows, reclassify_nids)
    t_ads = {
        "overall": top_ads(rows, reclassify_nids, None, 5),
        "auto":    top_ads(rows, reclassify_nids, "auto", 5),
        "home":    top_ads(rows, reclassify_nids, "home", 5),
    }
    html = generate_html(data, t_ads, start_date, end_date, label,
                         data["correction"], len(misclass_urls))

    # Determine output path
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.out:
        out_path = Path(args.out)
    else:
        slug = end_date  # e.g. 2026-03-01
        out_path = REPORTS_DIR / f"otto-performance-{slug}.html"

    out_path.write_text(html, encoding="utf-8")
    print(f"\n✅ Report written: {out_path}")

    # Always update index.html to latest
    INDEX_FILE.write_text(html, encoding="utf-8")
    print(f"✅ Index updated:  {INDEX_FILE}")

    # Print summary
    d = data["overall"]["dicer"]
    n = data["overall"]["non_dicer"]
    print(f"\n{'─'*50}")
    print(f"  Dicer ROAS:   {d['roas']:.2f}x  (Non-Dicer: {n['roas']:.2f}x)")
    print(f"  Dicer Spend:  ${d['spend']:,.0f}  ({d['share']:.1f}%)")
    print(f"  Total:        ${data['overall']['total_spend']:,.0f}")
    print(f"{'─'*50}\n")


if __name__ == "__main__":
    main()
