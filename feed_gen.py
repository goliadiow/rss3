import json, os, re
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from xml.sax.saxutils import escape

USER_AGENT = "ErichRiesenberg itserich@gmail.com"
# Define your phrases here
# Define your phrases here as a list
PHRASES = ["tender offer"]

# This builds the exact string required by the SEC API:
# '"tender offer" OR "liquidating distribution" OR "plan of arrangement"'
QUERY = " OR ".join(f'"{p}"' for p in PHRASES)
FORMS = ["8-K","6-K","DEF 14A","PRE 14A","DFAN14A","DEFM14A","PREM14A","PREC14A","DEFC14A","PREC14C","DEFC14C","SC TO-C","SC TO-T","SC TO-I"]
LOOKBACK_DAYS = 4
CONTEXT_WORDS = 50
MAX_INSTANCES = 10
OUTPUT_FILE = "docs/feed.xml"
CACHE_FILE = "docs/cache.json"

def fetch_hits():
    end = datetime.utcnow().date()
    start = end - timedelta(days=LOOKBACK_DAYS)
    params = {
        "q": QUERY, 
        "forms": ",".join(FORMS), 
        "dateRange": "custom", 
        "startdt": start.isoformat(), 
        "enddt": end.isoformat(), 
        "size": 100 
    }
    url = "https://efts.sec.gov/LATEST/search-index?" + urlencode(params)
    
    # Log the URL so you can copy-paste it into a browser to test
    print(f"DEBUG: Querying SEC API: {url}")
    
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            hits = data.get("hits", {}).get("hits", [])
            # Log the number of hits returned
            print(f"DEBUG: API returned {len(hits)} hits.")
            return hits
    except Exception as e:
        print(f"Request failed: {e}")
        return []
        
def fetch_all_snippets(url, phrases, words=CONTEXT_WORDS, max_instances=MAX_INSTANCES):
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return f"<p>(could not fetch document: {escape(str(e))})</p>"
    
    text = re.sub(r"(?is)<(script|style).*?>.*?(</\1>)", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    
    # Create regex: "tender offer|liquidating distribution"
    pattern = "|".join(re.escape(p) for p in phrases)
    matches = list(re.finditer(pattern, text, re.IGNORECASE))
    
    if not matches:
        return "<p>(no matching phrase found in fetched document — may be in an attachment/exhibit)</p>"
    
    blocks = []
    for i, m in enumerate(matches[:max_instances], start=1):
        before = text[:m.start()].split(" ")
        after = text[m.end():].split(" ")
        snippet_before = escape(" ".join(before[-words:]))
        matched_text = escape(text[m.start():m.end()])
        snippet_after = escape(" ".join(after[:words]))
        blocks.append(f"<p><b>Instance {i} — \"{matched_text}\":</b> ... {snippet_before} "
                      f"<span style=\"background-color: yellow;\">{matched_text}</span> "
                      f"{snippet_after} ...</p>")
    
    if len(matches) > max_instances:
        blocks.append(f"<p>(+{len(matches) - max_instances} more instance(s) not shown)</p>")
    return "".join(blocks)

def build_item(hit, cache):
    src = hit["_source"]
    adsh, filename = hit["_id"].split(":", 1)
    cik = str(int(src["ciks"][0]))
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{filename}"
    # Extract the form type dynamically
    company = src.get("display_names", ["Unknown company"])[0]
    file_date = src.get("file_date", "")
    form_type = src.get("form", "Filing") # Added to get exact form type
    guid = adsh
    
    if guid in cache:
        snippet_html = cache[guid].get("snippet_html", cache[guid].get("snippet", "")) # Fallback for old cache format
    else:
        snippet_html = fetch_all_snippets(url, PHRASES)
        cache[guid] = {"snippet_html": snippet_html}

    # Update the title to use form_type instead of hardcoded 8-K
    return {"title": f"{company} — {form_type} filed {file_date}",
            "link": url,
            "guid": guid,
            "pubdate": file_date,
            "description_html": snippet_html}
            
def rfc822(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.strftime("%a, %d %b %Y %H:%M:%S %z")
    except Exception:
        return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

def build_rss(items):
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0"><channel>',
             '<title>SEC 8-K and 6-K filings mentioning "tender offer"</title>',
             '<link>https://www.sec.gov/edgar/search/</link>',
             '<description>Auto-generated feed of 8-K and 6-K filings containing "tender offer", with all instances highlighted</description>',
             f'<lastBuildDate>{now}</lastBuildDate>']
             
    for i in items:
        parts += ["<item>", f"<title>{escape(i['title'])}</title>",
                  f"<link>{escape(i['link'])}</link>",
                  f"<guid isPermaLink=\"false\">{escape(i['guid'])}</guid>",
                  f"<pubDate>{rfc822(i['pubdate'])}</pubDate>",
                  f"<description><![CDATA[{i['description_html']}]]></description>",
                  "</item>"]
                  
    parts.append("</channel></rss>")
    return "\n".join(parts)

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}

def main():
    cache = load_cache()
    hits = fetch_hits()
    
    # Process items
    items = sorted([build_item(h, cache) for h in hits], key=lambda i: i["pubdate"], reverse=True)
    
    # Log the final count
    print(f"DEBUG: Generated {len(items)} items for the final feed.")
    
    os.makedirs("docs", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(build_rss(items))

    # trim cache to only keys still in the current window, to keep the file small
    live_guids = {i["guid"] for i in items}
    cache = {k: v for k, v in cache.items() if k in live_guids}
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f)

if __name__ == "__main__":
    main()
