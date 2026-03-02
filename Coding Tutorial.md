# 🎟️ Event Website Scraping – Step-by-Step Playbook for non-technical users
This guide explains how to make the event collection script work for a new website in your country.
You do not need to know programming.
You only need to:
* Open a website
* Inspect elements
* Identify the website type
* Copy a selector
* Paste a URL into a template
## 🎯 Goal
Automatically collect upcoming events from ticketing platforms and arena websites and save them in a structured format (CSV/Excel).
For each event we collect:
* event_name
* location
* city
* date
* time
* event_link

### 🧭 STEP 1 — Inspect the Website
1. Open the event website.
2. Right-click on an event title.
3. Click Inspect.
4. Look at the HTML structure in the Elements tab.

**You are trying to understand:**
How are events displayed on this website?

### 🧠 STEP 2 — Identify Website Type
There are 4 types of websites.

#### 🟢 TYPE A — Static Website (Most Common)
**How to recognize:**
Events are visible immediately.
Scrolling does NOT load new content.
Event titles are visible directly inside <div> or <a> tags.
No heavy loading animation.

**Use This Template:**
'import requests
import pandas as pd
from bs4 import BeautifulSoup

def scrape_example():
    url = "PASTE_WEBSITE_URL_HERE"

    response = requests.get(url)
    soup = BeautifulSoup(response.text, "lxml")

    events = []

    for event in soup.select("PASTE_EVENT_CONTAINER_SELECTOR"):
        title_element = event.select_one("PASTE_TITLE_SELECTOR")
        date_element = event.select_one("PASTE_DATE_SELECTOR")
        link_element = event.select_one("a")

        title = title_element.get_text(strip=True) if title_element else ""
        date = date_element.get_text(strip=True) if date_element else ""
        link = link_element["href"] if link_element else ""

        events.append({
            "event_name": title,
            "date": date,
            "event_link": link
        })

    return pd.DataFrame(events)'

##### 🔍 How to Get CSS Selector

* Right-click on event title.
* Click Inspect.
* Right-click highlighted HTML.
* Click Copy → Copy selector.
    
**What to Replace:**

* *PASTE_WEBSITE_URL_HERE*
* *PASTE_EVENT_CONTAINER_SELECTOR*
* *PASTE_TITLE_SELECTOR*
* *PASTE_DATE_SELECTOR*

#### 🟡 TYPE B — Infinite Scroll Website
**How to recognize:**
* Events appear only after scrolling.
* There is no "Next page" button.
* Content loads gradually.

Use This Template:
'import asyncio
import pandas as pd
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

async def scrape_example():
    url = "PASTE_WEBSITE_URL_HERE"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url)

        # Scroll multiple times
        for _ in range(15):
            await page.mouse.wheel(0, 5000)
            await page.wait_for_timeout(1000)

        content = await page.content()
        soup = BeautifulSoup(content, "html.parser")

        events = []

        for link in soup.select("a[href*='/event/']"):
            title = link.get_text(strip=True)
            href = link.get("href")

            events.append({
                "event_name": title,
                "event_link": href
            })

        await browser.close()

    return pd.DataFrame(events)


asyncio.run(scrape_example())'

##### 🔍 How to Get CSS Selector

* Right-click on event title.
* Click Inspect.
* Right-click highlighted HTML.
* Click Copy → Copy selector.

**What to Replace:**
* *Website URL*
* Selector inside soup.select("PASTE_SELECTOR_HERE")


#### 🔵 TYPE C — Pagination Website
**How to recognize:**
* There is a "Next page" button.
* URL changes like: *?page=1*, *?page=2*, *?page=3*

**Use This Template:** 

'import requests
import pandas as pd
from bs4 import BeautifulSoup

def scrape_example():
    base_url = "https://example.com/events?page={}"

    events = []

    for page_number in range(1, 6):
        url = base_url.format(page_number)
        response = requests.get(url)
        soup = BeautifulSoup(response.text, "lxml")

        for event in soup.select("PASTE_EVENT_SELECTOR"):
            title = event.get_text(strip=True)
            events.append({"event_name": title})

    return pd.DataFrame(events)


df = scrape_example()
print(df)'

**What to Replace:**
* *Base URL*
* *Page range*
* *PASTE_EVENT_SELECTOR*

#### 🟣 TYPE D — JSON-LD Website (Best Case)
**How to recognize:**
* Open Inspect → Elements
* Press Ctrl + F
* Search: *application/ld+json*
* If you see:
/<script type="application/ld+json">
{
 "@type": "Event",
 "name": "...",
 "startDate": "..."
}
</script>/

**Use This Template:**
'import requests
import json
import pandas as pd
from bs4 import BeautifulSoup

def scrape_example():
    url = "PASTE_WEBSITE_URL_HERE"

    response = requests.get(url)
    soup = BeautifulSoup(response.text, "lxml")

    events = []

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except:
            continue

        if isinstance(data, dict) and data.get("@type") == "Event":
            events.append({
                "event_name": data.get("name", ""),
                "date": data.get("startDate", ""),
                "location": data.get("location", {}).get("name", "")
            })

    return pd.DataFrame(events)


df = scrape_example()
print(df)'

**What to Replace:**
* Only the website URL
* No selectors needed.

### 🧼 STEP 3 — Standardize Output

All countries should use this structure:

'events.append({
    "event_name": title,
    "location": "ARENA_NAME",
    "city": "CITY_NAME",
    "date": date,
    "time": time,
    "event_link": link
})'

This ensures consistency across countries.

### 📦 Installation (One-Time Setup)

**Run in terminal:**
'pip install pandas requests beautifulsoup4 lxml playwright
playwright install' 

### 🚨 Common Problems & Fixes
Problem	| Likely Cause | Fix|
---|---|---|
No events returned |	Wrong selector |	Re-copy selector|
Only few events|	Pagination not handled|	Use pagination template|
Missing events	|Scroll not enough	|Increase scroll rounds|
Website blocks request|	Anti-bot protection	|Use Playwright|
Dates look wrong	|Local month names	|Add month dictionary|

### 🏗️ Best Practice Per Country

* Create one file per website.
* Use correct template.
* Standardize output fields.
* Remove duplicates.
* Test results manually.
