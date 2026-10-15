import requests
import time
import re
import json
import os
from pathlib import Path
from bs4 import BeautifulSoup

BASE = "https://www.gsmarena.com/"
D = Path(__file__).resolve().parent
LISTS = D / "lists"
SPECS = D / "specs"
ERRORS = D / "errors.jsonl"
STATE = D / "state.json"

LISTS.mkdir(exist_ok=True)
SPECS.mkdir(exist_ok=True)

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
})

DELAY = 7.0                    # Yavaş və təhlükəsiz
START = time.time()
MAX_SECONDS = int(os.environ.get("MAX_SECONDS", "0"))  # 0 = limitsiz

API_KEY = os.getenv("SCRAPER_API_KEY")
if not API_KEY:
    raise SystemExit("SCRAPER_API_KEY yoxdur! GitHub Secrets-ə əlavə et.")

class TimeUp(Exception):
    pass


def log(*a):
    print(time.strftime("%m-%d %H:%M:%S"), *a, flush=True)


def check_time(extra=0):
    if MAX_SECONDS and (time.time() - START + extra) > MAX_SECONDS:
        raise TimeUp()


def get(url: str) -> str | None:
    payload = {
        "api_key": API_KEY,
        "url": url,
        "render": "true",
        "country_code": "us",
    }
    scraper_url = "https://api.scraperapi.com/"

    wait = 45
    for attempt in range(1, 26):
        check_time()
        try:
            r = S.get(scraper_url, params=payload, timeout=100)
        except Exception as e:
            log("ERR", url, type(e).__name__, str(e)[:100])
            time.sleep(min(wait, 180))
            wait = min(wait * 1.7, 1200)
            continue

        if r.status_code == 200:
            time.sleep(DELAY)
            return r.text

        if r.status_code == 404:
            log("404", url)
            time.sleep(DELAY)
            return None

        log(f"HTTP {r.status_code}", url, f"attempt={attempt}", f"wait={int(wait)}s")
        check_time(wait)
        time.sleep(wait)
        wait = min(wait * 1.7, 1500)

    log("GAVE UP", url)
    return None


def safe(name: str) -> str:
    return re.sub(r"[^\w.&+-]+", "_", name).strip("_") or "unknown"


def save_state(st: dict):
    tmp = STATE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    tmp.replace(STATE)


def brands() -> list[dict]:
    html = get(BASE + "makers.php3")
    if not html:
        raise SystemExit("makers.php3 alınmadı")

    soup = BeautifulSoup(html, "html.parser")
    res = []
    for a in soup.select("div.st-text table a"):
        href = a.get("href", "")
        if not href.endswith(".php"):
            continue
        span = a.find("span")
        cnt = None
        if span:
            m = re.search(r"(\d+)", span.get_text())
            if m:
                cnt = int(m.group(1))
            span.extract()
        name = a.get_text(" ", strip=True)
        if name:
            res.append({"name": name, "url": href, "count": cnt})
    return res


def list_page(html: str) -> tuple[list[dict], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for a in soup.select("div.makers ul li a"):
        sp = a.select_one("strong span") or a.find("strong") or a
        for br in sp.find_all("br"):
            br.replace_with(" ")
        name = " ".join(sp.get_text(" ").split())
        href = a.get("href")
        if name and href:
            items.append({"name": name, "url": href})

    pages = []
    for a in soup.select("div.nav-pages a"):
        h = a.get("href", "")
        if h and "-p" in h and h not in pages:
            pages.append(h)
    return items, pages


def parse_specs(html: str, url: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("h1.specs-phone-name-title")
    name = title_el.get_text(" ", strip=True) if title_el else None

    specs = {}
    for table in soup.select("div#specs-list table"):
        th = table.find("th")
        cat = th.get_text(" ", strip=True) if th else "Other"
        sec = specs.setdefault(cat, {})
        last_key = None

        for tr in table.find_all("tr"):
            ttl = tr.select_one("td.ttl")
            nfo = tr.select_one("td.nfo")
            if not nfo:
                continue

            for br in nfo.find_all("br"):
                br.replace_with("\n")

            val = "\n".join(
                " ".join(line.split())
                for line in nfo.get_text().split("\n")
                if line.strip()
            )

            key = ttl.get_text(" ", strip=True) if ttl else ""
            if not key or key in ("\xa0", "&nbsp;"):
                key = last_key or "_"
                if key in sec:
                    sec[key] = (sec[key] + "\n" + val).strip()
                else:
                    sec[key] = val
            else:
                if key in sec:
                    sec[key] += "\n" + val
                else:
                    sec[key] = val
                last_key = key

    # Təmiz baza üçün: boş və ya çox zəif specs-i qəbul etmirik
    if not name or not specs or len(specs) < 3:
        return None

    return {
        "name": name,
        "url": BASE + url,
        "specs": specs,
    }


def main():
    if STATE.exists():
        st = json.loads(STATE.read_text(encoding="utf-8"))
    else:
        st = {"brands": None, "listed": [], "done_urls": []}

    # ---------- Phase 1: Brands ----------
    if not st["brands"]:
        log("Brendlər yüklənir...")
        st["brands"] = brands()
        save_state(st)
        log("Brendlər:", len(st["brands"]))

    done = set(st["done_urls"])

    # ---------- Phase 2: Phone lists ----------
    for b in st["brands"]:
        if b["name"] in st["listed"]:
            continue

        log("LIST →", b["name"])
        html = get(BASE + b["url"])
        if not html:
            log("LIST UĞURSUZ", b["name"])
            continue

        items, pages = list_page(html)
        for p in pages:
            html2 = get(BASE + p)
            if html2:
                it, _ = list_page(html2)
                items.extend(it)

        # dublikatları sil
        seen = set()
        unique = []
        for it in items:
            if it["url"] not in seen:
                seen.add(it["url"])
                unique.append(it)

        list_file = LISTS / f"{safe(b['name'])}.json"
        list_file.write_text(json.dumps(unique, ensure_ascii=False, indent=2), encoding="utf-8")
        st["listed"].append(b["name"])
        save_state(st)
        log("LIST", b["name"], len(unique), "/", b.get("count"))

    log("PHASE 1 (lists) DONE")

    # ---------- Phase 3: Specs ----------
    total = 0
    for b in st["brands"]:
        f = LISTS / f"{safe(b['name'])}.json"
        if f.exists():
            total += len(json.loads(f.read_text(encoding="utf-8")))

    n = len(done)
    clean = 0
    log(f"Ümumi telefon: {total} | Artıq hazır: {n}")

    for b in st["brands"]:
        list_file = LISTS / f"{safe(b['name'])}.json"
        if not list_file.exists():
            continue

        items = json.loads(list_file.read_text(encoding="utf-8"))
        fn = SPECS / f"{safe(b['name'])}.jsonl"

        for it in items:
            if it["url"] in done:
                continue

            html = get(BASE + it["url"])
            rec = None

            if html:
                rec = parse_specs(html, it["url"])

            if rec:
                rec["brand"] = b["name"]
                rec["list_name"] = it["name"]
                with open(fn, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                clean += 1
            else:
                # Xətalı / boş qeydi ayrıca saxlayırıq
                err = {
                    "brand": b["name"],
                    "list_name": it["name"],
                    "url": BASE + it["url"],
                    "error": "empty_or_failed",
                }
                with open(ERRORS, "a", encoding="utf-8") as f:
                    f.write(json.dumps(err, ensure_ascii=False) + "\n")

            done.add(it["url"])
            st["done_urls"].append(it["url"])
            n += 1

            if n % 10 == 0:
                save_state(st)
                log(f"SPEC {n}/{total} | təmiz: {clean} → {b['name']}")

        save_state(st)
        log("BRAND DONE", b["name"], len(items))

    # ---------- Final statistika ----------
    log("=" * 50)
    log("FINISHED")
    log(f"Ümumi işlənən : {n}")
    log(f"Təmiz qeyd    : {clean}")
    log(f"Xətalı/boş    : {n - clean}")
    log("=" * 50)
    (D / "FINISHED").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except TimeUp:
        log("TIME LIMIT - dayandım, progress saxlanıldı")
    except KeyboardInterrupt:
        log("Manual stop - progress saxlanıldı")
