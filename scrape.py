import requests, time, re, json, os
from bs4 import BeautifulSoup

BASE = "https://www.gsmarena.com/"
D = os.path.dirname(os.path.abspath(__file__))
LISTS = D + "/lists"      # brand -> [{name,url}]
SPECS = D + "/specs"      # brand.jsonl, one device per line
STATE = D + "/state.json"
os.makedirs(LISTS, exist_ok=True); os.makedirs(SPECS, exist_ok=True)

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
})
DELAY = 3
START = time.time()
MAX_SECONDS = int(os.environ.get("MAX_SECONDS", "0"))  # 0 = no limit

# API Key-i GitHub Secrets-dən və ya buradakı xxxxxxxx dəyərindən götürür
API_KEY = os.getenv("SCRAPER_API_KEY", "xxxxxxxx")

class TimeUp(Exception):
    pass

def log(*a):
    print(time.strftime("%m-%d %H:%M:%S"), *a, flush=True)

def check_time(extra=0):
    if MAX_SECONDS and time.time() - START + extra > MAX_SECONDS:
        raise TimeUp()

def get(url):
    wait = 120
    payload = {
        'api_key': API_KEY,
        'url': url,
        'render': 'true'
    }
    scraper_api_url = 'https://api.scraperapi.com/'

    for attempt in range(30):
        check_time()
        try:
            # Sorğu birbaşa hədəfə yox, ScraperAPI-yə gedir
            r = S.get(scraper_api_url, params=payload, timeout=60)
        except Exception as e:
            log("ERR", url, e); time.sleep(60); continue
        
        if r.status_code == 200:
            time.sleep(DELAY)
            return r.text
        if r.status_code == 404:
            log("404", url); time.sleep(DELAY); return None
            
        log("HTTP", r.status_code, url, "wait", wait)
        check_time(wait)
        time.sleep(wait); wait = min(wait * 2, 1800)
        
    raise SystemExit("gave up: " + url)

def safe(n):
    return re.sub(r"[^\w.&+-]+", "_", n)

def brands():
    soup = BeautifulSoup(get(BASE + "makers.php3"), "html.parser")
    res = []
    for a in soup.select("div.st-text table a"):
        href = a.get("href", "")
        if not href.endswith(".php"):
            continue
        span = a.find("span")
        cnt = int(re.sub(r"\D", "", span.get_text())) if span else None
        if span: span.extract()
        res.append({"name": a.get_text(" ", strip=True), "url": href, "count": cnt})
    return res

def list_page(html):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.select("div.makers ul li a"):
        sp = a.select_one("strong span") or a.find("strong") or a
        for br in sp.find_all("br"):
            br.replace_with(" ")
        items.append({"name": " ".join(sp.get_text(" ").split()), "url": a.get("href")})
    pages = []
    for a in soup.select("div.nav-pages a"):
        h = a.get("href", "")
        if h and "-p" in h and h not in pages:
            pages.append(h)
    return items, pages

def parse_specs(html, url):
    soup = BeautifulSoup(html, "html.parser")
    t = soup.select_one("h1.specs-phone-name-title")
    out = {"name": t.get_text(" ", strip=True) if t else None, "url": BASE + url, "specs": {}}
    for table in soup.select("div#specs-list table"):
        th = table.find("th")
        cat = th.get_text(" ", strip=True) if th else ""
        sec = out["specs"].setdefault(cat, {})
        last = None
        for tr in table.find_all("tr"):
            ttl = tr.select_one("td.ttl"); nfo = tr.select_one("td.nfo")
            if not nfo:
                continue
            for br in nfo.find_all("br"):
                br.replace_with("\n")
            val = "\n".join(" ".join(l.split()) for l in nfo.get_text().split("\n") if l.strip())
            key = ttl.get_text(" ", strip=True) if ttl else ""
            if not key or key == "\xa0":
                key = last if last else "_"
                sec[key] = (sec.get(key, "") + "\n" + val).strip()
            else:
                if key in sec:
                    sec[key] += "\n" + val
                else:
                    sec[key] = val
                last = key
    return out

def main():
    st = json.load(open(STATE)) if os.path.exists(STATE) else {"brands": None, "listed": [], "done_urls": []}
    if not st["brands"]:
        st["brands"] = brands(); json.dump(st, open(STATE, "w"))
        log("brands:", len(st["brands"]))
    done = set(st["done_urls"])
    # phase 1: lists
    for b in st["brands"]:
        if b["name"] in st["listed"]:
            continue
        items, pages = list_page(get(BASE + b["url"]))
        for p in pages:
            it, _ = list_page(get(BASE + p)); items += it
        json.dump(items, open(f"{LISTS}/{safe(b['name'])}.json", "w"), ensure_ascii=False, indent=1)
        st["listed"].append(b["name"]); json.dump(st, open(STATE, "w"))
        log("LIST", b["name"], len(items), "/", b["count"])
    log("PHASE1 DONE")
    # phase 2: specs
    total = sum(len(json.load(open(f"{LISTS}/{safe(b['name'])}.json"))) for b in st["brands"])
    n = len(done)
    for b in st["brands"]:
        items = json.load(open(f"{LISTS}/{safe(b['name'])}.json"))
        fn = f"{SPECS}/{safe(b['name'])}.jsonl"
        for it in items:
            if it["url"] in done:
                continue
            html = get(BASE + it["url"])
            rec = parse_specs(html, it["url"]) if html else {"name": it["name"], "url": BASE + it["url"], "specs": None, "error": "404"}
            rec["brand"] = b["name"]; rec["list_name"] = it["name"]
            with open(fn, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done.add(it["url"]); n += 1
            st["done_urls"].append(it["url"])
            json.dump(st, open(STATE, "w"))
            if n % 50 == 0:
                log("SPEC", n, "/", total, b["name"])
        json.dump(st, open(STATE, "w"))
        log("BRAND DONE", b["name"], len(items))
    log("FINISHED")
    open(D + "/FINISHED", "w").write(time.strftime("%Y-%m-%d %H:%M:%S"))

try:
    main()
except TimeUp:
    log("TIME LIMIT - stopping, progress saved")
