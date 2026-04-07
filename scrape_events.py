from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup, NavigableString
from playwright.async_api import async_playwright


def save_df(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def report_rows(df_name: str, df: pd.DataFrame) -> None:
    print(f"{df_name}: {len(df)} rows")


def report_saved(path: Path) -> None:
    print(f"Saved: {path.as_posix()}")

# BILIETAI.LT
def scrape_bilietai_lt_api(max_pages: int = 6) -> pd.DataFrame:
    BASE_URL = "https://www.bilietai.lt/api/v1/events"
    DETAIL_URL = "https://www.bilietai.lt/api/v1/events/{}"

    HEADERS = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": "https://www.bilietai.lt/",
        "Origin": "https://www.bilietai.lt",
    }

    VENUES = [
        "21-61-3876",
        "21-61-7436",
        "21-61-7730",
        "21-61-2709",
        "21-61-4751",
        "21-61-7295",
        "21-61-1882",
        "21-61-2553",
        "21-61-8097",
        "21-61-7929", 
    ]

    def get_event_list():
        items = []

        for page in range(1, max_pages + 1):
            params = [
                ("language", "en"),
                ("statuses", "ON_SALE"),
                ("statuses", "SALE_STOPPED"),
                ("sortBy", "date"),
                ("sortOrder", "ASC"),
                ("pageSize", "36"),
                ("page", str(page)),
            ]

            for v in VENUES:
                params.append(("venues", v))

            r = requests.get(BASE_URL, params=params, headers=HEADERS)
            data = r.json()

            page_items = data.get("items", [])
            print(f"Page {page}: {len(page_items)} events")

            if not page_items:
                break

            items.extend(page_items)

        return items

    def get_event_details(event_id):
        r = requests.get(
            DETAIL_URL.format(event_id),
            params={"language": "en"},
            headers=HEADERS,
        )
        if r.status_code != 200:
            return None
        return r.json()

    rows = []
    items = get_event_list()

    for e in items:
        title = e.get("title", "") or e.get("name", "")
        title_lower = title.lower()

        # 🚫 FILTERS
        if any(k in title_lower for k in ["dovan", "parking", "abonement"]):
            continue

        event_id = e.get("id")
        detail = get_event_details(event_id)

        if not detail:
            continue

        venue = detail.get("venue", {})
        location = venue.get("name", "")
        city = venue.get("city", "")

        start = detail.get("eventStartAt", "")

        date = ""
        time = ""

        if start:
            try:
                dt = datetime.fromisoformat(start.replace("Z", ""))
                date = dt.date().isoformat()
                time = dt.strftime("%H:%M")
            except:
                pass

        event_link = f"https://www.bilietai.lt/renginiai/{event_id}"

        rows.append(
            {
                "title": title,
                "location": location,
                "city": city,
                "start_date": date,
                "start_time": time,
                "event_link": event_link,
                "ticket_link": event_link, 
                "scraped_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            }
        )

    df = pd.DataFrame(rows)

    return (
        df.drop_duplicates(subset=["title", "start_date", "start_time", "location"])
        .sort_values(["start_date", "start_time"])
        .reset_index(drop=True)
    )

# Twinsbet Arena
async def scrape_twinsbet() -> pd.DataFrame:
    url = "https://twinsbetarena.lt/en/events/"
    rows = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()

        await page.goto(url, timeout=90000)
        await page.wait_for_timeout(2000)

        # scroll
        for _ in range(10):
            await page.mouse.wheel(0, 5000)
            await page.wait_for_timeout(500)

        links = await page.locator("a[href*='/renginys/']").evaluate_all(
            "els => els.map(e => e.href)"
        )

        links = list(set(links))

        for link in links:
            try:
                await page.goto(link, timeout=90000)
                await page.wait_for_timeout(1000)
            except:
                continue

            text = await page.locator("body").inner_text()

            title = ""
            try:
                title = await page.locator("h1").inner_text()
            except:
                pass

            date_match = re.search(r"\d{4}-\d{2}-\d{2}", text)
            time_match = re.search(r"\d{1,2}:\d{2}", text)

            rows.append({
                "event_name": title.strip(),
                "location": "Twinsbet Arena",
                "city": "Vilnius",
                "date": date_match.group(0) if date_match else "",
                "time": time_match.group(0) if time_match else "",
                "event_link": link,
            })

        await browser.close()

    return pd.DataFrame(rows).drop_duplicates()

# Šiaulių Arena
def scrape_siauliuarena() -> pd.DataFrame:
    base_url = "https://siauliuarena.lt"
    list_url = base_url + "/renginiai/"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    def norm(text):
        if not text:
            return ""
        text = " ".join(text.split())
        return unicodedata.normalize("NFC", text)

    resp = requests.get(list_url, headers=headers, timeout=30)
    resp.raise_for_status()

    html = resp.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")

    event_urls = set()
    for a in soup.select("a[href*='/event/']"):
        href = a.get("href")
        if not href:
            continue
        url = urljoin(base_url, href.split("?")[0].split("#")[0])
        event_urls.add(url)

    event_urls = sorted(event_urls)

    date_re = re.compile(r"\d{4}-\d{2}-\d{2}")
    time_re = re.compile(r"\b\d{1,2}:\d{2}\b")

    records: list[dict] = []

    for url in event_urls:
        try:
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
        except Exception:
            continue

        html_evt = r.content.decode("utf-8", errors="replace")
        soup_evt = BeautifulSoup(html_evt, "lxml")

        title_tag = soup_evt.find(["h1", "h2"])
        event_name = norm(title_tag.get_text()) if title_tag else ""
        if not event_name and soup_evt.title:
            event_name = norm(soup_evt.title.get_text())

        text = soup_evt.get_text("\n", strip=True)
        lines = [l for l in text.split("\n") if l.strip()]

        date_str = ""
        time_str = ""

        for idx, line in enumerate(lines):
            m_date = date_re.search(line)
            if not m_date:
                continue

            date_str = m_date.group(0)
            m_time = time_re.search(line)
            if m_time:
                time_str = m_time.group(0)
            else:
                for j in range(idx + 1, min(idx + 5, len(lines))):
                    m_time2 = time_re.search(lines[j])
                    if m_time2:
                        time_str = m_time2.group(0)
                        break
            break

        if not event_name:
            continue

        records.append(
            {
                "event_name": event_name,
                "location": "Šiaulių Arena",
                "city": "Šiauliai",
                "date": date_str,
                "time": time_str,
                "event_link": url,
            }
        )

    df = pd.DataFrame(records).drop_duplicates(subset=["event_name", "date", "time", "location"])
    if df.empty:
        return df
    return df.sort_values(["date", "time"], ascending=[True, True]).reset_index(drop=True)


# Kalnapilio Arena
def scrape_kalnapilioarena() -> pd.DataFrame:
    url = "https://kalnapilisarena.lt/renginiai/"
    base_url = "https://kalnapilisarena.lt"
    headers = {"User-Agent": "Mozilla/5.0 Chrome/120.0"}

    def norm(text):
        return unicodedata.normalize("NFC", " ".join(text.split())) if text else ""

    lt_months = {
        "sausio": "01",
        "vasario": "02",
        "kovo": "03",
        "balandžio": "04",
        "gegužės": "05",
        "birželio": "06",
        "liepos": "07",
        "rugpjūčio": "08",
        "rugsėjo": "09",
        "spalio": "10",
        "lapkričio": "11",
        "gruodžio": "12",
    }

    dt_re = re.compile(r"(\d{4})\s+([^\s]+)\s+(\d{1,2})\s+d\.\s+(\d{1,2}:\d{2})", re.IGNORECASE)

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.content, "lxml")
    events: list[dict] = []

    for node in soup.find_all(string=dt_re):
        m = dt_re.search(norm(node))
        if not m:
            continue

        year, month_word, day, time = m.groups()
        month = lt_months.get(month_word.lower())
        if not month:
            continue

        a_tag = node.find_previous("a")
        if not a_tag:
            continue

        title = norm(a_tag.get_text())
        event_link = urljoin(base_url, a_tag.get("href", ""))

        events.append(
            {
                "event_name": title,
                "location": "Kalnapilio Arena",
                "city": "Panevėžys",
                "date": f"{year}-{month}-{int(day):02d}",
                "time": time,
                "event_link": event_link,
            }
        )

    return (
        pd.DataFrame(events)
        .drop_duplicates(subset=["event_name", "date", "time", "location"])
        .reset_index(drop=True)
    )


# Švyturio Arena
def scrape_svyturioarena() -> pd.DataFrame:
    url = "https://www.svyturioarena.lt/en/renginiai/"
    base_url = "https://www.svyturioarena.lt"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    def norm(text):
        if not text:
            return ""
        text = " ".join(text.split())
        return unicodedata.normalize("NFC", text)

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    html = resp.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")

    dt_re = re.compile(r"^\s*(\d{4}/\d{2}/\d{2})\s*/\s*(\d{1,2}:\d{2})\s*$")

    def is_title_candidate(text):
        text = norm(text)
        if not text:
            return False
        if dt_re.match(text):
            return False
        if text.startswith("Ticket price:"):
            return False
        if text in ("To buy a ticket", "More"):
            return False
        if text.startswith("Image:"):
            return False
        if not any(ch.isalpha() for ch in text):
            return False
        if len(text) > 120:
            return False
        return True

    events: list[dict] = []

    for node in soup.find_all(string=dt_re):
        m = dt_re.match(node.strip())
        if not m:
            continue

        date_raw = m.group(1)
        time_str = m.group(2)
        date_str = date_raw.replace("/", "-")

        title = None
        for el in node.next_elements:
            if isinstance(el, NavigableString):
                cand = norm(el)
                if is_title_candidate(cand):
                    title = cand
                    break

        if not title:
            continue

        event_link = ""
        link_tag = node.find_previous("a", href=True)
        if link_tag:
            event_link = urljoin(base_url, link_tag["href"])

        events.append(
            {
                "event_name": title,
                "location": "Švyturio Arena",
                "city": "Klaipėda",
                "date": date_str,
                "time": time_str,
                "event_link": event_link,
            }
        )

    return (
        pd.DataFrame(events)
        .drop_duplicates(subset=["event_name", "date", "time", "location"])
        .reset_index(drop=True)
    )


# Compensa
def scrape_compensa(pages_to_check: int = 6) -> pd.DataFrame:
    base_url = "https://www.compensakoncertusale.lt/events"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    def norm(text):
        if not text:
            return ""
        text = " ".join(text.split())
        return unicodedata.normalize("NFC", text)

    lt_months = {
        "sau": "01",
        "vas": "02",
        "kov": "03",
        "bal": "04",
        "geg": "05",
        "bir": "06",
        "lie": "07",
        "rgp": "08",
        "rugp": "08",
        "rug": "09",
        "rugs": "09",
        "spa": "10",
        "lap": "11",
        "gru": "12",
    }

    event_re = re.compile(
        r"(?P<title>.+?)\s+"
        r"(?P<day>\d{1,2})\s+"
        r"(?P<month>[A-Za-zĄČĘĖĮŠŲŪŽąčęėįšųūž]{3,6})\s+"
        r"(?P<time>\d{1,2}:\d{2})\s+"
        r".*?(?P<link>www\.(?:bilietai|kakava|manobilietas|ticketshop|medusa)\.lt\S*)",
        re.UNICODE,
    )

    def guess_year(month_num: int) -> int:
        today = datetime.today().date()
        year = today.year
        if month_num - today.month < -6:
            year += 1
        return year

    def clean_title(raw_title: str) -> str:
        t = norm(raw_title)
        t = re.sub(r"\s*\|\s*Vilnius$", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*\(Vilnius\)$", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*COMPENSA\s*$", "", t, flags=re.IGNORECASE)
        return t.strip(" -|")

    urls = [base_url] + [f"{base_url}?page={i}" for i in range(1, pages_to_check)]
    events: list[dict] = []

    for url in urls:
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
        except Exception:
            continue

        html = resp.content.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")

        event_page_links: list[str] = []
        seen = set()

        href_re = re.compile(
            r"^(?:https?://(?:www\.)?compensakoncertusale\.lt)?(?P<path>/renginiai/[^/?#]+)(?:/)?$",
            re.IGNORECASE,
        )

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            m_href = href_re.match(href)
            if not m_href:
                continue
            full = "https://www.compensakoncertusale.lt" + m_href.group("path")
            if full in seen:
                continue
            seen.add(full)
            event_page_links.append(full)

        def slugify(s: str) -> str:
            s = unicodedata.normalize("NFKD", norm(s).lower())
            s = "".join(ch for ch in s if not unicodedata.combining(ch))
            s = re.sub(r"[^a-z0-9]+", "-", s)
            return re.sub(r"-+", "-", s).strip("-")

        link_idx = 0
        last_link_for_title: dict[str, str] = {}

        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+", " ", text)

        idx = text.lower().find("praėję renginiai")
        if idx != -1:
            text = text[:idx]

        for m in event_re.finditer(text):
            raw_title = m.group("title")
            day = int(m.group("day"))
            month_raw = m.group("month").lower().strip(".")
            time_str = m.group("time")

            key4 = month_raw[:4]
            key3 = month_raw[:3]
            month_num_str = lt_months.get(key4) or lt_months.get(key3)
            if not month_num_str:
                continue

            month_num = int(month_num_str)
            year = guess_year(month_num)
            date_iso = f"{year}-{month_num_str}-{day:02d}"

            title = clean_title(raw_title)
            if re.search(r"\brenginiai\b", title, re.IGNORECASE):
                continue

            ticket_link = m.group("link")
            if not ticket_link.startswith("http"):
                ticket_link = "https://" + ticket_link

            event_link = None
            title_slug = slugify(title)

            for j in range(link_idx, min(link_idx + 8, len(event_page_links))):
                if title_slug and title_slug in event_page_links[j]:
                    event_link = event_page_links[j]
                    link_idx = j + 1
                    break

            if event_link is None and link_idx < len(event_page_links):
                event_link = event_page_links[link_idx]
                link_idx += 1

            if event_link is None:
                event_link = last_link_for_title.get(title) or ticket_link

            last_link_for_title[title] = event_link

            events.append(
                {
                    "event_name": title,
                    "location": "Compensa koncertų salė",
                    "city": "Vilnius",
                    "date": date_iso,
                    "time": time_str,
                    "event_link": event_link,
                }
            )

    df = pd.DataFrame(events)
    if df.empty:
        return df

    return (
        df.drop_duplicates(subset=["event_name", "date", "time", "location"])
        .sort_values(["date", "time"], ascending=[True, True])
        .reset_index(drop=True)
    )


# Žalgirio Arena
def scrape_zalgirioarena() -> pd.DataFrame:
    url = "https://www.zalgirioarena.lt/en/events"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    def norm(text):
        if not text:
            return ""
        text = " ".join(text.split())
        return unicodedata.normalize("NFC", text)

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    html = resp.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")

    date_re = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*$")
    time_re = re.compile(r"^\s*(\d{1,2}:\d{2})\s*$")

    locations = {"Zalgirio Arena", "SDG amphitheatre", "Outside", "Foyer"}
    categories = {
        "Concert",
        "Conference",
        "EuroLeague",
        "Exhibition",
        "Fair",
        "LKL/KMT",
        "Other",
        "Performance",
        "Sport",
        "Stand-up",
    }

    def is_valid_title(text: str) -> bool:
        text = norm(text)
        if not text:
            return False
        if text in locations or text in categories:
            return False
        if text in ("Buy ticket", "Information"):
            return False

        bad_prefixes = (
            "Duration:",
            "Doors open",
            "Organizer:",
            "From ",
            "Photography",
            "Only allowed",
            "Children",
            "Free admission",
            "No free admission",
            "New AUDI club members",
            "Audi club members",
            "Nuo ",
            "Vaikai",
            "Neįgalieji",
        )
        if any(text.startswith(p) for p in bad_prefixes):
            return False
        if len(text) > 120:
            return False
        return True

    events: list[dict] = []

    for date_node in soup.find_all(string=date_re):
        m_date = date_re.match(date_node.strip())
        if not m_date:
            continue
        date_str = m_date.group(1)

        time_node = date_node.find_next(string=time_re)
        if not time_node:
            continue
        time_str = time_node.strip()

        loc_node = time_node.find_next(string=lambda s: s and s.strip() in locations)
        if not loc_node:
            continue
        location = norm(loc_node)

        cat_node = loc_node.find_next(string=lambda s: s and s.strip() in categories)
        if not cat_node:
            continue

        title = None
        for el in cat_node.next_elements:
            if isinstance(el, NavigableString):
                txt = el.strip()
                if not txt:
                    continue
                if txt in ("Buy ticket", "Information"):
                    break
                if is_valid_title(txt):
                    title = norm(txt)
                    break

        if not title:
            continue

        event_link = ""
        event_container = (
            date_node.find_parent(attrs={"role": "listitem"})
            or date_node.find_parent("li")
            or date_node.find_parent("div")
        )

        if event_container:
            for a in event_container.find_all("a", href=True):
                href = (a.get("href") or "").strip()
                label = a.get_text(" ", strip=True).lower()
                if label == "buy ticket" and href and href != "#":
                    event_link = requests.compat.urljoin(url, href)
                    break

            if not event_link:
                for a in event_container.find_all("a", href=True):
                    href = (a.get("href") or "").strip()
                    if not href or href == "#":
                        continue
                    href_l = href.lower()
                    if any(x in href_l for x in ["koobin", "kakava", "bilietai", "ticketshop", "manobilietas"]):
                        event_link = requests.compat.urljoin(url, href)
                        break

        events.append(
            {
                "event_name": title,
                "location": location,
                "city": "Kaunas",
                "date": date_str,
                "time": time_str,
                "event_link": event_link,
            }
        )

    return (
        pd.DataFrame(events)
        .drop_duplicates(subset=["event_name", "date", "time", "location"])
        .reset_index(drop=True)
    )

# Kultūros uostas - Klaipedos miesto renginiai
def scrape_kulturosuostas_festivaliai(months_forward: int = 6) -> pd.DataFrame:
    BASE_URL = "https://kulturosuostas.lt"
    AJAX_URL = f"{BASE_URL}/wp-admin/admin-ajax.php"

    HEADERS = {
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{BASE_URL}/renginiai-klaipedoje/",
    }

    FESTIVAL_LABEL_ID = "3203"
    CALENDAR_ID = "724"

    TIME_RANGE_RE = re.compile(r"^\d{1,2}:\d{2}\s*(?:-|–)\s*\d{1,2}:\d{2}$")
    TIME_SINGLE_RE = re.compile(r"^\d{1,2}:\d{2}$")
    ALL_DAY_RE = re.compile(r"^vis", re.IGNORECASE)

    MONTH_HEADER_RE = re.compile(
        r"(sausio|vasario|kovo|balandžio|gegužės|birželio|liepos|rugpjūčio|rugsėjo|spalio|lapkričio|gruodžio)\s+renginiai",
        re.IGNORECASE
    )

    def looks_like_time(s: str) -> bool:
        s = (s or "").strip()
        return bool(TIME_RANGE_RE.match(s) or TIME_SINGLE_RE.match(s) or ALL_DAY_RE.match(s))

    def is_month_header(s: str) -> bool:
        s = (s or "").strip().lower()
        return ("renginiai" in s and MONTH_HEADER_RE.search(s) is not None) or s.endswith("renginiai")

    def iter_future_months(start: datetime, months_forward: int):
        y0, m0 = start.year, start.month
        for i in range(months_forward):
            y = y0 + (m0 - 1 + i) // 12
            m = (m0 - 1 + i) % 12 + 1
            yield y, m

    def safe_event_date(year: int, month: int, day: int):
        try:
            return datetime(year, month, day)
        except ValueError:
            return None

    def smallest_container_with_single_h4(h4):
        node = h4
        while node and getattr(node, "name", None) not in ("body", "html"):
            if getattr(node, "name", None) in ("div", "li", "article", "section"):
                if len(node.find_all("h4")) == 1:
                    return node
            node = node.parent
        return h4.parent

    def extract_time_and_venue(container, event_name: str):
        strings = list(container.stripped_strings)
        if not strings:
            return None, None

        title_idx = None
        for i, s in enumerate(strings):
            if s == event_name:
                title_idx = i
                break
        if title_idx is None:
            for i, s in enumerate(strings):
                if event_name and event_name in s:
                    title_idx = i
                    break
        if title_idx is None:
            title_idx = 0

        time_text = None
        for s in strings[:title_idx]:
            if looks_like_time(s):
                time_text = s

        venue_text = None
        for s in strings[title_idx + 1:]:
            if not s or s == event_name:
                continue
            if looks_like_time(s):
                continue
            if is_month_header(s):
                continue
            if s.strip().lower() == "festivaliai":
                continue
            venue_text = s
            break

        return time_text, venue_text

    today = datetime.today()
    events = []

    session = requests.Session()
    session.headers.update(HEADERS)

    for year, month in iter_future_months(today.replace(day=1), months_forward):
        payload = {
            "action": "mec_full_calendar_switch_skin",
            "skin": "monthly",
            "atts[id]": CALENDAR_ID,
            "atts[skin]": "full_calendar",
            "atts[sf_status]": "1",
            "sf[label]": FESTIVAL_LABEL_ID,
            "sf[year]": str(year),
            "sf[month]": str(month),
            "sf[event_status]": "all",
            "apply_sf_date": "1",
        }

        r = session.post(AJAX_URL, data=payload, timeout=20)
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")
        current_day = None

        for el in soup.find_all(["h3", "h4"]):
            if el.name == "h3":
                t = el.get_text(strip=True)
                current_day = int(t) if t.isdigit() else None
                continue

            a = el.find("a", href=True)
            if not a or not current_day:
                continue

            event_name = a.get_text(strip=True)
            if not event_name:
                continue

            link = a["href"]
            if link and not link.startswith("http"):
                link = BASE_URL + link

            event_dt = safe_event_date(year, month, current_day)
            if not event_dt:
                continue

            if event_dt.date() < today.date():
                continue

            container = smallest_container_with_single_h4(el)
            time_text, venue_text = extract_time_and_venue(container, event_name)

            if venue_text and is_month_header(venue_text):
                venue_text = None

            events.append({
                "event_name": event_name,
                "location": venue_text,
                "city": "Klaipėda",
                "date": event_dt.strftime("%Y-%m-%d"),
                "time": time_text,
                "event_link": link,
            })

    df = pd.DataFrame(events)

    if df.empty:
        return df

    return (
        df.drop_duplicates(subset=["event_name", "date", "event_link"])
        .sort_values(["date", "time", "event_name"], na_position="last")
        .reset_index(drop=True)
    )

async def main() -> None:
    out_dir = Path("output")

    # Bilietai.lt
    df_bilietai_lt = scrape_bilietai_lt_api(max_pages=6)
    report_rows("df_bilietai_lt", df_bilietai_lt)
    save_df(df_bilietai_lt, out_dir / "df_bilietai_lt.csv")
    report_saved(out_dir / "df_bilietai_lt.csv")

    # Twinsbet Arena
    df_twinsbet = await scrape_twinsbet()
    report_rows("df_twinsbet", df_twinsbet)
    save_df(df_twinsbet, out_dir / "df_twinsbet.csv")
    report_saved(out_dir / "df_twinsbet.csv")

    # Šiaulių Arena
    df_siauliuarena = scrape_siauliuarena()
    report_rows("df_siauliuarena", df_siauliuarena)
    save_df(df_siauliuarena, out_dir / "df_siauliuarena.csv")
    report_saved(out_dir / "df_siauliuarena.csv")

    # Kalnapilio Arena
    df_kalnapilioarena = scrape_kalnapilioarena()
    report_rows("df_kalnapilioarena", df_kalnapilioarena)
    save_df(df_kalnapilioarena, out_dir / "df_kalnapilioarena.csv")
    report_saved(out_dir / "df_kalnapilioarena.csv")

    # Švyturio Arena
    df_svyturioarena = scrape_svyturioarena()
    report_rows("df_svyturioarena", df_svyturioarena)
    save_df(df_svyturioarena, out_dir / "df_svyturioarena.csv")
    report_saved(out_dir / "df_svyturioarena.csv")

    # Compensa
    df_compensa = scrape_compensa(pages_to_check=6)
    report_rows("df_compensa", df_compensa)
    save_df(df_compensa, out_dir / "df_compensa.csv")
    report_saved(out_dir / "df_compensa.csv")

    # Žalgirio Arena
    df_zalgirioarena = scrape_zalgirioarena()
    report_rows("df_zalgirioarena", df_zalgirioarena)
    save_df(df_zalgirioarena, out_dir / "df_zalgirioarena.csv")
    report_saved(out_dir / "df_zalgirioarena.csv")

    # Kultūros uostas - Klaipedos miesto renginiai
    df_klaipeda = scrape_kulturosuostas_festivaliai()
    report_rows("df_klaipeda", df_klaipeda)
    save_df(df_klaipeda, out_dir / "df_klaipeda.csv")
    report_saved(out_dir / "df_klaipeda.csv")


if __name__ == "__main__":
    asyncio.run(main())
