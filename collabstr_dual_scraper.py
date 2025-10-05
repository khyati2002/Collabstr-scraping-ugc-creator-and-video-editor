import re
import time
import logging
import os
import pickle
from pathlib import Path
from typing import Set, List, Dict

import pandas as pd
from urllib.parse import urljoin, urlparse, parse_qs

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("collabstr_dual_scraper")

BASE = "https://collabstr.com"
UGC_URL = "https://collabstr.com/influencers?c=ugc"
VIDEO_URL = "https://collabstr.com/influencers?c=video"

EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def validate_email(email: str) -> bool:
    """Validate email format and check for common issues"""
    if not email or not isinstance(email, str):
        return False
    
    email = email.strip().lower()
    
    if not EMAIL_REGEX.match(email):
        return False
    
    invalid_patterns = [
        r'noreply', r'no-reply', r'donotreply',
        r'example\.com', r'test@', r'admin@localhost',
    ]
    
    for pattern in invalid_patterns:
        if re.search(pattern, email):
            return False
    
    if len(email) < 6 or len(email) > 254:
        return False
    
    parts = email.split('@')
    if len(parts) != 2:
        return False
    
    local, domain = parts
    
    if len(local) > 64 or not local:
        return False
    
    if len(domain) > 253 or not domain or '.' not in domain:
        return False
    
    domain_parts = domain.split('.')
    if len(domain_parts[-1]) < 2:
        return False
    
    return True


class CollabstrSession:
    """Manages Collabstr login session with cookie persistence"""
    
    def __init__(self, email: str = None, password: str = None, cookies_file: str = "collabstr_cookies.pkl"):
        self.email = email or os.getenv("COLLABSTR_EMAIL")
        self.password = password or os.getenv("COLLABSTR_PASSWORD")
        self.cookies_file = Path(cookies_file)
        self.driver = None
        self.logged_in = False
        
    def create_driver(self):
        """Create Chrome driver"""
        options = Options()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        options.add_argument("--window-size=1920,1080")
        
        driver = webdriver.Chrome(options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        return driver
    
    def save_cookies(self):
        """Save cookies to file"""
        try:
            cookies = self.driver.get_cookies()
            with open(self.cookies_file, 'wb') as f:
                pickle.dump(cookies, f)
            logger.info(f"✓ Cookies saved")
        except Exception as e:
            logger.error(f"Failed to save cookies: {e}")
    
    def load_cookies(self):
        """Load cookies from file"""
        try:
            if not self.cookies_file.exists():
                return False
            
            with open(self.cookies_file, 'rb') as f:
                cookies = pickle.load(f)
            
            self.driver.get("https://collabstr.com/")
            time.sleep(2)
            
            for cookie in cookies:
                try:
                    self.driver.add_cookie(cookie)
                except:
                    pass
            
            self.driver.refresh()
            time.sleep(3)
            
            if self.is_logged_in():
                logger.info("✓ Loaded cookies successfully")
                return True
            else:
                return False
                
        except Exception as e:
            logger.error(f"Failed to load cookies: {e}")
            return False
    
    def is_logged_in(self):
        """Check if currently logged in to Collabstr"""
        try:
            current_url = self.driver.current_url.lower()
            
            logged_in_indicators = [
                (By.CSS_SELECTOR, "a[href*='/dashboard']"),
                (By.CSS_SELECTOR, "a[href*='/profile']"),
                (By.CSS_SELECTOR, "a[href*='/account']"),
                (By.XPATH, "//a[contains(text(), 'Dashboard')]"),
                (By.XPATH, "//a[contains(text(), 'Logout')]"),
            ]
            
            for by, selector in logged_in_indicators:
                try:
                    element = self.driver.find_element(by, selector)
                    if element:
                        return True
                except NoSuchElementException:
                    continue
            
            if "login" not in current_url:
                try:
                    login_btn = self.driver.find_element(By.XPATH, "//a[contains(text(), 'Login') or contains(text(), 'Sign In')]")
                    if not login_btn.is_displayed():
                        return True
                except NoSuchElementException:
                    return True
            
            return False
            
        except Exception as e:
            return False
    
    def login(self):
        """Login to Collabstr"""
        if not self.email or not self.password:
            raise ValueError("Email and password required. Set COLLABSTR_EMAIL and COLLABSTR_PASSWORD env vars or pass them as arguments.")
        
        logger.info("Starting Collabstr login...")
        
        self.driver = self.create_driver()
        
        if self.load_cookies():
            self.logged_in = True
            return self.driver
        
        self.driver.get("https://collabstr.com/login")
        time.sleep(3)
        
        try:
            wait = WebDriverWait(self.driver, 10)
            
            email_field = None
            email_selectors = [
                (By.ID, "email"),
                (By.NAME, "email"),
                (By.CSS_SELECTOR, "input[type='email']"),
            ]
            
            for by, selector in email_selectors:
                try:
                    email_field = wait.until(EC.presence_of_element_located((by, selector)))
                    break
                except TimeoutException:
                    continue
            
            if not email_field:
                raise Exception("Could not find email input field")
            
            password_field = self.driver.find_element(By.CSS_SELECTOR, "input[type='password']")
            if not password_field:
                raise Exception("Could not find password input field")
            
            email_field.clear()
            email_field.send_keys(self.email)
            time.sleep(1)
            
            password_field.clear()
            password_field.send_keys(self.password)
            time.sleep(2)
            
            login_button = None
            login_selectors = [
                (By.CSS_SELECTOR, "button.submit.btn"),
                (By.CSS_SELECTOR, "button[type='submit']"),
            ]
            
            for by, selector in login_selectors:
                try:
                    login_button = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((by, selector))
                    )
                    break
                except:
                    continue
            
            if not login_button:
                from selenium.webdriver.common.keys import Keys
                password_field.send_keys(Keys.RETURN)
            else:
                login_button.click()
            
            time.sleep(7)
            
            if self.is_logged_in():
                logger.info("✓ Successfully logged in")
                self.logged_in = True
                self.save_cookies()
                return self.driver
            else:
                raise Exception("Login failed")
                    
        except Exception as e:
            logger.error(f"Login error: {e}")
            raise Exception(f"Automated login failed: {e}")
    
    def get_authenticated_driver(self):
        """Get an authenticated driver session"""
        if not self.driver or not self.logged_in:
            return self.login()
        return self.driver
    
    def close(self):
        """Close the driver"""
        if self.driver:
            self.driver.quit()
            self.driver = None
            self.logged_in = False


class CollabstrDualScraper:
    def __init__(self, delay: float = 2.0, max_pages: int = 3,
                 collabstr_email: str = None, collabstr_password: str = None, 
                 max_profiles: int = 60):
        self.delay = delay
        self.max_pages = max_pages
        self.max_profiles = max_profiles
        
        self.ugc_rows = []
        self.video_rows = []
        self.ugc_profile_urls: Set[str] = set()
        
        self.session = CollabstrSession(collabstr_email, collabstr_password)
        self.driver = None
        self.instagram_driver = None

    def get_authenticated_driver(self):
        """Get authenticated Chrome driver"""
        if not self.driver:
            self.driver = self.session.get_authenticated_driver()
        return self.driver

    def get_instagram_driver(self):
        """Get or create Instagram driver and reuse it"""
        if not self.instagram_driver:
            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            self.instagram_driver = webdriver.Chrome(options=options)
        return self.instagram_driver

    def find_cards(self, driver):
        """Find creator profile cards using Selenium"""
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.profile-listing-holder"))
            )
            
            profile_holders = driver.find_elements(By.CSS_SELECTOR, "div.profile-listing-holder")
            
            if profile_holders:
                logger.info(f"Found {len(profile_holders)} profile cards")
                return profile_holders
            
            return []
            
        except TimeoutException:
            return []
        except Exception as e:
            return []

    def validate_heading_for_role(self, heading_text: str, role_type: str) -> bool:
        """Validate that heading matches the expected role type"""
        if not heading_text:
            return False
        
        heading_lower = heading_text.lower()
        
        if role_type == "ugc":
            ugc_terms = ["ugc", "user generated content", "content creator"]
            return any(term in heading_lower for term in ugc_terms)
        
        elif role_type == "video_editor":
            video_terms = ["video", "editor", "video editor", "video editing"]
            return any(term in heading_lower for term in video_terms)
        
        return False

    def extract_from_card(self, el) -> dict:
        """Extract creator data from profile card using Selenium"""
        data = {}
        
        try:
            text = el.text
            
            link = el.find_element(By.CSS_SELECTOR, "a[href^='/']")
            href = link.get_attribute("href")
            
            if href and not any(x in href for x in ["login", "signup", "about", "contact"]):
                data["profile_url"] = href
                username = href.rstrip("/").split("/")[-1]
                data["username"] = f"@{username}" if username else ""
            else:
                data["profile_url"] = ""
                data["username"] = ""
            
            try:
                name_elem = el.find_element(By.CSS_SELECTOR, "div.profile-listing-owner-name")
                raw_name = name_elem.text.strip()
                data["name"] = re.sub(r'\s*\d+\.\d+\s*$', '', raw_name).strip()
            except NoSuchElementException:
                data["name"] = data["username"]
            
            try:
                heading_elem = el.find_element(By.CSS_SELECTOR, "h1.listing-title, .header-title")
                data["heading"] = heading_elem.text.strip()
            except NoSuchElementException:
                data["heading"] = ""
            
            m_email = EMAIL_REGEX.search(text)
            email = m_email.group(0) if m_email else ""
            data["email"] = email if validate_email(email) else ""
            
        except Exception as e:
            data = {"profile_url": "", "username": "", "name": "", "email": "", "heading": ""}
        
        return data

    def extract_instagram_from_profile(self, profile_url: str, expected_role: str = None) -> tuple:
        """Extract Instagram handle from Collabstr profile and validate heading"""
        logger.info(f"  → {profile_url}")
        
        try:
            driver = self.get_authenticated_driver()
            driver.get(profile_url)
            
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            heading_valid = True
            if expected_role:
                try:
                    heading_elem = driver.find_element(By.CSS_SELECTOR, "h1.listing-title, .header-title")
                    heading_text = heading_elem.text.strip()
                    heading_valid = self.validate_heading_for_role(heading_text, expected_role)
                    
                    if not heading_valid:
                        return "", False
                except NoSuchElementException:
                    heading_valid = False
            
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            time.sleep(2)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            
            try:
                instagram_elements = driver.find_elements(By.CSS_SELECTOR, 'a[href*="instagram.com"]')
                
                for elem in instagram_elements:
                    try:
                        href = elem.get_attribute("href")
                        
                        match = re.search(r"instagram\.com/([a-zA-Z0-9._]+?)(?:/|\?|$)", href)
                        if match:
                            handle = match.group(1)
                            
                            if handle.lower() == "collabstr":
                                continue
                            
                            invalid = ["p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct"]
                            if handle.lower() not in invalid:
                                logger.info(f"  ✓ Instagram: @{handle}")
                                return handle, heading_valid
                    except:
                        continue
                        
            except Exception as e:
                pass
            
            return "", heading_valid
            
        except Exception as e:
            return "", False

    def parse_search_page(self, url: str, role_type: str):
        """Parse a Collabstr search results page using Selenium"""
        driver = self.get_authenticated_driver()
        
        try:
            logger.info(f"Loading page: {url}")
            driver.get(url)
            time.sleep(3)
            
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            
            cards = self.find_cards(driver)
            
            card_data_list = []
            for el in cards:
                d = self.extract_from_card(el)
                d["role_type"] = role_type
                
                if d.get("heading") and not self.validate_heading_for_role(d["heading"], role_type):
                    continue
                
                if d.get("profile_url") and d.get("name"):
                    if role_type == "video_editor" and d["profile_url"] in self.ugc_profile_urls:
                        continue
                    card_data_list.append(d)
            
            out = []
            for d in card_data_list:
                ig_handle, heading_valid = self.extract_instagram_from_profile(d["profile_url"], role_type)
                
                if not heading_valid:
                    continue
                
                d["instagram_handle"] = ig_handle
                out.append(d)
                time.sleep(self.delay)
            
            return out
            
        except Exception as e:
            logger.error(f"Error parsing page: {e}")
            return []

    def paginate_urls(self, first_url: str):
        """Generate URLs for multiple pages"""
        urls = [first_url]
        parsed = urlparse(first_url)
        q = parse_qs(parsed.query)
        
        if "page" in q:
            try:
                start = int(q["page"][0])
            except:
                start = 1
            for i in range(start + 1, start + self.max_pages):
                urls.append(self.rebuild_query(parsed, {"page": i}))
        else:
            for i in range(2, self.max_pages + 1):
                sep = "&" if parsed.query else "?"
                urls.append(first_url + f"{sep}pg={i}")
        
        return urls
    
    def rebuild_query(self, parsed, extra: dict) -> str:
        """Rebuild URL query string"""
        q = parse_qs(parsed.query)
        for k, v in extra.items():
            q[k] = [str(v)]
        
        parts = []
        for k, vals in q.items():
            for v in vals:
                parts.append(f"{k}={v}")
        qs = "&".join(parts)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{qs}"

    def scrape_category(self, base_url: str, role_type: str, max_profiles: int):
        """Scrape a specific category"""
        logger.info("=" * 60)
        logger.info(f"SCRAPING {role_type.upper().replace('_', ' ')} CATEGORY")
        logger.info("=" * 60)
        
        urls = self.paginate_urls(base_url)
        rows = []
        
        for i, url in enumerate(urls, 1):
            if len(rows) >= max_profiles:
                logger.info(f"Reached max profiles limit ({max_profiles})")
                break
                
            logger.info(f"[{i}/{len(urls)}] {url}")
            page_rows = self.parse_search_page(url, role_type)
            logger.info(f"Found {len(page_rows)} creators")
            rows.extend(page_rows)
            
            if len(rows) >= max_profiles:
                rows = rows[:max_profiles]
                break
                
            time.sleep(self.delay)
        
        return rows

    def run(self):
        """Main scraping logic"""
        self.get_authenticated_driver()
        
        # # Scrape UGC creators first
        self.ugc_rows = self.scrape_category(UGC_URL, "ugc", self.max_profiles)
        
        # # Store UGC profile URLs for deduplication
        # self.ugc_profile_urls = {row["profile_url"] for row in self.ugc_rows if row.get("profile_url")}
        
        # Scrape Video creators (will skip duplicates)
        self.video_rows = self.scrape_category(VIDEO_URL, "video_editor", self.max_profiles)

    def fetch_instagram_email_selenium(self, instagram_handle: str) -> str:
        """Extract email from Instagram bio using reusable driver"""
        if not instagram_handle:
            return ""
        
        uname = instagram_handle.lstrip("@").strip()
        url = f"https://www.instagram.com/{uname}/"
        
        logger.info(f"  → Instagram: @{uname}")
        
        try:
            driver = self.get_instagram_driver()
            driver.get(url)
            
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            time.sleep(3)
            
            page_source = driver.page_source
            m = EMAIL_REGEX.search(page_source)
            if m:
                email = m.group(0)
                if validate_email(email):
                    logger.info(f"  ✓ Email: {email}")
                    return email
            
            return ""
            
        except Exception as e:
            return ""

    def enrich_with_instagram_emails(self, rows: List[Dict], category: str):
        """Enrich profiles with Instagram emails"""
        logger.info("=" * 60)
        logger.info(f"Starting Instagram email extraction for {category}...")
        logger.info("=" * 60)
        
        filled = 0
        
        for row in rows:
            if not row.get("email") and row.get("instagram_handle"):
                email = self.fetch_instagram_email_selenium(row["instagram_handle"])
                
                if email:
                    row["email"] = email
                    filled += 1
                
                time.sleep(self.delay)
        
        logger.info(f"✓ Filled {filled} emails from Instagram for {category}")

    def save_csv(self):
        """Export data to separate CSV files"""
        if not self.ugc_rows and not self.video_rows:
            logger.warning("No data to save.")
            return
        
        if self.ugc_rows:
            self.enrich_with_instagram_emails(self.ugc_rows, "UGC")
        if self.video_rows:
            self.enrich_with_instagram_emails(self.video_rows, "Video Creators")
        
        if self.ugc_rows:
            df_ugc = pd.DataFrame(self.ugc_rows)
            df_ugc = df_ugc[["name", "email", "profile_url", "role_type"]]
            df_ugc = df_ugc[df_ugc["email"].notna() & (df_ugc["email"] != "")]
            df_ugc.to_csv("ugc_creators.csv", index=False)
            logger.info(f"✓ Saved {len(df_ugc)} UGC creators with emails to ugc_creators.csv")
        
        if self.video_rows:
            df_video = pd.DataFrame(self.video_rows)
            df_video = df_video[["name", "email", "profile_url", "role_type"]]
            df_video = df_video[df_video["email"].notna() & (df_video["email"] != "")]
            df_video.to_csv("video_editors.csv", index=False)
            logger.info(f"✓ Saved {len(df_video)} Video creators with emails to video_editors.csv")
        
        logger.info("=" * 60)
        logger.info("SCRAPING COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Total UGC creators with emails: {len(df_ugc) if self.ugc_rows else 0}")
        logger.info(f"Total Video creators with emails: {len(df_video) if self.video_rows else 0}")
    
    def __del__(self):
        """Cleanup when object is destroyed"""
        if hasattr(self, 'session'):
            self.session.close()
        if hasattr(self, 'instagram_driver') and self.instagram_driver:
            self.instagram_driver.quit()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Collabstr Dual Category Scraper (UGC + Video)")
    ap.add_argument("--pages", type=int, default=3, help="Pages to scrape per category")
    ap.add_argument("--delay", type=float, default=1.0, help="Delay between requests")
    ap.add_argument("--max_profiles", type=int, default=400, help="Max profiles per category")
    ap.add_argument("--email",  help="Collabstr email")
    ap.add_argument("--password", help="Collabstr password")
    args = ap.parse_args()

    email = args.email or os.getenv("COLLABSTR_EMAIL")
    password = args.password or os.getenv("COLLABSTR_PASSWORD")
    
    if not email or not password:
        logger.error("Credentials required!")
        return

    scraper = CollabstrDualScraper(
        delay=args.delay,
        max_pages=args.pages,
        collabstr_email=email,
        collabstr_password=password,
        max_profiles=args.max_profiles
    )
    
    try:
        scraper.run()
        scraper.save_csv()
    finally:
        if hasattr(scraper, 'session'):
            scraper.session.close()
        if hasattr(scraper, 'instagram_driver') and scraper.instagram_driver:
            scraper.instagram_driver.quit()


if __name__ == "__main__":
    main()