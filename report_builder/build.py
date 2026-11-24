"""Build the Prudential Hong Kong competitive-intelligence report + Vercel app from the Chris Crozier / Emma template.

Usage: python -I report_app/prudential/build.py
Outputs:
  vercel_prudential_report/      (deployable app: api/, backend.py, private/report.html, private/corpus.md, ...)
  Prudential/Prudential_HK_Competitive_Intelligence_Report.html   (local copy of the report page)
Inputs:
  report_app/prudential/{body.html,data.js,system_prompt.txt}
  Prudential/ci-monitor/data/ci.db          (counts are computed here, never typed by hand)
  Prudential/_report_work/hk_financials.md  (HK-segment financial extraction)
  Prudential/Prudential_HK_newsroom.xlsx    (newsroom_articles sheet only; the contacts sheet is never read)
"""
import html as html_lib
import json
import re
import shutil
import sqlite3
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
TPL_DIR = ROOT / "vercel_report"
OUT = ROOT / "vercel_prudential_report"
PRU = ROOT / "Prudential"
DB = PRU / "ci-monitor/data/ci.db"
WIN = ("2025-11-01", "2026-10-31")
BRANDS = {"PRU_HK": "Prudential", "AIA_HK": "AIA", "MFC_HK": "Manulife", "AXA_HK": "AXA"}

# ---------- numbers computed from the evidence store ----------
con = sqlite3.connect(DB)
q = lambda s, *a: con.execute(s, a).fetchall()

_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def clean_title(t):
    """Record titles as shown to readers: no HTML line breaks, no ISO dates (house rule P11)."""
    t = re.sub(r"<br\s*/?>", " ", t or "", flags=re.I)
    t = re.sub(r"\b(20\d\d)-(\d\d)-(\d\d)\b", lambda m: f"{int(m[3])} {_MON[int(m[2]) - 1]} {m[1]}", t)
    return re.sub(r"\s+", " ", t).strip()


# English names for Chinese-language outlets (the page is English only; the original title stays in the hover note)
OUTLET_EN = {
    "香港經濟日報HKET": "Hong Kong Economic Times", "經濟日報": "Hong Kong Economic Times", "香港01": "HK01", "星島頭條": "Sing Tao Headline",
    "信報網站": "Hong Kong Economic Journal", "on.cc東網": "on.cc", "明報財經網": "Ming Pao Finance", "明報新聞網": "Ming Pao",
    "大公文匯網": "Ta Kung Wen Wei Po", "Yahoo 財經": "Yahoo Finance Hong Kong", "Yahoo新聞": "Yahoo News Hong Kong",
    "香港電台新聞網": "RTHK News", "Now 新聞": "Now News", "Now 財經": "Now Finance", "香港財經時報HKBT": "Hong Kong Business Times",
    "經濟一週": "Economic Digest", "巴士的報": "Bastille Post", "香港商报": "Hong Kong Commercial Daily", "東方日報": "Oriental Daily News",
    "Capital 資本平台": "Capital", "BigGo 財經": "BigGo Finance", "橙新聞": "Orange News", "富途牛牛": "Futu", "Oh!爸媽": "Oh!Mama",
    "遠見雜誌": "Global Views Monthly", "追光者 Pulse HK News": "Pulse HK News", "超訊 Supermedia": "Supermedia", "UHK 港生活": "UHK",
    "成報": "Sing Pao", "CMoney投資網誌": "CMoney", "胡润百富榜": "Hurun Report", "Fitz 運動平台": "Fitz", "香港賽馬會": "Hong Kong Jockey Club",
    "台視新聞網": "TTV News", "Sing Tao Daily 星島日報加拿大": "Sing Tao Daily (Canada)", "大紀元時報香港": "The Epoch Times Hong Kong",
    "紫荊網": "Bauhinia", "經濟導報": "Economic Herald", "地產站": "Property news site", "新假期": "Weekend Weekly magazine",
    "新華澳報": "Macau newspaper", "澳門商報": "Macau newspaper", "大衆報": "Macau newspaper",
}
_CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]+")


def outlet_en(src):
    if not src:
        return src
    s = src.strip().rstrip("*").strip()
    if s in OUTLET_EN:
        return OUTLET_EN[s]
    if s.startswith("Investing.com 香港"):
        return "Investing.com Hong Kong"
    if _CJK.search(s):
        rest = re.sub(r"\s+", " ", _CJK.sub(" ", s)).strip(" -|·")
        return rest if len(rest) >= 3 else "Chinese-language outlet"
    return src

M1_TYPES = ("announcement", "news_article", "content_article")
# Records that matched a brand name but are about another company or market (checked 11 Nov 2026).
NOT_HK = """ and o.title not like '%Prudential Financial%' and o.title not like '%Tata AIA%'
             and o.title not like '%Asia_s Growing Retirement Divide%' """
# Added 12 Nov 2026 after the independent review: 安盛生 is iXensor, a Taiwanese biotech (shares AXA's Chinese name);
# the others are about another company or a non-Hong Kong business of the group.
NOT_HK += """ and o.title not like '%安盛生%' and o.title not like '%iXensor%' and o.title not like '%BNP Paribas Targets%'
             and o.title not like '%Sompo hires%' and o.title not like '%Manulife Wealth & Asset Management expands%'
             and o.title not like '%Vietnam_s Ministry of Finance%' and o.title not like '%Prudential Brokerage%' """
# Noise and dead links: app-download mirror sites and unrelated documents are not evidence of insurer activity;
# records whose link is confirmed dead (404/410 or no server) in the last full link check are left out.
NOISE_DOMAINS = ("soft112.com", "aptoide.com", "apkpure", "uptodown", "apkcombo", "compass-group.com")
NOT_HK += "".join(f" and coalesce(o.source_url,'') not like '%{d}%'" for d in NOISE_DOMAINS)
_dead_f = ROOT / "Prudential" / "_report_work" / "dead_links.txt"
_dead = _dead_f.read_text(encoding="utf-8").split() if _dead_f.exists() else []
if _dead:
    NOT_HK += " and coalesce(o.source_url,'') not in (" + ",".join("'" + u.replace("'", "''") + "'" for u in _dead) + ")"
base = f"""from observations o where o.is_primary=1 and o.hk_relevance='explicit'
           and o.record_type in {M1_TYPES} and o.publication_date between '{WIN[0]}' and '{WIN[1]}' {NOT_HK}"""
stats = {"activity_total": {}, "activity_type": defaultdict(dict), "month": defaultdict(dict), "category": defaultdict(dict),
         "official": {}, "social": {}, "apps": [], "reviews": {}}
for cid, n in q(f"select company_id, count(*) {base} group by 1"):
    stats["activity_total"][BRANDS[cid]] = n
for cid, t, n in q(f"select company_id, activity_type, count(*) {base} group by 1,2"):
    stats["activity_type"][BRANDS[cid]][t] = n
for m, cid, n in q(f"select substr(publication_date,1,7), company_id, count(*) {base} group by 1,2"):
    stats["month"][m][BRANDS[cid]] = n
for cid, tag, n in q(f"""select o.company_id, t.tag, count(*) {base.replace('from observations o', 'from observations o join observation_tags t using(record_id)')}
                         and t.tag_type='product_category' group by 1,2"""):
    stats["category"][BRANDS[cid]][tag] = n
for cid, n in q(f"""select company_id, count(*) from observations where record_type='announcement' and is_primary=1
                    and publication_date between '{WIN[0]}' and '{WIN[1]}' group by 1"""):
    stats["official"][BRANDS[cid]] = n
import sys as _sys
_sys.path.insert(0, str(HERE))
import linkedin as _li  # noqa: E402
LI_POSTS, LI_STATS = _li.load()
stats["li"] = LI_STATS
import social_meta as _sm  # noqa: E402
META_POSTS, META_STATS = _sm.load()
stats["meta"] = META_STATS
stats["meta_ads"] = _sm.load_ads()
import deep as _deep  # noqa: E402
stats["deep"] = _deep.build()
import investors as _inv  # noqa: E402
stats["inv"] = _inv.build()   # share prices, HKEX investor news, Hong Kong capital strength
import maps as _maps  # noqa: E402
stats["maps"] = _maps.build()  # Google Maps footprint and location reviews (None until collected: section stays hidden)
# Industry context: Hong Kong Insurance Awards 2026 (HKFI) and MPF statistics (MPFA), from the context table
stats["awards"] = [{"award": m.replace("Hong Kong Insurance Awards 2026: ", ""), "winner": w, "brand": BRANDS.get(c) if c else None, "url": u, "page": pg}
                   for m, w, c, u, pg in q("select metric, value_text, company_id, url, page from context_stats where source like 'HKFI%' and unit='award' order by company_id is null, metric")]
stats["mpf"] = {"rows": [{"period": pe, "metric": m, "value": v, "unit": un, "page": pg} for pe, m, v, un, pg in
                         q("select period, metric, value, unit, page from context_stats where source like 'MPFA%' order by metric, period")],
                "url": (q("select url from context_stats where source like 'MPFA%' limit 1") or [[None]])[0][0]}   # computed social, ad, video, search and Tai Po fire findings
# YouTube: official channels, videos published in the window (views include paid ad plays; likes hidden on some videos)
stats["youtube"] = {}
for cid, b in BRANDS.items():
    ch = q("select channel_title, url, followers from channel_context where company_id=? and platform='youtube'", cid)
    _tr = json.loads((PRU / "_report_work" / "translations.json").read_text(encoding="utf-8")) if (PRU / "_report_work" / "translations.json").exists() else {}
    vids = [(re.sub(r"\s*[（(\[「【][^)）\]」】]*[㐀-鿿][^)）\]」】]*[)）\]」】]", "", (_tr.get(rid) or {}).get("en") or t), u, d, v) for rid, t, u, d, v in q(
             f"""select o.record_id, o.title, o.source_url, o.publication_date, (select value from observation_metrics m where m.record_id=o.record_id and metric='views')
                 from observations o where o.company_id=? and o.platform='youtube' and o.source_name like '%official%'
                 and o.publication_date between '{WIN[0]}' and '{WIN[1]}'""", cid)]   # English titles for Chinese videos
    views = [v for *_, v in vids if v is not None]
    top = sorted(vids, key=lambda r: -(r[3] or 0))[:3]
    stats["youtube"][b] = {"videos": len(vids), "views": int(sum(views)), "median_views": int(st.median(views)) if views else None,
                           "subscribers": ch[0][2] if ch else None, "channel": ch[0][1] if ch else None,
                           "top": [[t, u, d, int(v or 0)] for t, u, d, v in top]}
# Google Ads Transparency: creatives shown in Hong Kong in the window, by format
stats["ads"] = {}
for cid, b in BRANDS.items():
    rows_ = q("select format, count(*) from ad_library where company_id=? and in_scope=1 and shown_in_window=1 group by 1", cid)
    stats["ads"][b] = {"total": sum(n for _, n in rows_), "formats": dict(rows_)}
for cid, title, plat, url, pub, cap, rid, extra in q("""select company_id, title, platform, source_url, publication_date, capture_ts, record_id, extra
                                                        from observations where record_type='app_snapshot'"""):
    m = dict(q("select metric, value from observation_metrics where record_id=?", rid))
    x = json.loads(extra or "{}")
    stats["apps"].append({"brand": BRANDS[cid], "app": x.get("app_name"), "role": x.get("app_role"), "platform": plat, "url": url,
                          "updated": pub, "captured": cap[:10], "rating": round(m["rating"], 1) if "rating" in m else None,
                          "ratings": int(m["rating_count"]) if "rating_count" in m else None, "version": x.get("version")})
for cid, n in q("select company_id, count(*) from observations where record_type='app_review' group by 1"):
    stats["reviews"][BRANDS[cid]] = n
stats["social_window"] = list(LI_STATS["window"])
stats_js = "/* ---------- Computed from the evidence store at build time (do not edit) ---------- */\nconst STATS = " + json.dumps(stats, ensure_ascii=False) + ";\n"

# ---------- live evidence set for the dashboard filters and drill-downs ----------
# One row per Hong Kong-linked record: [id, yyyymmdd, brand, record type, activity, modules, categories, themes, channel, title, url, source, value]
CHANNEL = {"official": "Official site", "media": "Media", "social": "LinkedIn", "app_store": "App store"}
ins_ids = defaultdict(list)
for iid, rid in q("select insight_id, record_id from client_insight_evidence order by insight_id"):
    ins_ids[iid].append(rid)
need = {r for v in ins_ids.values() for r in v}
def fmt_day(d):
    """'2026-10-02' -> '2 Oct 2026' (report date style)."""
    if not d:
        return "n/a"
    y, m, dd = str(d)[:10].split("-")
    return f"{int(dd)} {['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][int(m) - 1]} {y}"


evid = []
M5_RX = re.compile(r"bancassurance|bank partner|banking partner|\bbroker|\bIFA\b|independent financial advis|distribution|\bagency\b|\bagents?\b|recruit|MDRT|"
                   r"chief distribution|appoint|succession|talent|hiring|代理|銀保|理財顧問|招聘|委任|經紀", re.I)
TR_DB = json.loads((PRU / "_report_work" / "translations.json").read_text(encoding="utf-8")) if (PRU / "_report_work" / "translations.json").exists() else {}
en_sum = {}                                  # EVID id -> English summary of a Chinese record (brief s1 / s9)
_cjk = re.compile(r"[一-鿿]")
for (rid, cid, rtype, atype, title, pub, url, src, scat, lang, prim, rel) in q(f"""
        select o.record_id, o.company_id, o.record_type, o.activity_type, o.title, o.publication_date, o.source_url, o.source_name,
               o.source_category, o.language, o.is_primary, o.hk_relevance
        from observations o where o.record_type in ('announcement','news_article','content_article','social_post','app_review','app_snapshot')
        and o.record_id not like 'SOC-%' and o.record_id not like 'ADM-%'
        and o.publication_date between '{WIN[0]}' and '2026-11-10' {NOT_HK} order by o.publication_date"""):
    if rid not in need and not (prim == 1 and rel == "explicit" and pub <= WIN[1] and rtype != "app_snapshot"):
        continue
    if rtype == "social_post" and rid not in need:      # replaced by the full-year LinkedIn export and the YouTube rows below
        continue
    mods = "".join(sorted(m for (m,) in q("select module from observation_modules where record_id=?", rid)))
    # Module 5 (distribution, partnerships and talent, brief s4): also tag news and releases about distribution partners, agents and senior hires
    if rtype in M1_TYPES and "M5" not in mods and M5_RX.search((title or "") + " " + (TR_DB.get(rid) or {}).get("en", "")):
        mods = "".join(sorted(set(re.findall(r"M\d", mods)) | {"M5"}))
    cats = [t for (t,) in q("select tag from observation_tags where record_id=? and tag_type='product_category'", rid)]
    ths = [t for (t,) in q("select tag from observation_tags where record_id=? and tag_type='theme'", rid)]
    met = dict(q("select metric, value from observation_metrics where record_id=?", rid))
    val = met.get("likes") if rtype == "social_post" else met.get("rating")
    if url and url.startswith("javascript"):
        url = ""
    src = "LinkedIn" if rtype == "social_post" else re.sub(r"\s*\(via .*?\)", "", src or "")
    src = outlet_en(src)
    if lang == "zh" and (TR_DB.get(rid) or {}).get("en"):
        en_sum[rid] = TR_DB[rid]["en"]
    evid.append([rid, int(pub.replace("-", "")), BRANDS[cid], rtype, atype, mods, cats, ths, CHANNEL.get(scat, "Media"),
                 clean_title(title)[:180] + ("*" if lang == "zh" else ""), url or "", src, val])
for n, p_ in enumerate(LI_POSTS):
    evid.append([f"LI-{n}", int(p_["date"].replace("-", "")), p_["brand"], "social_post", "", "M2", [], p_["themes"], "LinkedIn",
                 (p_["text"][:180] or "(image post)") + ("*" if _cjk.search(p_["text"] or "") else ""), p_["url"] or "", "LinkedIn", p_["likes"], [p_["comments"], p_["shares"], p_["er"], p_["format"]]])
for n, p_ in enumerate(META_POSTS):
    evid.append([f"{p_['platform'][:2].upper()}-{n}", int(p_["date"].replace("-", "")), p_["brand"], "social_post", "", "M2", [], p_["themes"], p_["platform"],
                 (p_["text"][:180] or "(post without caption)") + ("*" if _cjk.search(p_["text"] or "") else ""), p_["url"] or "", p_["platform"], p_["reactions"], [p_["comments"], p_["shares"], p_["er"], p_["format"]]])
# --- every other collected dataset, as searchable evidence records (not counted as competitor activity) ---
# Facebook and Instagram ads running in Hong Kong on 11 Nov 2026 (Meta Ad Library)
for rid, cid, title, url, pub, fmt, lang, xtra in q("""select record_id, company_id, coalesce(original_content, title), source_url, publication_date,
        content_format, language, extra from observations where record_id like 'ADM-%' order by publication_date"""):
    if not pub:
        continue
    t = re.sub(r"\s+", " ", title or "(ad without text)")[:180]
    if lang == "zh" and (TR_DB.get(rid) or {}).get("en"):
        en_sum[rid] = TR_DB[rid]["en"]
    cta = (json.loads(xtra or "{}") or {}).get("cta")
    evid.append([rid, int(pub.replace("-", "")), BRANDS[cid], "ad", "", "M2", [], [], "Meta ads", t + ("*" if lang == "zh" else ""), url,
                 "Meta Ad Library" + (f" · button: {cta}" if cta else ""), None, [None, None, None, (fmt or "").title() or None]])
# Google ad creatives shown in Hong Kong in the window (Google Ads Transparency Center; no ad text is published there)
for n, (cid, fmt, first, last, url) in enumerate(q("""select company_id, format, first_shown, last_shown, url from ad_library
        where in_scope=1 and shown_in_window=1 order by first_shown""")):
    d = max(first or WIN[0], WIN[0])[:10]
    evid.append([f"GAD-{n}", int(d.replace("-", "")), BRANDS[cid], "ad", "", "M2", [], [], "Google ads",
                 f"Google {(fmt or 'ad').lower()} ad, first shown {fmt_day(first)}, last shown {fmt_day(last)}", url, "Google Ads Transparency Center", None])
# Stock-exchange filings (HKEXnews) for the three Hong Kong-listed groups
_ROUTINE = re.compile(r"next day disclosure|monthly return|transaction in own shares|form of proxy|notification letter|request form|reply slip|change in issued share|"
                      r"total voting rights|block listing|forfeit|transactions by persons discharging|Admission to Trading|Dividend Mandate|Scrip Reference", re.I)
_hk = []
for _f in ("hkex_filings.json", "hkex_filings_1-11Nov2026.json"):
    _fp = PRU / "hk_investor_data" / _f
    if _fp.exists():
        _hk += json.loads(_fp.read_text(encoding="utf-8"))
for n, x in enumerate(_hk):
    dd, mm, yy = x["date"][:10].split("/")
    t = html_lib.unescape(x["title"])
    evid.append([f"HKX-{n}", int(yy + mm + dd), x["brand"], "filing", "", "M1M5" if not _ROUTINE.search(t) else "M1", [], [], "HKEX",
                 ("[Routine] " if _ROUTINE.search(t) else "") + t[:170], x["url"], f"HKEXnews · {re.sub(r'<[^>]+>', '', x.get('category') or '')[:60]}", None])
# Industry awards: Hong Kong Insurance Awards 2026 (HKFI Monthly Brief)
for n, (metric, winner, cid, url, page) in enumerate(q("""select metric, value_text, company_id, url, page from context_stats
        where source like 'HKFI%' and unit='award' and company_id is not null""")):
    evid.append([f"AWD-{n}", 20261030, BRANDS[cid], "award", "award_recognition", "M1", [], [], "Industry",
                 f"{metric.replace('Hong Kong Insurance Awards 2026: ', 'Hong Kong Insurance Awards 2026, ')}: {winner}", url, f"HKFI Monthly Brief, p.{page}", None])
for rid, cid, title, url, pub in q(f"""select record_id, company_id, title, source_url, publication_date from observations
        where platform='youtube' and source_name like '%official%' and publication_date between '{WIN[0]}' and '{WIN[1]}'"""):
    v = q("select value from observation_metrics where record_id=? and metric='views'", rid)
    evid.append([rid, int(pub.replace("-", "")), BRANDS[cid], "video", "", "M2", [], [], "YouTube",
                 re.sub(r"\s+", " ", title or "")[:180], url or "", "YouTube", v[0][0] if v else None])
_CJK_T = re.compile(r"[㐀-鿿]")
for row in evid:
    # Chinese titles never flagged as Chinese (e.g. videos): flag them and use their English translation
    if not row[9].endswith("*") and _CJK_T.search(row[9]) and ((TR_DB.get(row[0]) or {}).get("en") or (TR_DB.get(row[10]) or {}).get("en")):
        row[9] += "*"
    if row[0] not in en_sum and row[9].endswith("*"):
        en = (TR_DB.get(row[0]) or {}).get("en") or (TR_DB.get(row[10]) or {}).get("en")
        if en:
            en_sum[row[0]] = en
# English only on the page: drop Chinese names in brackets inside English summaries ("Tennis Open 2026 (保誠香港網球公開賽2026)")
_NOZH = re.compile(r"\s*[（(\[「【][^()（）\[\]「」【】]*[㐀-鿿][^()（）\[\]「」【】]*[)）\]」】]")
_CJK_RUN = re.compile(r"\s*[（(\[「【]?[㐀-鿿「」【】，、：]+[)）\]」】]?")


def nozh(v):
    """English text only: remove Chinese names in (nested) brackets, then any leftover Chinese runs."""
    p = None
    while p != v:
        p, v = v, _NOZH.sub("", v)
    return re.sub(r"\s{2,}", " ", _CJK_RUN.sub("", v)).strip()


en_sum = {k: nozh(v) for k, v in en_sum.items()}
stats_js += "const EN_SUM = " + json.dumps(en_sum, ensure_ascii=False, separators=(",", ":")) + ";\n"
stats_js += "const EVID = " + json.dumps(evid, ensure_ascii=False, separators=(",", ":")) + ";\n"
# Emotional positioning and calls to action of each post and ad. Rule-based on the full text (English and Chinese keyword lists below,
# shown on the dashboard); a record can carry several narratives. Ads use the ad's own button; posts use call-to-action wording in the text.
NARR_RX = {
    "family": re.compile(r"famil|loved ones?|your child|children|\bkids?\b|parents?\b|promise|家人|家庭|摯愛|親人|子女|父母|孩子|承諾", re.I),
    "health": re.compile(r"health|wellness|well-being|wellbeing|longevity|ageing|aging|fitness|marathon|\brun\b|mental|cancer|critical illness|medical|"
                         r"健康|長壽|樂齡|運動|身心|跑步|醫療|癌|危疾|疾病", re.I),
    "wealth": re.compile(r"wealth|saving|retire|\bMPF\b|annuit|invest|financial (freedom|independence|goals?|planning)|passive income|"
                         r"財富|儲蓄|退休|強積金|年金|理財|投資|收息|增值|財務自由", re.I),
    "premium": re.compile(r"private (wealth|bank|client)|prestige|legacy|high[- ]net[- ]worth|\bHNW\b|\bAlta\b|premier|elite|succession|"
                          r"尊尚|尊貴|傳承|私人財富|高淨值|尊享|顯赫", re.I),
    "rewards": re.compile(r"lucky draw|giveaway|prize|\bwin\b|discount|rebate|voucher|coupon|promo code|"
                          r"抽獎|獎賞|優惠|禮券|回贈|折扣|獎品|贏取", re.I),
}
CTA_BTN = {"Learn more": "explore", "See details": "explore", "Contact us": "adviser", "Apply now": "apply", "Get quote": "apply", "Sign up": "signup"}
CTA_RX = [("apply", re.compile(r"get a quote|apply|buy now|purchase|enrol|投保|申請|報價|立即保障", re.I)),
          ("adviser", re.compile(r"contact|consult|adviser|advisor|financial planner|consultant|whatsapp|call us|聯絡|查詢|顧問|預約|理財策劃", re.I)),
          ("signup", re.compile(r"sign up|register|join us|lucky draw|登記|報名|參加|參與", re.I)),
          ("app", re.compile(r"download|in the app|app store|下載|應用程式", re.I)),
          ("explore", re.compile(r"learn more|find out|discover|visit|click|link in bio|了解更多|即睇|按此|詳情|立即了解", re.I))]
narr = {}
# Brand sign-off hashtags on almost every post are a signature, not the post's message: AIA #HealthierLongerBetterLives / #健康長久好生活, AXA #KnowYouCan
SIGNOFF_RX = re.compile(r"#(HealthierLongerBetterLives|健康長久好生活|KnowYouCan)\.?", re.I)
def _narr(rid, text, btn=None):
    text = SIGNOFF_RX.sub(" ", text or "")
    n = [k for k, rx in NARR_RX.items() if rx.search(text)]
    c = CTA_BTN.get(btn) if btn else None
    if not c:
        c = next((k for k, rx in CTA_RX if rx.search(text)), None)
    if n or c:
        narr[rid] = [n, c, "button" if btn in CTA_BTN else "text"]
for rid, txt, xtra in q("select record_id, coalesce(original_content, title), extra from observations where record_id like 'ADM-%'"):
    _narr(rid, txt, (json.loads(xtra or "{}") or {}).get("cta"))
for n, p_ in enumerate(LI_POSTS):
    _narr(f"LI-{n}", p_["text"])
for n, p_ in enumerate(META_POSTS):
    _narr(f"{p_['platform'][:2].upper()}-{n}", p_["text"])
stats_js += "const NARR = " + json.dumps(narr, ensure_ascii=False, separators=(",", ":")) + ";\n"
print(f"narrative / call-to-action tags: {len(narr)}")
# Named Hong Kong publications searched one by one (pipeline/collectors/hk_media_sites.py)
_msf = PRU / "ci-monitor" / "data" / "raw" / "hk_media_sites_coverage.json"
stats_js += "const MEDIA_SRC = " + (_msf.read_text(encoding="utf-8") if _msf.exists() else "[]") + ";\n"
# Insurance Authority press releases, read in a browser on 17 Nov 2026 (the site blocks automated tools)
_iapr = PRU / "_report_work" / "ia_press_releases.json"
stats_js += "const IA_PR = " + (_iapr.read_text(encoding="utf-8") if _iapr.exists() else "null") + ";\n"
# Where each ad's call-to-action button leads (Meta Ad Library, ads running on 11 Nov 2026), 17 Nov 2026.
# fb.me = a form inside Facebook / Instagram (lead ad); insurer domains and their short links = own website or campaign page;
# AXA's bit.ly links are click-tracking links that land on axa.com.hk (checked on a sample, 17 Nov 2026).
_PAGE = {"191172447574636": "Prudential", "233237970068198": "AIA", "381309905353457": "AXA", "485081128298580": "Manulife"}
_cta_dest = {}
for _a in json.loads((PRU / "apify_raw" / "fb_ads.json").read_text(encoding="utf-8")):
    _b = _PAGE.get(str(_a.get("pageId") or _a.get("pageID")))
    if not _b:
        continue
    _s = _a.get("snapshot") or {}; _cards = _s.get("cards") or []
    _u = (_s.get("linkUrl") or (_cards[0].get("linkUrl") if _cards else "") or "").lower()
    _btn = _s.get("ctaText") or (_cards[0].get("ctaText") if _cards else None) or "No button"
    _d = ("form" if "fb.me" in _u else "site" if re.search(r"aia\.com|axa\.com|manulife\.com|prudential\.com|pruhk\.co|bit\.ly", _u)
          else "social" if re.search(r"instagram\.com|facebook\.com", _u) else "app" if re.search(r"apps\.apple|play\.google", _u)
          else "chat" if re.search(r"wa\.me|whatsapp|m\.me", _u) else "none" if not _u else "other")
    _x = _cta_dest.setdefault(_b, {"ads": 0, "dest": {}, "buttons": {}, "example": {}})
    _x["ads"] += 1; _x["dest"][_d] = _x["dest"].get(_d, 0) + 1
    _x["buttons"].setdefault(_d, {}); _x["buttons"][_d][_btn] = _x["buttons"][_d].get(_btn, 0) + 1
    _x["example"].setdefault(_d, str(_a.get("adArchiveID") or _a.get("adArchiveId") or ""))
stats_js += "const CTA_DEST = " + json.dumps(_cta_dest, ensure_ascii=False, separators=(",", ":")) + ";\n"
stats["cta_destinations_meta_ads"] = {"note": "where each Facebook / Instagram ad's button leads (ads running on 11 Nov 2026): form = lead form inside Facebook / Instagram; site = the insurer's own website or campaign page", **_cta_dest}
# Health and critical-illness products: official brochures and product pages (_report_work/health_ci_products.json)
_hcf = PRU / "_report_work" / "health_ci_products.json"
stats_js += "const HCI = " + (_hcf.read_text(encoding="utf-8") if _hcf.exists() else "{}") + ";\n"
stats_js += "const INS_EVID = " + json.dumps([ins_ids[f"INS-0{i}"] for i in range(1, 6)]) + ";\n"
print(f"dashboard evidence rows: {len(evid)}")

# ---------- search logic (brief s6.1-6.3): the queries actually run, read back from the stored records ----------
_QFAM = [("Google News: brand names (s6.1)", "Brand name, English and Hong Kong Traditional Chinese, searched month by month"),
         ("Google News: brand + category or activity terms (s6.3)", "Brand name AND a group of product, theme or activity terms (s6.2), English and Traditional Chinese"),
         ("Web search: brand + Hong Kong + category (s6.3)", "Brand + Hong Kong + one category or activity group (s6.2), English"),
         ("YouTube search: brand + Hong Kong media (s6.3)", "Brand + interview, podcast or campaign 2026; only Hong Kong or regional media channels kept"),
         ("Google News: named Hong Kong publications (s5.6)", "site: search of each named news, marketing and insurance publication for each brand, English and Chinese, quarter by quarter"),
         ("Google News: campaign and health / critical-illness topics", "Brand + campaign name or ambassador; brand + critical illness, medical or cancer, English and Chinese"),
         ("Google News: Hong Kong insurance discovery (s5.6)", "\"Hong Kong\" insurance + brand (English) and 香港 保險 + brand (Chinese), quarter by quarter; links validated against the publisher")]
_qfam = {i: {"q": set(), "n": 0, "en": None, "zh": None} for i in range(len(_QFAM))}
for (_x,) in q("select extra from observations where extra like '%query%'"):
    try:
        _e = json.loads(_x or "{}")
    except Exception:
        continue
    _qq = _e.get("query") or _e.get("search_query")
    if not _qq:
        continue
    _tool = str(_e.get("tool") or _e.get("collector") or _e.get("import") or "")
    if _e.get("collector") == "hk_media_sites":
        _i = 4 if _e.get("search_kind") == "publication" else 6 if _e.get("search_kind") == "discovery" else 5
    elif _e.get("search_query"):
        _i = 3
    elif "exa" in _tool or "tavily" in _tool:
        _i = 2
    elif " OR " in _qq:
        _i = 1
    else:
        _i = 0
    _qq = re.sub(r"\s*after:\S+|\s*before:\S+", "", _qq).strip()
    _f = _qfam[_i]
    _f["q"].add(_qq)
    _f["n"] += 1
    _lang = "zh" if _cjk.search(_qq) else "en"
    if not _f[_lang] or len(_qq) < len(_f[_lang]):
        _f[_lang] = _qq
SEARCHQ = [[_QFAM[i][0], _QFAM[i][1], len(_qfam[i]["q"]), _qfam[i]["n"], _qfam[i]["en"], _qfam[i]["zh"]] for i in range(len(_QFAM))]
stats_js += "const SEARCHQ = " + json.dumps(SEARCHQ, ensure_ascii=False) + ";\n"
# ---------- Insurance Authority: insurer-level market share (tools/ia_market_share.py, from the IA's own Excel files) ----------
_iaf = PRU / "_report_work" / "ia_market_share.json"
stats_js += "const IA_SHARE = " + (json.dumps(json.loads(_iaf.read_text(encoding="utf-8"))["periods"], ensure_ascii=False) if _iaf.exists() else "[]") + ";\n"

# ---------- licensed company data: profile, workforce, technology detected, research-intent topics ----------
import openpyxl
INTENT_GROUPS = [("AI and machine learning", {"machine learning & artificial intelligence"}),
                 ("Cloud, data and infrastructure", {"cloud", "data management", "database", "storage", "networking", "hardware", "data center"}),
                 ("Security", {"security"}),
                 ("Insurance and personal finance", {"insurance", "personal finance", "finance it"})]
ACRO = {"ai", "ml", "pc", "raid", "hsm", "byol", "aig", "fdap", "tdir", "it", "crm", "api"}
BRANDCASE = {"gemini": "Gemini", "bard": "Bard", "copilot": "Copilot", "studio": "Studio", "azure": "Azure", "aon": "Aon", "anthropic": "Anthropic", "google": "Google", "microsoft": "Microsoft"}
TECH_MAP = [  # each tool assigned to one plain-English group (provider columns mix unrelated tools); covers every tool in the four files
    ("Cloud and containers", ["Microsoft Azure", "Microsoft Azure App Service", "Kubernetes", "Azure Kubernetes Service (AKS)", "Docker", "OpenShift"]),
    ("Data and analytics", ["Databricks", "Azure Databricks", "Microsoft Power BI", "SAS Analytics", "Tableau Software", "Tableau Online",
                            "Oracle Business Intelligence Enterprise Edition", "Google Analytics", "New Relic One", "Informatica", "Tealium IQ", "Webtrends",
                            "SAS Base", "Apache NiFi", "Microsoft SQL Server", "Azure SQL", "Oracle 12c", "Oracle Database", "pandas python", "Visual Website Optimizer"]),
    ("Customer and marketing systems", ["Salesforce CRM", "Salesforce Mobile", "Adobe Experience Manager", "Adobe Marketing Cloud", "Qualtrics",
                                        "Microsoft Customer Experience Management", "Verint", "Agile CRM", "Avaya", "Campaign Monitor",
                                        "Adobe Business Catalyst", "Qualtrics Site Intercept", "Facebook for Business"]),
    ("Back office and automation", ["Workday", "Oracle EBS", "Oracle PeopleSoft", "Anaplan", "Coupa", "BluePrism", "Control-M", "Oracle Procurement",
                                    "IBM FileNet", "IBM Content Manager", "Microsoft Procurement", "iSpring", "Coupa Expense Management", "Oracle Project Management"]),
    ("Security and identity", ["Microsoft Azure Active Directory", "Microsoft Cloud App Security", "Akamai DNS", "Microsoft Active Directory", "reCAPTCHA"]),
    ("Workplace and collaboration", ["Microsoft Teams", "Microsoft SharePoint", "Microsoft Office 365", "Microsoft Exchange Online", "Wechat Work",
                                     "JIRA Software", "Microsoft Project"]),
    ("Software development", ["Microsoft Visual Studio", "Microsoft Team Foundation Server", "IBM WebSphere", "React Native", "Django", "Selenium",
                              "ASP.NET", "Angularjs", "Oracle PL/SQL", "Adobe"]),
]
SITE_OF = {"Prudential": "prudential.com.hk", "AIA": "aia.com.hk", "Manulife": "manulife.com.hk", "AXA": "axa.com.hk"}
lic = {}
for b in BRANDS.values():
    wb = openpyxl.load_workbook(PRU / f"{b}_Hong_Kong_explorium_data.xlsx", read_only=True)
    sheet = lambda n: list(wb[n].iter_rows(values_only=True))
    f = dict(zip(*sheet("1_Firmographics")[:2]))
    w = dict(zip(*sheet("6_Workforce_Trends")[:2]))
    t = dict(zip(*sheet("4_Technographics")[:2]))
    topics = sheet("11_intent_score")[1:]
    num = lambda v: float(str(v).replace(",", "")) if v not in (None, "") else None
    roles = sorted(((k.replace("Perc ", "").replace(" Roles", ""), round(num(v))) for k, v in w.items() if k.startswith("Perc ") and num(v)), key=lambda kv: -kv[1])[:3]
    stack = set(x.strip() for c, v in t.items() if v for x in str(v).split(", "))
    SHOWN = {"Wechat Work": "WeChat Work", "Angularjs": "AngularJS", "pandas python": "pandas (Python)"}
    tech = {lab: [SHOWN.get(x, x) for x in tools if x in stack][:5] for lab, tools in TECH_MAP}
    intent = {}
    for lab, cats in INTENT_GROUPS:
        rows_ = sorted(((s, n.partition(": ")[2]) for n, s in topics if str(n).partition(": ")[0] in cats and "insider" not in str(n)), reverse=True)[:6]
        out, seen = [], set()
        for sc, n in rows_:
            n = re.sub(r"\s*\([^)]*\)$", "", n).strip()
            if n.lower() in seen:
                continue
            seen.add(n.lower())
            n = " ".join(w.upper() if w.lower() in ACRO else BRANDCASE.get(w.lower(), w) for w in n.split())
            out.append([n[:1].upper() + n[1:], sc])
        intent[lab] = out[:3]
    lic[b] = {"employees": f.get("Number Of Employees Range"), "profiles": int(num(w.get("Profiles Found Per Quarter")) or 0) or None,
              "roles": [[r, v] for r, v in roles], "tech": tech, "intent": intent, "snapshot": "1 Nov 2026"}
    # awards reported in the licensed news-events feed (window only) and the employer rating (only when it is this insurer's own page)
    ev = sheet("13_News_Events")
    awards = []
    for row in ev[1:]:
        d = dict(zip(ev[0], row))
        try:
            data = json.loads(d.get("data") or "{}")
        except Exception:
            data = {}
        if d.get("event_name") == "company_award" and WIN[0] <= str(d.get("event_time"))[:10] <= WIN[1]:
            awards.append([str(d.get("event_time"))[:10], data.get("award_name"), data.get("awarding_body")])
    lic[b]["awards"] = sorted(awards, reverse=True)
    rt = sheet("7_Company_Ratings")
    r_ = dict(zip(rt[0], rt[1])) if len(rt) > 1 else {}
    url_ok = str(r_.get("Company Url") or "").rstrip("/").endswith(SITE_OF.get(b, "~"))
    name_ok = b.lower() in str(r_.get("Company Name") or "").lower()
    lic[b]["rating"] = ({k.replace("Ratings ", ""): r_.get(k) for k in ("Ratings Overall", "Ratings Work Life Balance", "Ratings Compensation Benefits",
                         "Ratings Career Opportunities", "Ratings Senior Management")} | {"reviews": r_.get("Total Reviews Count"), "name": r_.get("Company Name")}
                        if url_ok and name_ok and r_.get("Ratings Overall") else None)
stats_js += "const LIC = " + json.dumps(lic, ensure_ascii=False) + ";\n"

tpl = (TPL_DIR / "private/report.html").read_text(encoding="utf-8")

# ---------- report.html ----------
TITLE = "Prudential Hong Kong – Competitive Intelligence Report"
head = tpl[: tpl.index("<body>")]
head = re.sub(r"<title>.*?</title>", f"<title>{TITLE}</title>", head, flags=re.S)
logos = "".join(re.findall(r'<img class="logo logo-on-(?:light|dark)" src="data:image/png;base64,[A-Za-z0-9+/=]+" alt="BridgeAI">', tpl)[:2])
assert logos.count("<img") == 2, "logos not found in template"

chat_start = tpl.index("<!-- Chat -->")
script_start = tpl.index("\n<script>", chat_start)
chat = tpl[chat_start:script_start]
chat = re.sub(r"<h4>Ask anything about.*?</h4>", "<h4>Ask Intelligence: Prudential and its Hong Kong competitors</h4>", chat, flags=re.S)
chat = re.sub(r'(<div class="welcome" id="welcome">\s*<h4>.*?</h4>\s*)<p>.*?</p>',
              r'\1<p>Ask in plain English across the four modules and the evidence behind them: insurer newsrooms, results filings and transcripts, Hong Kong media, posts and ads, product brochures and app reviews, 1 Nov 2025 to 31 Oct 2026. Each answer gives the answer, what it means for Prudential, the evidence and metrics, how strong the evidence is, and the sources.</p>',
              chat, flags=re.S)
chat = chat.replace("Ask the report", "Ask Intelligence")   # walkthrough 16 Nov 2026: assistant renamed
chat = re.sub(r'placeholder="[^"]*"', 'placeholder="Ask about AIA, Manulife or AXA launches, campaigns, apps, results or what Prudential could do…"', chat)

generic = tpl[tpl.index("/* ---------- Interactions"):]
# Answers may carry one ```chart block (JSON): draw it as a simple labelled bar chart (walkthrough 16 Nov 2026: charts where useful)
_md_old = "  markCompetitors(d);\n  return d.innerHTML;"
assert _md_old in generic, "chat md() hook not found"
generic = generic.replace(_md_old, """  d.querySelectorAll("pre > code.language-chart").forEach(c => {
    try {
      const j = JSON.parse(c.textContent), items = (j.items || []).filter(x => Array.isArray(x) && isFinite(+x[1])).slice(0, 8);
      if (!items.length) return;
      const mx = Math.max(...items.map(x => +x[1])) || 1, u = esc(String(j.unit || ""));
      const fig = document.createElement("figure"); fig.className = "achart";
      fig.innerHTML = (j.title ? `<figcaption>${esc(String(j.title))}</figcaption>` : "") + items.map(([l, v]) =>
        `<div class="ab"><span class="al">${esc(String(l))}</span><span class="at"><i style="width:${Math.max(2, +v / mx * 100).toFixed(1)}%"></i></span><span class="av">${(+v).toLocaleString("en-GB")}${u}</span></div>`).join("");
      c.parentElement.replaceWith(fig);
    } catch (e) { /* not valid chart JSON: leave the code block as text */ }
  });
  markCompetitors(d);
  return d.innerHTML;""")

# Scroll handler: once per animation frame, ignore sections in hidden views, change classes only when the active link changes
_old = generic[generic.index('addEventListener("scroll", () => {'):]
_old = _old[:_old.index("}, { passive: true });") + len("}, { passive: true });")]
generic = generic.replace(_old, """let _ticking = false, _cur = -1, _shown = false;
addEventListener("scroll", () => {
  if (_ticking) return;
  _ticking = true;
  requestAnimationFrame(() => {
    _ticking = false;
    const h = document.documentElement, max = h.scrollHeight - h.clientHeight;
    $("#progress").style.width = (max > 0 ? Math.min(100, h.scrollTop / max * 100) : 0) + "%";
    let cur = 0;
    secs.forEach((s, i) => { if (s && s.offsetParent !== null && s.getBoundingClientRect().top < 140) cur = i; });
    const shown = scrollY > 300;
    if (cur !== _cur || shown !== _shown) { links.forEach((a, i) => a.classList.toggle("active", i === cur && shown)); _cur = cur; _shown = shown; }
    document.querySelectorAll(".reveal:not(.in)").forEach(el => { if (el.offsetParent !== null && el.getBoundingClientRect().top < innerHeight) reveal(el); });
  });
}, { passive: true });""")
# Every section names the competitors, so red highlighting would flood the page: brand pills are used instead.
generic, n = re.subn(r"const COMP_RE = /.*?/g;", r"const COMP_RE = /\\b(?!x)x\\b/g;", generic, count=1)
assert n == 1, "competitor regex not replaced"

# Chat panel shows only user-facing state: no model name, tokens or context bar (reviewer rule).
chat, n = re.subn(r'<div class="status" id="status">.*?</div>\s*</div>\s*</div>',
                  '<div class="status" id="status"><div class="row" style="margin:0"><span class="pill" id="stPill">Connecting…</span></div></div>\n  </div>',
                  chat, count=1, flags=re.S)
assert n == 1 and "ctxBar" not in chat and "stModel" not in chat, "chat status block not replaced"
s0 = generic.index("async function status()"); s1 = generic.index("status();", s0)
generic = generic[:s0] + '''async function status() {
  const st = $("#status"), pill = $("#stPill");
  try {
    const s = await (await fetch(API + "/api/status")).json();
    if (s.error) { st.className = "status err"; pill.textContent = "Chat is temporarily unavailable"; return; }
    if (!s.ready) { pill.textContent = "Preparing…"; return setTimeout(status, 1500); }
    st.className = "status ok"; pill.textContent = "Ready – answers drawn only from this report's sources";
  } catch (e) { st.className = "status err"; pill.textContent = "Chat is temporarily unavailable"; }
}
''' + generic[s1:]
generic, n = re.subn(r'\(usage \? `<span title="Prompt tokens.*?</span>` : ""\) \+ ', '', generic, count=1, flags=re.S)
assert n == 1, "token usage display not removed"
assert not re.search(r"Gemini|tokens|context window|stCache|ctxBar|stModel", generic), "AI-stack details still in chat JS"

body = (HERE / "body.html").read_text(encoding="utf-8").replace("{{LOGOS}}", logos)
data = (HERE / "data.js").read_text(encoding="utf-8")
# lead images of the news / newsroom articles (Prudential/ci-monitor/tools/news_images.py), keyed by evidence id
_nidx = json.loads((HERE / "media" / "news_images.json").read_text(encoding="utf-8")) if (HERE / "media" / "news_images.json").exists() else {}
# a picture that many articles share is the site's default logo/banner, not the article's own image: leave it out
_nimg = Counter(v["img"] for v in _nidx.values() if v)
_news = {k: "news_" + k.lower() + ".jpg" for k, v in _nidx.items() if v and v.get("ok", True) and _nimg[v["img"]] <= 2 and (HERE / "media" / v["file"]).exists()}
# screenshots of the source page behind each move on the timeline board (Prudential/ci-monitor/tools/capture_moves.py)
_mv = json.loads((HERE / "media" / "moves.json").read_text(encoding="utf-8")) if (HERE / "media" / "moves.json").exists() else {}
_moves = {u: v["file"] for u, v in _mv.items() if v and u.startswith("https://") and "news.google." not in u and (HERE / "media" / "moves" / v["file"]).exists()}   # Google News pages are consent screens
# (no picture merging: insurer newsroom pages share one template, so look-alike pages are different releases)
data = "const MOVESHOT = " + json.dumps(_moves, ensure_ascii=False, separators=(",", ":")) + ";" + chr(10) + data
# pictures for evidence rows: Google ad creatives, YouTube thumbnails, LinkedIn post images, app store pages (tools/evidence_images.py)
_ei = json.loads((HERE / "media" / "evimg.json").read_text(encoding="utf-8")) if (HERE / "media" / "evimg.json").exists() else {}
_evimg = {k: v for k, v in _ei.items() if v and (HERE / "media" / "evimg" / v).exists()}
import cnsoc as _cn   # Xiaohongshu / WeChat conversation (search-based), 20 Nov 2026
_CN_ROWS, _CN_ST, _CN_INS, _CN_KB = _cn.build()
data = ("const CNSOC = " + json.dumps(_CN_ROWS, ensure_ascii=False, separators=(",", ":")) + ";" + chr(10)
        + "const CNSOC_ST = " + json.dumps(_CN_ST, ensure_ascii=False, separators=(",", ":")) + ";" + chr(10)
        + "const CNSOC_INS = " + json.dumps(_CN_INS, ensure_ascii=False) + ";" + chr(10)) + data
data = "const EVIMGMAP = " + json.dumps(_evimg, separators=(",", ":")) + ";" + chr(10) + data
data = ("const NEWSIMG = " + json.dumps(_news, separators=(",", ":")) + ";\n"
        + "const MEDIA_BASE = location.protocol === 'file:' ? '../report_app/prudential/media/' : '/media/';\n") + data
dash = (HERE / "dashboard.js").read_text(encoding="utf-8")
# visual layer (Sahaj's walkthrough, 17 Nov 2026): source visuals, progressive disclosure, more white space
_media = json.loads((HERE / "media" / "media.json").read_text(encoding="utf-8")) if (HERE / "media" / "media.json").exists() else []
_media = [m for m in _media if (HERE / "media" / m["file"]).exists()]
visual_js = "const MEDIA = " + json.dumps(_media, ensure_ascii=False, separators=(",", ":")) + ";\n" + (HERE / "visual.js").read_text(encoding="utf-8")
visual_js += "\n" + (HERE / "overview.js").read_text(encoding="utf-8")          # InkTank-style executive overview (20 Nov 2026)
# live browser captures of the evidence pages (Prudential/ci-monitor/tools/capture_sources.py) + Stitch design system
_caps = json.loads((HERE / "media" / "captures.json").read_text(encoding="utf-8")) if (HERE / "media" / "captures.json").exists() else []
_caps = [c for c in _caps if (HERE / "media" / c["file"]).exists()]
visual_js += "\nconst CAPTURES = " + json.dumps(_caps, ensure_ascii=False, separators=(",", ":")) + ";\n" + (HERE / "captures.js").read_text(encoding="utf-8") + "\n" + (HERE / "explorer.js").read_text(encoding="utf-8") + "\n" + (HERE / "news.js").read_text(encoding="utf-8") + chr(10) + (HERE / "cardpics.js").read_text(encoding="utf-8") + chr(10) + (HERE / "sheet.js").read_text(encoding="utf-8") + chr(10) + (HERE / "highlight.js").read_text(encoding="utf-8") + chr(10) + (HERE / "cnsoc.js").read_text(encoding="utf-8") + chr(10) + (HERE / "geo.js").read_text(encoding="utf-8") + chr(10) + (HERE / "linkthumbs.js").read_text(encoding="utf-8") + chr(10) + (HERE / "brief.js").read_text(encoding="utf-8") + chr(10) + (HERE / "modcards.js").read_text(encoding="utf-8") + chr(10) + (HERE / "topnav.js").read_text(encoding="utf-8") + chr(10) + (HERE / "order.js").read_text(encoding="utf-8") + chr(10) + (HERE / "chartstop.js").read_text(encoding="utf-8")   # charts and graphs at the top (22 Nov 2026)
head = head.replace("</head>", (HERE / "visual.css").read_text(encoding="utf-8") + (HERE / "theme.css").read_text(encoding="utf-8")
                    + (HERE / "stitch.css").read_text(encoding="utf-8") + (HERE / "explorer.css").read_text(encoding="utf-8")
                    + (HERE / "pritesh.css").read_text(encoding="utf-8") + "</head>", 1)   # Pritesh / InkTank UI skin (22 Nov 2026), loaded last
html = "\n".join([head + "<body>", "", body, chat, "<script>", stats_js, data, dash, visual_js, generic])
assert "{{" not in body
if "<!--MARKET-->" in body:
    print("WARNING: market section not written yet")

# ---------- review-rule checks (Chris / HP / Palo Alto / Novaland / Emma tweak lists) ----------
data_strings = " ".join(m.group(0)[1:-1] for m in re.finditer(r'"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`', data + dash, flags=re.S))
visible = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S) + " " + data_strings
visible = re.sub(r"\$\{[^}]*\}", "", visible)          # template placeholders are code, not text
visible = re.sub(r"<[^>]+>", "", visible)
visible = re.sub(r"https?://\S+", "", visible)          # URLs are not prose
body_text = re.sub(r"<[^>]+>", "", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S))
problems = []
for label, pat in [
    ("em dash", "—"), ("empty brackets", r"\[\s*\]|\(\s*\)"), ("double full stop", r"(?<!\.)\.\.(?!\.)"), ("space before full stop", r"\w \.(\s|$)"),
    ("model / tool / vendor names", r"\b(Gemini|Vertex|Exa|Tavily|Apify|Explorium|Bright ?Data|PredictLeads|SerpAPI|ElevenLabs|tokens?|scraper|scraped|crawl\w*|pipeline|POC)\b"),
    ("methodology wording", r"\bxlsx\b|\bci\.db\b|\brecord_id\b|licensed (tool|extract)|Evidence: \d+ of"),
    ("first-person label", r"\bOur expectation\b|\bour inference\b|\bOur view\b"),
    ("hedging", r"needs? to be confirmed|requires? validation|subject to verification"), ("ISO date", r"\b20\d\d-\d\d-\d\d\b"),
    ("stock phrase", r"provides? an opening|caught my attention|may provide an opportunity"),
    ("overclaim", r"\b(proves?|guarantee[sd]?|definitely|clearly losing)\b"),
]:
    scope = body_text if label in ("empty brackets", "space before full stop", "double full stop") else visible
    for m in re.finditer(pat, scope, flags=re.I if label in ("model / tool / vendor names", "overclaim") else 0):
        problems.append(f"{label}: …{scope[max(0, m.start()-60):m.end()+40].strip()}…")
assert not problems, "Review-rule check failed:\n" + "\n".join(problems)

# Every external link must be a full https URL (no javascript:, no empty href).
for m in re.finditer(r'href="([^"#][^"]*)"', body):
    assert m.group(1).startswith("https://"), f"bad link in body: {m.group(1)}"
for m in re.finditer(r'https?://[^"`\s)]+', data):
    assert m.group(0).startswith("https://"), f"non-https link in data.js: {m.group(0)}"

# ---------- app folder ----------
OUT.mkdir(exist_ok=True)
for d in ("private", "api", "public"):
    (OUT / d).mkdir(exist_ok=True)
(OUT / "public/.gitkeep").touch()
for f in [".gitignore", ".python-version", ".vercelignore", "requirements.txt", "vercel.json"]:
    shutil.copyfile(TPL_DIR / f, OUT / f)

(OUT / "private/report.html").write_text(html, encoding="utf-8")
(OUT / "private" / "media").mkdir(parents=True, exist_ok=True)
for _m in _media + _caps:
    shutil.copyfile(HERE / "media" / _m["file"], OUT / "private" / "media" / _m["file"])
for _f in _moves.values():
    shutil.copyfile(HERE / "media" / "moves" / _f, OUT / "private" / "media" / _f)
for _r in _CN_ROWS:
    if _r[12]:
        shutil.copyfile(HERE / "media" / "cnsoc" / _r[12], OUT / "private" / "media" / _r[12])
for _f in set(_evimg.values()):
    shutil.copyfile(HERE / "media" / "evimg" / _f, OUT / "private" / "media" / _f)
for _k, _f in _news.items():
    shutil.copyfile(HERE / "media" / "news" / (_k + ".jpg"), OUT / "private" / "media" / _f)
(PRU / "Prudential_HK_Competitive_Intelligence_Report.html").write_text(html, encoding="utf-8")

login = (TPL_DIR / "private/login.template.html").read_text(encoding="utf-8")
login = login.replace("Chris Crozier – Contact Intelligence Report for HP", "Prudential Hong Kong – Competitive Intelligence Report")
login = login.replace("Chris Crozier – Contact Intelligence Report", TITLE)
assert "Chris" not in login and "HP" not in login, "login page still names the template subject"
(OUT / "private/login.template.html").write_text(login, encoding="utf-8")

api = (TPL_DIR / "api/index.py").read_text(encoding="utf-8")
api = api.replace("chris-crozier-report-access-v2", "prudential-hk-ci-report-access-v1")
assert "chris" not in api.lower()
# Narrated tour clips: served only to signed-in viewers (private/narration/pru/<step>.mp3)
_route = """        m = re.fullmatch(r'/narration/pru/([a-z0-9-]{1,40})\\.mp3', self.path)
        if m:
            if not self.authed():
                return self.send_json({'error': 'Sign in required'}, 401)
            clip = ROOT / 'private' / 'narration' / 'pru' / f'{m.group(1)}.mp3'
            return self.send_body(clip.read_bytes(), 'audio/mpeg') if clip.is_file() else self.send_json({'error': 'Not found'}, 404)
"""
_route += """        m = re.fullmatch(r'/media/([A-Za-z0-9_.-]{1,80}\\.jpg)', self.path)
        if m:
            if not self.authed():
                return self.send_json({'error': 'Sign in required'}, 401)
            img = ROOT / 'private' / 'media' / m.group(1)
            return self.send_body(img.read_bytes(), 'image/jpeg') if img.is_file() else self.send_json({'error': 'Not found'}, 404)
"""
_hook = "        if self.path == '/api/logout':\n"
assert api.count(_hook) == 1, "logout hook"
if "/media/" not in api:
    api = api.replace(_hook, _route + _hook)
if "\nimport re\n" not in api and not api.startswith("import re"):
    api = api.replace("import ", "import re\nimport ", 1)
(OUT / "api/index.py").write_text(api, encoding="utf-8")

backend = (TPL_DIR / "backend.py").read_text(encoding="utf-8")
prompt = (HERE / "system_prompt.txt").read_text(encoding="utf-8").strip()
backend = re.sub(r'SYSTEM_PROMPT = """.*?"""', lambda m: f'SYSTEM_PROMPT = """{prompt}"""', backend, flags=re.S)
backend = backend.replace("chris-crozier-report-kb", "prudential-hk-ci-report-kb")
# 17 Nov 2026, answer speed: cap the model's hidden thinking, and retrieve the relevant social posts per question
_b0 = 'body = {"generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS, "temperature": 0.3}}'
assert backend.count(_b0) == 1, "backend generationConfig not found"
backend = backend.replace(_b0, 'body = {"generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS, "temperature": 0.3, "thinkingConfig": {"thinkingBudget": 512}}}')
_b1 = '    turns.append({"role": "user", "parts": [{"text": question}]})'
assert backend.count(_b1) == 1, "backend question turn not found"
backend = backend.replace(_b1, '    turns.append({"role": "user", "parts": [{"text": question + retrieve_posts(question) + retrieve_records(question)}]})')
_b2 = "def build_request(question: str, history: list) -> dict:"
assert backend.count(_b2) == 1
backend = backend.replace(_b2, '''# --- retrieval over the individual social posts (private/posts.jsonl): keyword scoring, English words + Chinese character pairs ---
import math as _math, re
_POSTS_F = APP_DIR / "private/posts.jsonl"
POSTS = [json.loads(l) for l in _POSTS_F.read_text(encoding="utf-8").splitlines() if l.strip()] if _POSTS_F.exists() else []
_STOP = set("the a an of to in on for and or is are was were be by with at from as that this which what how who when do does did their its it vs versus over past last months month year".split())
def _terms(t):
    t = (t or "").lower()
    w = [x for x in re.findall(r"[a-z0-9+]{3,}", t) if x not in _STOP]
    c = re.findall(r"[\\u4e00-\\u9fff]", t)
    return w + [c[i] + c[i + 1] for i in range(len(c) - 1)]
_DF = {}
for _p in POSTS:
    _p["_t"] = set(_terms(_p["text"] + " " + _p["line"]))
    for _x in _p["_t"]:
        _DF[_x] = _DF.get(_x, 0) + 1
_BR = {"prudential": "Prudential", "aia": "AIA", "manulife": "Manulife", "axa": "AXA", "保誠": "Prudential", "友邦": "AIA", "宏利": "Manulife", "安盛": "AXA"}
def retrieve_posts(question, k=80):
    if not POSTS:
        return ""
    qt = set(_terms(question)) - set(_BR)
    brands = {b for n, b in _BR.items() if n in question.lower()}
    n = len(POSTS)
    scored = []
    for p in POSTS:
        s = sum(_math.log(1 + n / _DF[x]) for x in qt if x in p["_t"])
        if brands and p["brand"] in brands:
            s += 1.5
        if s > 0:
            scored.append((s, p["line"]))
    scored.sort(key=lambda x: -x[0])
    top = [l for _, l in scored[:k]]
    return ("\\n\\nRETRIEVED POSTS (the official social posts most relevant to this question, from PARTS I and H; cite with their URL):\\n" + "\\n".join(top)) if top else ""
_REC_F = APP_DIR / "private/records.jsonl"
RECORDS = [json.loads(l) for l in _REC_F.read_text(encoding="utf-8").splitlines() if l.strip()] if _REC_F.exists() else []
_RDF = {}
for _p in RECORDS:
    _p["_t"] = set(_terms(_p["text"]))
    for _x in _p["_t"]:
        _RDF[_x] = _RDF.get(_x, 0) + 1
def retrieve_records(question, k=60):
    if not RECORDS:
        return ""
    qt = set(_terms(question)) - set(_BR)
    brands = {b for n, b in _BR.items() if n in question.lower()}
    n = len(RECORDS)
    scored = []
    for p in RECORDS:
        s = sum(_math.log(1 + n / _RDF[x]) for x in qt if x in p["_t"])
        if brands and p["brand"] in brands:
            s += 1.5
        if s > 0:
            scored.append((s, p["line"]))
    scored.sort(key=lambda x: -x[0])
    top = [l for _, l in scored[:k]]
    return ("\\n\\nRETRIEVED EVIDENCE RECORDS (PART D records and PART S posts most relevant to this question; cite with their URL):\\n" + "\\n\\n".join(top)) if top else ""


def build_request(question: str, history: list) -> dict:''')
assert "Chris" not in backend and "Defence" not in backend
backend, n1 = re.subn(r'return self\.send_json\(\{\s*"ready": state\["ready"\],.*?\}\)',
                      'return self.send_json({"ready": state["ready"], "error": "unavailable" if state["cache_error"] else None})',
                      backend, count=1, flags=re.S)
backend, n2 = re.subn(r'emit\(\{"done": True, "usage": \{.*?\}\}\)', 'emit({"done": True})', backend, count=1, flags=re.S)
assert n1 == 1 and n2 == 1, "backend status/usage not patched"
(OUT / "backend.py").write_text(backend, encoding="utf-8")


# ---------- knowledge base (text only, deterministic order) ----------
def page_text(h: str) -> str:
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S)
    h = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', r"[\2](\1)", h, flags=re.S)
    h = re.sub(r"<(br|/p|/li|/tr|/h\d|/div)[^>]*>", "\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = re.sub(r"[ \t]+", " ", h)
    return re.sub(r"\n\s*\n+", "\n\n", h).strip()


def clean(t, n=None):
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    return t[:n] + " …" if n and len(t) > n else t


parts = [
    ("PART A - THE REPORT (primary source): page text", page_text(body)),
    ("PART A2 - THE REPORT: tables, cards and timeline data (JavaScript source; links use L(text, url))", (HERE / "data.js").read_text(encoding="utf-8")),   # picture maps and PART S data stay out
    ("PART A3 - COMPUTED COUNTS used by the report's charts (JSON)", json.dumps(stats, ensure_ascii=False, separators=(",", ":"))),
    ("PART A4 - LICENSED COMPANY DATA (snapshot 1 Nov 2026): employee range, workforce mix, technology detected on websites and job ads "
     "(detected does not mean deployed), and research-intent topics (what staff read about online; a research signal, not a purchase). "
     "Cite it as 'licensed data'; never name the provider. Topic volumes differ by company, so do not compare counts.",
     json.dumps(lic, ensure_ascii=False, indent=1)),
    ("PART B - HONG KONG SEGMENT FINANCIALS (extracted from the insurers' results filings, presentations and transcripts)",
     (PRU / "_report_work/hk_financials.md").read_text(encoding="utf-8") if (PRU / "_report_work/hk_financials.md").exists() else "(pending)"),
]
ins = json.loads((PRU / "ci-monitor/pipeline/insights/prudential_hk.json").read_text(encoding="utf-8"))
parts.append(("PART C - ANALYST INSIGHTS with evidence rules (earlier analysis behind Part 6 of the report; the report text in PART A takes precedence where they differ)",
              json.dumps(ins["insights"], ensure_ascii=False, indent=1)))

# PART D: every in-window observation (primary records first), grouped by brand and type.
obs = []
_REC = []   # 20 Nov 2026: records go to private/records.jsonl and are retrieved per question (smaller cached knowledge base, faster answers)
for (cid, rtype, atype, title, desc, content, lang, summ, pub, src, scat, url, hk, plat, size, rid) in q(f"""
        select company_id, record_type, activity_type, title, description, original_content, language, english_summary,
               publication_date, source_name, source_category, source_url, hk_relevance, platform, cluster_size, record_id
        from observations o where (publication_date between '{WIN[0]}' and '{WIN[1]}' or record_type in ('app_snapshot','web_feature'))
        and is_primary=1 and hk_relevance='explicit' and record_id not like 'SOC-%' and record_id not like 'ADM-%' {NOT_HK}
        order by company_id, record_type, publication_date"""):
    tags = ", ".join(f"{a}:{b}" for a, b in q("select tag_type, tag from observation_tags where record_id=? order by 1,2", rid))
    mets = ", ".join(f"{a}={b:g}" for a, b in q("select metric, value from observation_metrics where record_id=? order by 1", rid))
    body_txt = clean(content or desc, 160)   # 17 Nov 2026: shorter fields, for Ask Intelligence speed
    # one compact header line per record (17 Nov 2026; every record here is Hong Kong-relevant, so that field is left out)
    lines = [f"### [{BRANDS[cid]}] {pub or 'n/a'} · {rtype}/{atype} · {src} ({scat}{', ' + plat if plat and plat != scat else ''})"
             + (f" · same story in {size} outlets" if size and size > 1 else "") + f" · {clean(title, 200)}"]
    if url and "news.google.com" in url:
        lines.append("URL: none (found via a Google News redirect link; cite the outlet name and date)")
    elif url and not url.startswith("javascript"):
        lines.append(f"URL: {url}")
    if mets:
        lines.append(f"Metrics: {mets}")
    if summ and summ != desc and lang != "en":   # English records: the summary repeats the text
        lines.append(f"English summary: {clean(summ, 280)}")
    if body_txt and not (summ and lang != "en"):   # Chinese records with an English summary: the summary replaces the long original (title kept)
        lines.append(f"Text: {body_txt}")
    obs.append("\n".join(lines))
    _REC.append({"line": "\n".join(lines), "brand": BRANDS[cid], "text": " ".join(lines)})
_dcount = Counter((r["brand"], r["line"].split(" · ")[1].split("/")[0]) for r in _REC)
parts.append(("PART D - EVIDENCE RECORDS (searchable; 20 Nov 2026, answer speed)",
              f"{len(_REC)} Hong Kong-linked records (official releases, media coverage, app listings and reviews, website features; 1 Nov 2025 - 31 Oct 2026, "
              "duplicates of the same story merged) are held as searchable records. The records most relevant to each question are appended after the question "
              "under 'RETRIEVED EVIDENCE RECORDS'; quote them with their URL. Counts by brand and record type (use these for totals, not the retrieved sample): "
              + "; ".join(f"{b} {t}: {n}" for (b, t), n in sorted(_dcount.items()))))
if False: parts.append((f"PART D - EVIDENCE RECORDS (official releases, media coverage, LinkedIn posts, app listings and reviews, website features; Hong Kong-linked records only; "
              f"{len(obs)} records, 1 Nov 2025 - 31 Oct 2026, duplicates of the same story merged)", "\n\n".join(obs)))

cov = q("select company_id, source, status, records, note from source_coverage order by 1, 2")
# PART R: walkthrough additions (16 Nov 2026) - health and critical-illness products, positioning and calls to action, named media sources
_r = []
if _hcf.exists():
    _r += ["HEALTH AND CRITICAL-ILLNESS PRODUCTS AND ACTIVITY (official brochures and product pages; 'verification' says how each figure was checked):",
           _hcf.read_text(encoding="utf-8")]
_pos = {}
for _x in evid:
    if ((_x[3] == "social_post" and _x[8] != "Threads") or (_x[3] == "ad" and _x[8] == "Meta ads")) and 202511 <= int(_x[1]) // 100 <= 202610:   # same 12 months as the dashboard tables
        _d = _pos.setdefault(_x[2], {"records": 0, "narr": {}, "cta": {}, "with_cta": 0})
        _d["records"] += 1
        _t = narr.get(_x[0])
        if _t:
            for _k in _t[0]:
                _d["narr"][_k] = _d["narr"].get(_k, 0) + 1
            if _t[1]:
                _d["with_cta"] += 1
                _d["cta"][_t[1]] = _d["cta"].get(_t[1], 0) + 1
_r += ["", "EMOTIONAL POSITIONING AND CALLS TO ACTION BY BRAND (rule-based keyword counts over every Facebook, Instagram and LinkedIn post and every "
       "Facebook / Instagram ad dated 1 Nov 2025 - 31 Oct 2026, the same records as the dashboard tables; narratives: family = family and promises, health = health, wellness and longevity, wealth = wealth and "
       "financial independence, premium = premium / high-net-worth, rewards = rewards and promotions; calls to action: explore, adviser = talk to an adviser, "
       "apply = quote or apply, signup = sign up or register, app = use the app; ads use the ad's own button):",
       json.dumps(_pos, ensure_ascii=False, indent=1),
       "Campaign-level value propositions and calls to action (hand-reviewed) are in PART A2 under CAMPAIGNS."]
if _msf.exists():
    _r += ["", "NAMED NEWS, MARKETING AND INSURANCE PUBLICATIONS SEARCHED (site: searches for each brand, English and Chinese, 1 Nov 2025 - 31 Oct 2026; "
           "hits = brand headlines found; records = new records added; already_held = found earlier by other searches):", _msf.read_text(encoding="utf-8")]
parts.append(("PART R - HEALTH AND CRITICAL ILLNESS, POSITIONING AND CALLS TO ACTION, AND MEDIA SOURCES SEARCHED (added 16 Nov 2026)", "\n".join(_r)))

# PART S: Xiaohongshu and WeChat conversation about the four insurers (search-based; 20 Nov 2026)
_cn_head, _, _cn_posts = _CN_KB.partition("Posts (date | insurer")
parts.append(("PART S - MAINLAND SOCIAL CONVERSATION: XIAOHONGSHU AND WECHAT POSTS ABOUT THE FOUR INSURERS (search-based, not part of the activity counts)",
              _cn_head + "The individual posts are searchable records: the most relevant to each question are appended after it under 'RETRIEVED EVIDENCE RECORDS'."))
for _l in _cn_posts.splitlines()[1:]:
    if _l.strip():
        _f = _l.split(" | ")
        _REC.append({"line": "[Mainland social, search-based] " + _l, "brand": _f[1] if len(_f) > 1 else "", "text": _l})
parts.append(("PART E - SOURCE COVERAGE by brand (what was and was not available)",
              "\n".join(f"{BRANDS[c]} | {s} | {stt} | {n} records" + (f" | {clean(nt)}" if nt else "") for c, s, stt, n, nt in cov)))


def newsroom_text() -> str:
    import openpyxl
    wb = openpyxl.load_workbook(PRU / "Prudential_HK_newsroom.xlsx", read_only=True)
    ws = wb["newsroom_articles"]          # the media_contacts sheet holds personal phones/emails and is never read
    rows = list(ws.iter_rows(values_only=True))
    head = [str(h) for h in rows[0]]
    out = []
    for r in rows[1:]:
        d = dict(zip(head, r))
        if d.get("In POC window (Nov 25-Oct 26)") != "Yes":
            continue
        out.append(f"### {clean(d['Title'])}\nDate: {str(d['Publication date'])[:10]}\nURL: {d['Article URL']}\n"
                   f"Summary: {clean(d.get('Summary (meta description)'))}\nText: {clean(d.get('Article text'), 6000)}")
    return "\n\n".join(out)


parts.append(("PART F - PRUDENTIAL HONG KONG NEWSROOM: full text of every release in the window", newsroom_text()))
for nm, lab in [("deep_prudential.md", "Prudential Hong Kong"), ("deep_aia.md", "AIA Hong Kong"), ("deep_manulife.md", "Manulife Hong Kong"),
                ("deep_axa.md", "AXA Hong Kong"), ("deep_crossbrand.md", "cross-brand data (app reviews, media, licensed data)")]:
    parts.append((f"PART G - ANALYST DEEP READ: {lab} (findings with page-level sources; 'derived' numbers are analyst calculations; "
                  "where it differs from PART A, PART A wins)", (PRU / "_report_work" / nm).read_text(encoding="utf-8")))
# PARTS I and H (17 Nov 2026, answer speed): the individual posts are no longer sent in full with every question. They are written to
# private/posts.jsonl; the backend searches them per question and appends the most relevant ones after the question ("RETRIEVED POSTS").
# Totals, engagement, themes and positioning per brand stay here (PART A3, PART R and the report text).
_posts = []
for p_ in META_POSTS:
    _en = (TR_DB.get(p_['url']) or {}).get('en')
    _posts.append({"line": f"[{p_['brand']} · {p_['platform']}] {p_['date']} · {p_['format']} · reactions {p_['reactions']}, comments {p_['comments']}, "
                           f"shares {p_['shares']}, engagement rate {p_['er']}% · URL: {p_['url']}\n" + (f"EN: {clean(_en, 220)}" if _en else clean(p_['text'], 200)),
                   "brand": p_['brand'], "text": (p_['text'] or "")[:600] + " " + (_en or "")})
for p_ in LI_POSTS:
    _posts.append({"line": f"[{p_['brand']} · LinkedIn] {p_['date']} · {p_['format']} · reactions {p_['likes']}, comments {p_['comments']}, reposts {p_['shares']}, "
                           f"engagement rate {p_['er']}% · themes: {', '.join(p_['themes']) or 'none'} · URL: {p_['url']}\n{clean(p_['text'], 220)}",
                   "brand": p_['brand'], "text": (p_['text'] or "")[:600] + " " + " ".join(p_['themes'])})
(OUT / "private").mkdir(exist_ok=True)
(OUT / "private" / "posts.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in _posts), encoding="utf-8")
(OUT / "private" / "records.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in _REC), encoding="utf-8")
parts.append(("PARTS I AND H - INDIVIDUAL SOCIAL POSTS (Facebook, Instagram, Threads, LinkedIn; 1 Nov 2025 - 31 Oct 2026)",
              f"{len(_posts)} official posts are held as searchable records. The posts most relevant to each question are appended after the question "
              "under 'RETRIEVED POSTS'; quote them with their URL. Per-brand totals, engagement rates, themes, narratives and calls to action for ALL posts "
              "are in PART A3 and PART R; use those for counts and comparisons, not the retrieved sample. Facebook engagement rate = (reactions + shares) / "
              "page followers; Instagram = (likes + comments) / followers; LinkedIn = (reactions + comments + reposts) / followers; Threads = (likes + replies "
              "+ reposts + quotes) / followers; followers shown on 11 Nov 2026: " + json.dumps(META_STATS["followers"])))

# PART J - YouTube video transcripts (Prudential and AXA official channels; auto-generated captions where available)
def _yt_transcripts():
    import openpyxl
    out = []
    for f in sorted(PRU.glob("dataset_youtube-video-transcript_*.xlsx")):
        rows = list(openpyxl.load_workbook(f, read_only=True).active.iter_rows(values_only=True))
        h = rows[0]
        for r in rows[1:]:
            d = dict(zip(h, r))
            txt = " ".join(str(d[k]) for k in h if k and re.fullmatch(r"transcript/\d+/text", k) and d.get(k))
            if not txt and not d.get("description"):
                continue
            desc = re.sub(r"\s+", " ", str(d.get("description") or ""))[:600]
            tx = re.sub(r"\s+", " ", txt)[:3000] or "(transcript not available)"
            out.append(f"[{str(d.get('published_at') or '')[:10]}] {d.get('channel_name')} | {d.get('title')} | {d.get('url')} | views {d.get('view_count')}\n"
                       f"Description: {desc}\nTranscript: {tx}")
    return "\n\n".join(out)


parts.append(("PART J - YOUTUBE VIDEO TRANSCRIPTS (Prudential and AXA official channels, recent product and health videos; captions as published, "
              "some auto-generated in Cantonese)", _yt_transcripts()))
# PART K - extra deep reads and investor data collected on 11 Nov 2026
for _nm, _lab in (("deep_extra_aia.md", "AIA (additional results documents and transcripts)"), ("deep_extra_manulife.md", "Manulife (quarterly reports and call transcripts)"),
                  ("deep_extra_pru_axa.md", "Prudential and AXA (fact sheets, Hong Kong company accounts, AXA results)"),
                  ("axa_calls_hk.md", "AXA analyst calls: Hong Kong passages (machine listening; confirm before quoting)")):
    _fp = PRU / "_report_work" / _nm
    if _fp.exists():
        parts.append((f"PART K - ANALYST DEEP READ (extra): {_lab}. Lower priority than PART A; quote with the stated source and page.", _fp.read_text(encoding="utf-8")))
_mn = PRU / "manulife_newsroom" / "manulife_newsroom.json"
if _mn.exists():
    _rel = json.loads(_mn.read_text(encoding="utf-8"))
    parts.append(("PART L - MANULIFE HONG KONG NEWSROOM (archived copies, 1 Nov 2025 to 22 Sep 2026; live site blocks automated access)",
                  "\n".join(f"[{x['date']}] {x['title']} | {x['url']}\n{(x['full_text'] or x['lead'])[:1500]}" for x in _rel)))

# PART M - Facebook / Instagram ads running in Hong Kong on 11 Nov 2026 (Meta Ad Library)
_adg = {}   # identical ad copy from the same brand is grouped: number of ads, start-date range, buttons, formats, one example link
for rid, cid, txt, url, pub, fmt, lang, xtra in q("""select record_id, company_id, coalesce(original_content, title), source_url, publication_date,
        content_format, language, extra from observations where record_id like 'ADM-%' order by company_id, publication_date"""):
    xx = json.loads(xtra or "{}") or {}
    key = (BRANDS[cid], re.sub(r"\s+", " ", txt or "").strip()[:400])
    g = _adg.setdefault(key, {"n": 0, "first": pub, "last": pub, "cta": set(), "fmt": set(), "url": url,
                              "body": ((TR_DB.get(rid) or {}).get("en") if lang == "zh" else None) or key[1][:200]})
    g["n"] += 1
    g["first"], g["last"] = min(g["first"] or pub, pub or g["first"]), max(g["last"] or pub, pub or g["last"])
    if xx.get("cta"):
        g["cta"].add(xx["cta"])
    if fmt:
        g["fmt"].add(fmt)
_ads = [f"[{b}] {g['n']} ad{'s' if g['n'] > 1 else ''} · started {g['first']}{'' if g['first'] == g['last'] else ' to ' + str(g['last'])} · "
        f"{'/'.join(sorted(g['fmt'])) or 'ad'} · button: {'/'.join(sorted(g['cta'])) or '-'} · {g['body']} · e.g. {g['url']}"
        for (b, _), g in sorted(_adg.items(), key=lambda kv: (kv[0][0], -kv[1]["n"]))]
parts.append(("PART M - FACEBOOK AND INSTAGRAM ADS running in Hong Kong on 11 Nov 2026 (Meta Ad Library lists only active ads for Hong Kong; "
              "spend is not disclosed). Ads with identical copy from the same brand are grouped: number of ads, start dates, format, call-to-action button, "
              "ad text (English summary for Chinese ads), one example library link. "
              "Counts: " + json.dumps({b: v.get("active") for b, v in stats["meta_ads"].items()}), "\n".join(_ads)))

# PART N - Google ads (Ads Transparency Center): no ad text is published, so a summary per brand
_g = {}
for cid, fmt, first in q("select company_id, lower(format), substr(first_shown,1,7) from ad_library where in_scope=1 and shown_in_window=1"):
    d = _g.setdefault(BRANDS[cid], {"total": 0, "formats": {}, "first_shown_by_month": {}})
    d["total"] += 1
    d["formats"][fmt] = d["formats"].get(fmt, 0) + 1
    d["first_shown_by_month"][first] = d["first_shown_by_month"].get(first, 0) + 1
_adv = {BRANDS[c]: u for c, u in q("select company_id, min(substr(url,1,instr(url,'/creative')-1)) from ad_library where in_scope=1 group by 1")}
parts.append(("PART N - GOOGLE ADS shown in Hong Kong in the period (Google Ads Transparency Center; creatives, formats and dates only, no text, spend or impressions)",
              json.dumps({b: dict(v, advertiser_page=_adv.get(b)) for b, v in _g.items()}, ensure_ascii=False, indent=1)))

# PART O - HKEX filings by the three Hong Kong-listed groups
_sub, _rout = [], {}
for x in _hk:
    t = html_lib.unescape(x["title"])
    if _ROUTINE.search(t):
        k = (x["brand"], re.sub(r"\s+", " ", t)[:60])
        _rout[k] = _rout.get(k, 0) + 1
    else:
        _sub.append(f"[{x['brand']}] {x['date'][:10]} · {t} · {x['url']}")
parts.append(("PART O - HONG KONG STOCK EXCHANGE (HKEXnews) FILINGS, 1 Nov 2025 - 11 Nov 2026, Prudential (2378), AIA (1299), Manulife (945); AXA is not listed in Hong Kong. "
              "Substantive filings one per line; routine returns counted below. Group-level documents: use for investor events, not as Hong Kong operating evidence.",
              "\n".join(_sub) + "\n\nRoutine filings (count): " + "; ".join(f"{b} - {t}: {n}" for (b, t), n in sorted(_rout.items()))))

# PART P - market and investor context in plain text (also in PART A3 as JSON)
_ctx = ["HONG KONG INSURANCE AWARDS 2026 (HKFI Monthly Brief No.348, Oct 2026, p.2), winners as named by HKFI:"]
_ctx += [f"- {a['award']}: {a['winner']}{' [' + a['brand'] + ']' if a['brand'] else ''}" for a in stats["awards"]]
_ctx += ["", "MPF STATISTICS (MPFA MPF Schemes Statistical Digest, June 2026; " + (stats["mpf"]["url"] or "") + "):"]
_ctx += [f"- {r['period']} · {r['metric']}: {r['value']:,} {r['unit']} (p.{r['page']})" for r in stats["mpf"]["rows"]]
_inv = stats["inv"]
_ctx += ["", "SHARE PRICES (group-level, not Hong Kong-only; change 3 Nov 2025 - 30 Oct 2026): " + json.dumps(_inv["change"]),
         "Highest close in the window [date, price, % below it at end Oct]: " + json.dumps(_inv["peak"]),
         "Move on 6 Aug 2026 (mainland tax reports): " + json.dumps(_inv["tax_day"]), "Investor events with the share move that day:"]
_ctx += [f"- {e['date']} {e['brand']}: {e['label']} (move {e['move']}%) {e['url']}" for e in _inv["events"]]
_ctx += ["", "HONG KONG COMPANY CAPITAL STRENGTH, end 2025 (capital base / regulatory minimum, from each company's public disclosure statement):"]
_ctx += [f"- {c['brand']} · {c['entity']}: " + (f"{c['ratio']}% (2024 {c['ratio_prev']}%), capital HK${c['capital_base_bn']}bn vs minimum HK${c['required_bn']}bn, "
         f"total assets HK${c['assets_bn']}bn (2024 HK${c['assets_prev_bn']}bn); {c['src']} {c['pages']}" if c.get("ratio") else c.get("note", "")) for c in _inv["capital"]]
_ml = PRU / "hk_investor_data" / "hkex_market_lists"
_rows = []
for _f in sorted(_ml.glob("*.json")):
    for r in json.loads(_f.read_text(encoding="utf-8")):
        if r.get("insurer_in_scope"):
            _rows.append(f"- {_f.stem} · page date {r.get('page_date')} · " + " | ".join(str(v) for k, v in r.items()
                         if k not in ("insurer_in_scope", "page_date", "source_url", "via", "captured_utc") and v)[:220])
_ctx += ["", "HKEX MARKET LISTS (board meetings, dividends and entitlements) rows for the three insurers:"] + (_rows or ["- none"])
# media coverage summary (Hong Kong media stories in the evidence records)
_med = {}
for r in evid:
    if r[3] == "news_article" and WIN[0].replace("-", "") <= str(r[1]) <= WIN[1].replace("-", ""):
        m = _med.setdefault(r[2], {"stories": 0, "chinese": 0, "outlets": {}})
        m["stories"] += 1
        m["chinese"] += r[9].endswith("*")
        o = re.sub(r"^www\.", "", r[11] or "")
        if "linkedin" not in o.lower():
            m["outlets"][o] = m["outlets"].get(o, 0) + 1
_tot = sum(v["stories"] for v in _med.values()) or 1
_ctx += ["", "MEDIA COVERAGE (Hong Kong media stories in the window; share measures visibility, not sentiment):"]
_ctx += [f"- {b}: {v['stories']} stories ({v['stories'] / _tot * 100:.0f}% of all), {v['chinese'] / max(v['stories'], 1) * 100:.0f}% in Chinese; main outlets: "
         + ", ".join(f"{o} ({n})" for o, n in sorted(v["outlets"].items(), key=lambda kv: -kv[1])[:5]) for b, v in sorted(_med.items())]
parts.append(("PART P - MARKET, INVESTOR AND MEDIA CONTEXT (awards, MPF, share prices, Hong Kong capital strength, HKEX lists, media coverage)", "\n".join(_ctx)))
if stats["maps"]:
    parts.append(("PART Q - PHYSICAL FOOTPRINT AND LOCATION REVIEWS (Google Maps listings in Hong Kong's 18 districts; reviews without reviewer identity)",
                  _maps.kb_text(stats["maps"])))
corpus ="\n\n".join(f"========== {t} ==========\n{c}" for t, c in parts)
# Never publish personal contact details: press-release media blocks carry staff mobiles and emails.
corpus = re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "[email removed]", corpus)
corpus = re.sub(r"(\+?852[ -]?)?\b[5-9]\d{3}[ -]?\d{4}\b", "[phone removed]", corpus)
for pat in (r"[\w.+-]+@prudential\.com\.hk", r"(\+?852[ -]?)?\b[5-9]\d{3}[ -]?\d{4}\b"):
    assert not re.search(pat, corpus), f"personal contact detail in knowledge base: {pat}"
(OUT / "private/corpus.md").write_text(corpus, encoding="utf-8")
# Narrated guided tour (player + step list; recorded clips if present, browser voice otherwise)
import subprocess as _sp  # noqa: E402
_sp.run([_sys.executable, "-I", str(HERE / "build_tour.py"), "--inject"], check=True)
print(f"report.html {len(html):,} chars · corpus.md {len(corpus):,} chars (~{len(corpus)//3:,} tokens) · {len(obs)} evidence records")
print("activity totals:", stats["activity_total"], "· official releases:", stats["official"])
