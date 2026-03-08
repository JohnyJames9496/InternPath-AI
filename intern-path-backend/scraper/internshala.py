import random
import gc
import re
from datetime import date
from patchright.async_api import async_playwright
from sqlalchemy.orm import Session
from database import models


BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-gl-drawing-for-tests",
    "--disable-gpu",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
]

DEFAULT_KEYWORDS = [
    "python",
    "java",
    "ai",
    "web",
    "data science",
    "machine learning",
    "backend",
    "frontend"
]


def save_job(db: Session, data: dict, keyword: str):
    existing = db.query(models.Internship)\
        .filter(models.Internship.link == data["link"])\
        .first()

    if existing:
        existing.scraped_date = date.today()
        db.commit()
        return False

    new_job = models.Internship(
        title=data["title"][:200],
        company=data["company"][:100],
        link=data["link"],
        source=data["source"],
        keyword=keyword,
        location=data.get("location", "Remote")[:100],
        duration=data.get("duration", "Flexible")[:50],
        stipend=data.get("stipend", "Hidden")[:100],
        skills=data.get("skills", "N/A")[:200],
        scraped_date=date.today(),
    )

    db.add(new_job)
    db.commit()
    return True


async def create_stealth_page(context):
    page = await context.new_page()
    await page.route(
        "**/*",
        lambda r: r.abort()
        if r.request.resource_type in ["image", "media", "font", "stylesheet"]
        else r.continue_()
    )
    return page


async def scrape_internshala(db: Session, limit: int = 8):

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=BROWSER_ARGS
        )

        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS)
        )

        page = await create_stealth_page(context)

        total_saved = 0

        for keyword in DEFAULT_KEYWORDS:

            try:
                url = f"https://internshala.com/internships/keywords-{keyword.replace(' ', '-')}"
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_selector(".individual_internship", timeout=15000)

                cards = await page.query_selector_all(".individual_internship")

            except Exception as e:
                print(f"Page load failed for {keyword}:", e)
                continue

            count = 0

            for card in cards:
                if count >= limit:
                    break

                try:
                    # Title
                    title_el = await card.query_selector("h3")
                    if not title_el:
                        continue
                    title = await title_el.inner_text()

                    # Link
                    href = await card.get_attribute("data-href")
                    if not href:
                        continue
                    link = f"https://internshala.com{href}"

                    # Company
                    # Company (strict extraction)
                    # Company (precise extraction)
                    company_el = await card.query_selector("p.company-name")
                    company = await company_el.inner_text() if company_el else "Unknown"
                    company = company.strip()

                    # Full card text
                    full_text = await card.inner_text()

                    # Stipend
                    stipend_match = re.search(
                        r"(₹\s?[\d,]+(\s?-\s?[\d,]+)?(?:\s?/\s?\w+)?|Unpaid|Performance Based)",
                        full_text,
                        re.IGNORECASE
                    )
                    stipend = stipend_match.group(0).strip() if stipend_match else "Hidden"

                    # Duration
                    duration_match = re.search(
                        r"(\d+\s?(?:Month|Week)s?)",
                        full_text,
                        re.IGNORECASE
                    )
                    duration = duration_match.group(0).strip() if duration_match else "Flexible"

                    # Location
                    location_el = await card.query_selector(".location_link")
                    location = await location_el.inner_text() if location_el else "Remote"

                    # Skills
                    # Skills (clean extraction)
                    skill_elements = await card.query_selector_all(".job_skills .skill_container")

                    skill_list = []

                    for el in skill_elements:
                         text = await el.inner_text()
                         if text.strip():
                          skill_list.append(text.strip())

                    skills = ", ".join(skill_list) if skill_list else "N/A"

                    job_data = {
                        "title": title.strip(),
                        "company": company.strip(),
                        "link": link,
                        "source": "Internshala",
                        "location": location.strip(),
                        "duration": duration,
                        "stipend": stipend,
                        "skills": skills
                    }

                    if save_job(db, job_data, keyword):
                        total_saved += 1

                    count += 1

                except Exception:
                    continue

        await browser.close()
        gc.collect()

        print(f"Total saved internships: {total_saved}")
        return total_saved