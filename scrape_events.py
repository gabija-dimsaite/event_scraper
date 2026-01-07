from __future__ import annotations

# Imports
import asyncio
import json
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


# Bilietai.lt
async def scrape_bilietai_lt(pages_to_check: int = 6) -> pd.DataFrame:
    base_url = (
        "https://www.bilietai.lt/eng/tickets/visi/"
        "category:1002,1005,1006/"
        "status:insales,sold_out/"
        "order:date,asc/"
        "page:{}/"
        "venue:294187,45371,103680,208473,39028,39404,41503,"
        "39103,84421,40473,39368,39220,317656,40052,47301,"
        "45058,90741,190114,39105,45041/"
    )

    site_root = "https://www.bilietai.lt"

    event_page_re = re.compile(r"/(?:eng|lit)/tickets/.+-\d+")
    abs_event_page_re = re.compile(r"https?://(?:www\.)?bilietai\.lt/(?:eng|lit)/tickets/.+-\d+")
    time_re = re.compile(r"\b(\d{1,2}:\d{2})\b")

    def abs_url(href: str) -> str:
        return urljoin(site_root, href)

    def split_dt(s: str) -> tuple[str, str]:
        if not s:
            return "", ""
        if "T" in s:
            d, t = s.split("T", 1)
            return d.strip(), t.strip()[:5]
        return s.strip(), ""

    def first_time_from_text(text: str) -> str:
        m = time_re.search(text or "")
        return m.group(1) if m else ""

    def iter_event_jsonld(soup: BeautifulSoup):
        for s in soup.find_all("script", type="application/ld+json"):
            raw = s.get_text(strip=True)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue

            objs = data if isinstance(data, list) else [data]
            for obj in objs:
                if isinstance(obj, dict) and obj.get("@type") == "Event":
                    yield s, obj

    def find_container_and_event_link(script_tag):
        a = script_tag.find_parent("a", href=True)
        if a:
            href = a["href"]
            if href.startswith("/") and event_page_re.search(href):
                return a, abs_url(href)
            if abs_event_page_re.search(href):
                return a, href.split("#")[0]

        node = script_tag
        for _ in range(10):
            node = node.parent
            if not node:
                break

            links = []
            for a2 in node.find_all("a", href=True):
                href = a2["href"]
                if href.startswith("/") and event_page_re.search(href):
                    links.append(abs_url(href))
                elif abs_event_page_re.search(href):
                    links.append(href.split("#")[0])

            uniq = list(dict.fromkeys(links))
            if len(uniq) == 1:
                return node, uniq[0]

        return None, ""

    def row_from_event(e: dict, container, event_page_link: str) -> dict:
        start_raw = e.get("startDate", "")
        start_date, start_time = split_dt(start_raw)

        container_text = container.get_text(" ", strip=True) if container else ""
        if not start_time:
            start_time = first_time_from_text(container_text)

        loc = e.get("location", {}) if isinstance(e.get("location"), dict) else {}
        addr = loc.get("address", {}) if isinstance(loc.get("address"), dict) else {}

        offers = e.get("offers", {}) if isinstance(e.get("offers"), dict) else {}
        ticket_link = offers.get("url", "")
        if isinstance(ticket_link, str) and ticket_link.startswith("/"):
            ticket_link = abs_url(ticket_link)

        return {
            "title": e.get("name", ""),
            "location": loc.get("name", ""),
            "city": addr.get("addressLocality", ""),
            "start_date": start_date,
            "start_time": start_time,
            "event_link": event_page_link,
            "ticket_link": ticket_link,
            "scraped_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }

    rows: list[dict] = []
    series_links: set[str] = set()
    series_fallback: dict[str, dict] = {}
    seen_event_pages: set[str] = set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        page = await context.new_page()

        for page_num in range(1, pages_to_check + 1):
            url = base_url.format(page_num)
            try:
                await page.goto(url, wait_until="networkidle", timeout=90000)
            except Exception:
                continue

            soup = BeautifulSoup(await page.content(), "html.parser")

            for script_tag, e in iter_event_jsonld(soup):
                container, event_page_link = find_container_and_event_link(script_tag)
                if not event_page_link or event_page_link in seen_event_pages:
                    continue

                seen_event_pages.add(event_page_link)
                r = row_from_event(e, container, event_page_link)

                is_series = (not r["location"]) or ("Different venues" in (container.get_text() if container else ""))
                if is_series:
                    series_links.add(event_page_link)
                    series_fallback[event_page_link] = r
                else:
                    rows.append(r)

        for series_url in series_links:
            try:
                await page.goto(series_url, wait_until="networkidle", timeout=90000)
            except Exception:
                if series_url in series_fallback:
                    rows.append(series_fallback[series_url])
                continue

            soup = BeautifulSoup(await page.content(), "html.parser")
            found = False

            for script_tag, e in iter_event_jsonld(soup):
                loc = e.get("location", {})
                offers = e.get("offers", {})
                if not isinstance(loc, dict) or not loc.get("name"):
                    continue
                if not isinstance(offers, dict) or not offers.get("url"):
                    continue

                container, link = find_container_and_event_link(script_tag)
                if not link or link == series_url:
                    continue

                rows.append(row_from_event(e, container, link))
                found = True

            if not found and series_url in series_fallback:
                rows.append(series_fallback[series_url])

        await context.close()
        await browser.close()

    return (
        pd.DataFrame(rows)
        .drop_duplicates(subset=["title", "start_date", "start_time", "location"])
        .reset_index(drop=True)
    )


# Twinsbet Arena
def scrape_twinsbet() -> pd.DataFrame:
    url = "https://twinsbetarena.lt/en/events/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        )
    }

    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "lxml")
    events: list[dict] = []

    date_nodes = soup.find_all(string=re.compile(r"\d{4}-\d{2}-\d{2}"))

    def is_valid_name_text(s: str) -> bool:
        if not s or not s.strip():
            return False
        text = s.strip()
        if "Price from" in text or "Buy a ticket" in text:
            return False
        if text in ("Category", "All categories", "Date"):
            return False
        if re.search(r"\d{4}-\d{2}-\d{2}", text):
            return False
        return True

    for date_node in date_nodes:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", str(date_node))
        if not m:
            continue

        date_str = m.group(1)
        time_node = date_node.find_next(string=re.compile(r"^\s*\d{2}:\d{2}\s*$"))
        time_str = time_node.strip() if time_node else ""

        name_node = date_node.find_previous(string=is_valid_name_text)
        event_name = " ".join(name_node.strip().split()) if name_node else ""

        event_link = ""
        link_tag = date_node.find_previous("a", href=True)
        if link_tag:
            event_link = requests.compat.urljoin(url, link_tag["href"])

        events.append(
            {
                "event_name": event_name,
                "location": "Twinsbet Arena",
                "city": "Vilnius",
                "date": date_str,
                "time": time_str,
                "event_link": event_link,
            }
        )

    return pd.DataFrame(events).drop_duplicates().reset_index(drop=True)


# Kakava.lt
async def scrape_kakava_lt(scroll_rounds: int = 20) -> pd.DataFrame:
    events: list[dict] = []
    start_url = "https://www.kakava.lt/en/events"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await browser.new_context()
        page = await ctx.new_page()

        await page.goto(start_url, wait_until="domcontentloaded", timeout=90000)

        for _ in range(scroll_rounds):
            await page.mouse.wheel(0, 5000)
            await page.wait_for_timeout(1000)

        cards = await page.query_selector_all("a[href*='/event/']")
        for c in cards:
            href = await c.get_attribute("href")
            title = (await c.inner_text() or "").strip()
            if href and title:
                events.append({"title": title, "url": "https://www.kakava.lt" + href})

        await ctx.close()
        await browser.close()

    df = pd.DataFrame(events).drop_duplicates(subset=["url"]).reset_index(drop=True)
    df["timestamp"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    return df


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


async def main() -> None:
    out_dir = Path("output")

    # Bilietai.lt
    df_bilietai_lt = await scrape_bilietai_lt(pages_to_check=6)
    report_rows("df_bilietai_lt", df_bilietai_lt)
    save_df(df_bilietai_lt, out_dir / "df_bilietai_lt.csv")
    report_saved(out_dir / "df_bilietai_lt.csv")

    # Twinsbet Arena
    df_twinsbet = scrape_twinsbet()
    report_rows("df_twinsbet", df_twinsbet)
    save_df(df_twinsbet, out_dir / "df_twinsbet.csv")
    report_saved(out_dir / "df_twinsbet.csv")

    # Kakava.lt
    df_kakava_lt = await scrape_kakava_lt(scroll_rounds=20)
    report_rows("df_kakava_lt", df_kakava_lt)
    save_df(df_kakava_lt, out_dir / "df_kakava_lt.csv")
    report_saved(out_dir / "df_kakava_lt.csv")

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


if __name__ == "__main__":
    asyncio.run(main())
