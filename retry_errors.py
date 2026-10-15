import requests
import time
import re
import json
import os
from pathlib import Path
from bs4 import BeautifulSoup

BASE = "https://www.gsmarena.com/"
D = Path(__file__).resolve().parent
SPECS = D / "specs"
ERRORS = D / "errors.jsonl"
RETRY_LOG = D / "retry_log.jsonl"

SPECS.mkdir(exist_ok=True)

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
})

DELAY = 8.0
API_KEY = os.getenv("SCRAPER_API_KEY")
if not API_KEY:
    raise SystemExit("SCRAPER_API_KEY yoxdur!")

def log(*a):
    print(time.strftime("%m-%d %H:%M:%S"), *a, flush=True)

def get(url: str) -> str | None:
    payload = {
        "api_key": API_KEY,
        "url": url,
        "render": "true",
        "country_code": "us",
    }
    scraper_url = "https://api.scraperapi.com/"

    wait = 60
    for attempt in range(1, 18):
        try:
            r = S.get(scraper_url, params=payload, timeout=100)
        except Exception as e:
            log("ERR", url, type(e).__name__, str(e)[:80])
            time.sleep(min(wait, 200))
            wait = min(wait * 1.6, 1200)
            continue

        if r.status_code == 200:
            time.sleep(DELAY)
            return r.text
        if r.status_code == 404:
            log("404", url)
            time.sleep(DELAY)
            return None

        log(f"HTTP {r.status_code}", url, f"attempt={attempt}", f"wait={int(wait)}s")
        time.sleep(wait)
        wait = min(wait * 1.6, 1500)

    log("GAVE UP", url)
    return None

def safe(name: str) -> str:
    return re.sub(r"[^\w.&+-]+", "_", name).strip("_") or "unknown"

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

    # Təmiz baza üçün ciddi yoxlama
    if not name or not specs or len(specs) < 3:
        return None

    return {
        "name": name,
        "url": url if url.startswith("http") else BASE + url,
        "specs": specs,
    }

def main():
    if not ERRORS.exists():
        log("errors.jsonl tapılmadı. Heç nə yoxdur.")
        return

    errors = []
    with open(ERRORS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    errors.append(json.loads(line))
                except Exception:
                    pass

    if not errors:
        log("errors.jsonl boşdur.")
        return

    log(f"Yenidən cəhd ediləcək qeyd sayı: {len(errors)}")

    success = 0
    still_failed = []

    for i, err in enumerate(errors, 1):
        url = err.get("url", "")
        brand = err.get("brand", "Unknown")
        list_name = err.get("list_name", "")

        if url.startswith(BASE):
            path = url[len(BASE):]
        else:
            path = url

        log(f"[{i}/{len(errors)}] Retry → {brand} | {list_name}")

        full_url = url if url.startswith("http") else BASE + path
        html = get(full_url)
        rec = None

        if html:
            rec = parse_specs(html, path)

        if rec:
            rec["brand"] = brand
            rec["list_name"] = list_name

            fn = SPECS / f"{safe(brand)}.jsonl"
            with open(fn, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

            success += 1
            log(f"  ✓ UĞURLU → {rec['name']}")

            with open(RETRY_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"status": "ok", **rec}, ensure_ascii=False) + "\n")
        else:
            still_failed.append(err)
            log(f"  ✗ Yenə uğursuz")
            with open(RETRY_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"status": "fail", **err}, ensure_ascii=False) + "\n")

    # errors.jsonl-i yenilə
    if still_failed:
        with open(ERRORS, "w", encoding="utf-8") as f:
            for e in still_failed:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        log(f"Hələ də uğursuz qalan: {len(still_failed)} → errors.jsonl yeniləndi")
    else:
        ERRORS.unlink(missing_ok=True)
        log("Bütün xətalar düzəldi! errors.jsonl silindi.")

    log("=" * 50)
    log(f"Retry tamamlandı")
    log(f"Uğurlu bərpa : {success}")
    log(f"Hələ uğursuz : {len(still_failed)}")
    log("=" * 50)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Manual stop")
