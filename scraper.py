"""Website and Google News reader. Rules: rubric sections 2, 4 and 11, plus the brief. Every limit comes from Neon (rubric_db.apply).

Website pages
  - robots.txt is read and obeyed for company sites. A page it disallows is a retrieval failure, never a poor-fit signal.
  - Never LinkedIn, never login/app pages (pipeline.fetch_allowed).
  - Max R.MAX_READ_ATTEMPTS tries, only for transient errors (429, 5xx, network) with bounded back-off.
    Blocked (401/403) and not-found pages are NOT retried: retrying unchanged input does not help (rubric 11).
  - A page is not refetched inside S.PAGE_CACHE_DAYS. Results are cached in Neon (page_cache).
Google News
  - Read through the public RSS search feed. Google's terms and robots.txt do NOT allow this kind of automated reading; the
    Growth Lead decided to use it anyway (decision recorded in run_log). So it is kept gentle: one request every 2 seconds,
    every query cached for a day, and a clear User-Agent."""
import re, time, datetime as dt
import urllib.robotparser
from urllib.parse import urlparse, quote
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
import requests
from bs4 import BeautifulSoup
import db
import settings as S
import rubric as R
import pipeline as P          # reuse the tested helpers: extract_text, candidates_from, pick_pages, fetch_allowed

ROBOT_TOKEN = "SprintoResearchBot"
UA = {"User-Agent": f"Mozilla/5.0 (compatible; {ROBOT_TOKEN}/0.3; college project; research only)"}
SITE_GAP, NEWS_GAP = 1.0, 2.0          # minimum seconds between requests to the same host
_LAST = {}

def throttle(host, gap):
    wait = _LAST.get(host, 0) + gap - time.time()
    if wait > 0:
        time.sleep(wait)
    _LAST[host] = time.time()

# ---------------------------------------------------------------- Neon page cache
def cache_get(url, ttl_days):
    r = db.one("SELECT * FROM page_cache WHERE url=%s", (url,))
    if r and (dt.datetime.now(dt.timezone.utc) - r["retrieved_at"]).days <= ttl_days:
        return r
    return None

def cache_put(url, status, text, links, attempts):
    db.x("INSERT INTO page_cache (url,status,text,links,attempts,retrieved_at) VALUES (%s,%s,%s,%s,%s,now()) "
         "ON CONFLICT (url) DO UPDATE SET status=EXCLUDED.status, text=EXCLUDED.text, links=EXCLUDED.links, "
         "attempts=EXCLUDED.attempts, retrieved_at=now()", (url, status, text, db.J(links), attempts))

# ---------------------------------------------------------------- one HTTP read with the rubric's retry rules
def get(url, gap=SITE_GAP, timeout=15):
    """Returns (label, response, attempts). label: ok | blocked | not_found | failed. Max R.MAX_READ_ATTEMPTS tries."""
    host = urlparse(url).netloc
    for attempt in range(1, R.MAX_READ_ATTEMPTS + 1):
        throttle(host, gap)
        try:
            r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
        except requests.RequestException:
            r = None
        if r is not None:
            if r.status_code in (401, 403):
                return "blocked", None, attempt                       # retrieval failure; retrying will not help
            if r.status_code in (404, 410):
                return "not_found", None, attempt                     # invalid input: do not retry unchanged
            if 400 <= r.status_code < 500 and r.status_code != 429:
                return "failed", None, attempt
            if r.status_code < 400:
                ctype = r.headers.get("content-type", "text/html").lower()
                if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
                    return "failed", None, attempt
                return "ok", r, attempt
        if attempt < R.MAX_READ_ATTEMPTS:
            time.sleep(min(2 ** attempt, 8))                          # bounded back-off for 429 / 5xx / network errors
    return "failed", None, R.MAX_READ_ATTEMPTS

# ---------------------------------------------------------------- robots.txt (company sites only)
def robots_allows(url):
    """(allowed, reason). RFC 9309: robots.txt missing or 4xx = no restrictions; unreadable (5xx/network) = treat as disallowed."""
    u = urlparse(url)
    rurl = f"{u.scheme}://{u.netloc}/robots.txt"
    c = cache_get(rurl, 1)
    if c is None:
        label, r, attempts = get(rurl, timeout=10)
        text = r.text[:60000] if r is not None else ""
        if label in ("ok", "blocked", "not_found"):                  # cache decisive answers only
            cache_put(rurl, label, text, None, attempts)
        c = {"status": label, "text": text}
    if c["status"] == "ok":
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(c["text"].splitlines())
        return (rp.can_fetch(ROBOT_TOKEN, url), "robots.txt disallows this page")
    if c["status"] in ("blocked", "not_found"):
        return True, ""
    return False, "robots.txt could not be read"

# ---------------------------------------------------------------- one website page
def fetch_page(url):
    """Returns {url,status,text,links,attempts,retrieved_at,from_cache,site_name}.
    status: ok | blocked | not_found | failed | robots_disallowed (the last four are retrieval failures, not a judgement on fit)."""
    c = cache_get(url, S.PAGE_CACHE_DAYS)
    if c:
        return {"url": url, "status": c["status"], "text": c["text"] or "", "links": c["links"] or [], "attempts": c["attempts"] or 0,
                "retrieved_at": c["retrieved_at"].date().isoformat(), "from_cache": True, "site_name": ""}
    if not P.fetch_allowed(url):
        return {"url": url, "status": "failed", "text": "", "links": [], "attempts": 0, "retrieved_at": S.today(), "from_cache": False,
                "site_name": "", "note": "login or LinkedIn page: never read"}
    ok, why = robots_allows(url)
    if not ok:
        res = {"url": url, "status": "robots_disallowed", "text": "", "links": [], "attempts": 0, "retrieved_at": S.today(),
               "from_cache": False, "site_name": "", "note": why}
        cache_put(url, "robots_disallowed", "", [], 0)
        return res
    label, r, attempts = get(url)
    res = {"url": url, "status": label, "text": "", "links": [], "attempts": attempts, "retrieved_at": S.today(), "from_cache": False, "site_name": ""}
    if r is not None:
        res["url"] = r.url
        soup = BeautifulSoup(r.text, "html.parser")
        from urllib.parse import urljoin
        res["links"] = [[urljoin(r.url, a["href"]), a.get_text(" ", strip=True)[:80]] for a in soup.find_all("a", href=True)][:300]
        res["text"], res["site_name"] = P.extract_text(r.text)
    if label in ("ok", "blocked", "not_found"):                       # transient failures are not cached: they may work later
        cache_put(url, label, res["text"], res["links"], attempts)
    return res

# ---------------------------------------------------------------- Google News (RSS)
def parse_rss(xml_text, max_items=10):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for item in root.findall("./channel/item")[:max_items]:
        title, link = (item.findtext("title") or "").strip(), (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        try:
            published = parsedate_to_datetime(item.findtext("pubDate")).date().isoformat()
        except (TypeError, ValueError):
            published = None
        src = item.find("source")
        desc = BeautifulSoup(item.findtext("description") or "", "html.parser").get_text(" ", strip=True)
        out.append({"headline": title, "link": link, "published": published,
                    "publisher": (src.text or "").strip() if src is not None else None,
                    "publisher_url": src.get("url") if src is not None else None, "description": desc[:300]})
    return out

def google_news(query, max_items=10, ttl_days=1):
    """Search Google News. Returns (items, info) where info says whether it came from cache and how many tries it took."""
    rss = "https://news.google.com/rss/search?q=" + quote(query) + "&hl=en-US&gl=US&ceid=US:en"
    c = cache_get(rss, ttl_days)
    if c and c["status"] == "ok":
        return parse_rss(c["text"], max_items), {"from_cache": True, "attempts": 0, "url": rss}
    label, r, attempts = get(rss, gap=NEWS_GAP, timeout=20)
    if r is None:
        return [], {"from_cache": False, "attempts": attempts, "url": rss, "error": label}
    cache_put(rss, "ok", r.text[:400000], None, attempts)
    return parse_rss(r.text, max_items), {"from_cache": False, "attempts": attempts, "url": rss}

def company_news(domain, brand, months):
    """Dated signals about ONE company. Returns (sources, items, info). sources = {link: 'date | headline | publisher'}."""
    when = f" when:{months // 12}y" if months % 12 == 0 else f" when:{months * 30}d"
    q = f'("{brand}" OR "{domain}") (funding OR raises OR "Series A" OR "Series B" OR "Series C" OR SOC OR "ISO 27001" OR compliance){when}'
    items, info = google_news(q)
    sources = {i["link"]: f"{i['published'] or 'date unknown'} | {i['headline']} | publisher: {i['publisher'] or 'unknown'}" for i in items}
    return sources, items, info
