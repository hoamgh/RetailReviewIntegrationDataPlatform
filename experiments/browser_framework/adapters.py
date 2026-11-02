"""Small browser transports sharing the production DOM selectors and Review schema."""
from __future__ import annotations

import hashlib
import json
import time
from urllib.parse import urlsplit

from crawl_experiment.sources.google_maps import selectors as S
from crawl_experiment.sources.google_maps.extractor import ReviewExtractor
from crawl_experiment.sources.google_maps.health import HealthState, classify_page
from crawl_experiment.sources.google_maps.place_resolution import google_identity


class SourceBlocked(RuntimeError):
    pass


class NavigationFailed(RuntimeError):
    pass


class PlaywrightElement:
    """Only the read-only WebElement subset required by ReviewExtractor."""
    def __init__(self, locator):
        self.locator = locator

    @property
    def text(self):
        return self.locator.inner_text(timeout=5000)

    def get_attribute(self, name):
        return self.locator.get_attribute(name, timeout=5000)

    def find_elements(self, by, selector):
        if by != "css selector":
            raise ValueError("PoC supports CSS selectors only")
        matches = self.locator.locator(selector)
        return [PlaywrightElement(matches.nth(i)) for i in range(matches.count())]


class PlaywrightBrowser:
    def __init__(self, executable, driver_version=None, driver_path=None, profile_path=None):
        self.executable = executable
        self.profile_path = profile_path
        self.runtime = self.browser = self.context = None

    def start(self):
        from playwright.sync_api import sync_playwright
        if not self.profile_path:
            raise ValueError("Verified persistent benchmark profile required")
        from experiments.browser_framework.profiles import assert_available
        assert_available([self.profile_path])
        self.runtime = sync_playwright().start()
        self.context = self.runtime.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_path), executable_path=self.executable,
            headless=False, locale="en-US", viewport={"width":1280,"height":900},
            args=["--window-size=1280,900", "--lang=en-US", "--profile-directory=Default"])
        self.browser = self.context.browser
        self.page = self.context.new_page()
        self.page.set_default_timeout(5000)
        if self.browser:
            return self.browser.version
        session = self.context.new_cdp_session(self.page)
        return session.send("Browser.getVersion")["product"].split("/", 1)[1]

    def go(self, url):
        response = self.page.goto(url, timeout=30000, wait_until="domcontentloaded")
        if response and response.status in (401, 403, 429):
            raise SourceBlocked("BLOCKED")

    @property
    def url(self):
        return self.page.url

    def viewport(self):
        return self.page.evaluate("({width:innerWidth,height:innerHeight})")

    def elements(self, selector):
        loc = self.page.locator(selector)
        return [PlaywrightElement(loc.nth(i)) for i in range(loc.count())]

    def click(self, element):
        element.locator.click(timeout=5000)

    def scroll(self, element):
        element.locator.evaluate("e => e.scrollBy(0, e.clientHeight)")

    def close(self):
        try:
            if self.context:
                self.context.close()
        finally:
            if self.runtime:
                self.runtime.stop()


class SeleniumBaseBrowser:
    def __init__(self, executable, driver_version, driver_path=None, profile_path=None):
        self.executable, self.driver_version = executable, driver_version
        self.driver_path = driver_path
        self.profile_path = profile_path
        self.driver = None

    def start(self):
        from seleniumbase import Driver
        from seleniumbase import config as sb_config
        from pathlib import Path
        if not self.profile_path:
            raise ValueError("Verified persistent benchmark profile required")
        from experiments.browser_framework.profiles import assert_available
        assert_available([self.profile_path])
        if not self.driver_path or not Path(self.driver_path).is_file():
            raise ValueError("Explicit isolated ChromeDriver required")
        # This setting affects this experimental process only; production files/PATH
        # are never changed. SeleniumBase selects this directory for ChromeService.
        sb_config.settings.NEW_DRIVER_DIR = str(Path(self.driver_path).resolve().parent)
        # Deliberately no UC, stealth, challenge solving, profiles, or proxies.
        self.driver = Driver(browser="chrome", uc=False, headless=False,
                             binary_location=self.executable,
                             driver_version=self.driver_version,
                             user_data_dir=str(self.profile_path),
                             chromium_arg="profile-directory=Default",
                             locale_code="en-US", window_size="1280,900",
                             page_load_strategy="eager")
        self.driver.set_page_load_timeout(30)
        self.driver.implicitly_wait(0)
        # Match Playwright's explicit CSS viewport. Native window sizing includes
        # Windows chrome/banners and cannot guarantee equal content dimensions.
        self.driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", dict(
            width=1280, height=900, deviceScaleFactor=1, mobile=False))
        if Path(self.driver.service.path).resolve() != Path(self.driver_path).resolve():
            self.driver.quit()
            raise RuntimeError("SeleniumBase selected an unexpected driver")
        actual_profile = self.driver.capabilities.get("chrome", {}).get("userDataDir")
        if not actual_profile or Path(actual_profile).resolve() != Path(self.profile_path).resolve():
            self.driver.quit()
            raise RuntimeError("SeleniumBase selected an unexpected profile")
        return self.driver.capabilities.get("browserVersion")

    def go(self, url):
        self.driver.get(url)

    @property
    def url(self):
        return self.driver.current_url

    def viewport(self):
        return self.driver.execute_script("return {width:innerWidth,height:innerHeight}")

    def elements(self, selector):
        return self.driver.find_elements("css selector", selector)

    def click(self, element):
        element.click()

    def scroll(self, element):
        self.driver.execute_script("arguments[0].scrollBy(0, arguments[0].clientHeight)", element)

    def close(self):
        if self.driver:
            self.driver.quit()


def guard(browser):
    body = browser.elements("body")
    text = body[0].text if body else ""
    state = classify_page(browser.url, text)
    host = urlsplit(browser.url).hostname or ""
    if state is HealthState.CHALLENGE:
        raise SourceBlocked("CHALLENGE")
    if state is HealthState.RATE_LIMITED:
        raise SourceBlocked("BLOCKED")
    if state is HealthState.LIMITED:
        raise SourceBlocked("LIMITED_VIEW")
    if host in ("consent.google.com", "accounts.google.com"):
        raise SourceBlocked("BLOCKED")
    if any(phrase in text.casefold() for phrase in ("sign in to see", "limited view")):
        raise SourceBlocked("LIMITED_VIEW")
    if any(phrase in text.casefold() for phrase in (
            "sign in to continue", "before you continue to google", "too many requests", "access denied")):
        raise SourceBlocked("BLOCKED")


def content_hash(review):
    """No author/profile/text is persisted; hash fields for reference accuracy."""
    payload = [review.rating, " ".join((review.text or "").split()),
               " ".join((review.displayed_date or "").split())]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()


def collect(browser, case, *, clock=time.monotonic, sleep=time.sleep):
    """Same bounded algorithm for both transports, no shared harness changes."""
    started = clock()
    deadline = started + 75
    try:
        browser.go(case["url"])
    except SourceBlocked:
        raise
    except Exception as exc:
        raise NavigationFailed(type(exc).__name__) from None
    guard(browser)
    review_mode = case["kind"] == "reviews"
    if not review_mode:
        # DOMContentLoaded does not imply that the Maps client has rendered its feed.
        # Poll both transports identically, within the existing workflow deadline.
        ready_deadline = min(deadline, clock() + 20)
        while not browser.elements(S.SEARCH_RESULT_CANDIDATES):
            guard(browser)
            body = browser.elements("body")
            text = body[0].text.casefold() if body else ""
            if any(phrase in text for phrase in ("no results found", "không tìm thấy kết quả")):
                return dict(status="empty_confirmed", reason="source_no_results", items={}, parse_errors=0)
            if clock() >= ready_deadline:
                return dict(status="navigation_failed", access_state="NAVIGATION_FAILED",
                            reason="discovery_results_not_ready", items={}, parse_errors=0)
            sleep(.5)
    if review_mode:
        # Direct verified place URL avoids search-resolution differences.
        expected_id, _ = google_identity(case["url"])
        actual_id, _ = google_identity(browser.url)
        if not expected_id or actual_id != expected_id:
            return dict(status="resolution_error", reason="place_identity_unverified", items={}, parse_errors=0)
        opened = False
        while clock() < min(deadline, started + 20):
            guard(browser)
            if browser.elements(S.REVIEW_CARD):
                opened = True
                break
            tabs = browser.elements(S.REVIEWS_TAB_CSS)
            if tabs:
                browser.click(tabs[0])
                # Continue observing until review cards actually render.
                # Avoid repeated clicks while the first click is still loading.
                while clock() < min(deadline, started + 20):
                    guard(browser)
                    if browser.elements(S.REVIEW_CARD):
                        opened = True
                        break
                    sleep(.5)
                break
            sleep(.5)
        if not opened:
            return dict(status="workflow_error", reason="reviews_entry_missing", items={}, parse_errors=0)
    items, idle, parse_errors, unresolved = {}, 0, 0, set()
    stop_reason = "scroll_limit"
    extractor = ReviewExtractor()
    for _ in range(12):
        if clock() >= deadline:
            return dict(status="timeout", reason="workflow_deadline", items=items,
                        parse_errors=parse_errors, unresolved_links=len(unresolved))
        guard(browser)
        before = len(items)
        elements = browser.elements(S.REVIEW_CARD if review_mode else S.SEARCH_RESULT_CANDIDATES)
        limit = case["max_reviews"] if review_mode else case["max_places"]
        for element in elements:
            if len(items) >= limit:
                break
            if review_mode:
                try:
                    review = extractor.extract(element, case["id"])
                    items[review.source_review_id] = content_hash(review)
                except Exception:
                    parse_errors += 1
            else:
                url = element.get_attribute("href") or ""
                key, _ = google_identity(url)
                if key:
                    items[key] = True
                else:
                    unresolved.add(url)
        if len(items) >= limit:
            stop_reason = "target_reached"
            break
        idle = idle + 1 if len(items) == before else 0
        if idle >= 3:
            stop_reason = "idle_limit"
            break
        panes = browser.elements(S.REVIEW_PANE_PRIMARY if review_mode else "div[role='feed']")
        if not panes and review_mode:
            for selector in S.REVIEW_PANE_FALLBACKS:
                panes = browser.elements(selector)
                if panes:
                    break
        if not panes:
            stop_reason = "scroll_pane_missing"
            break
        browser.scroll(panes[0])
        sleep(1)
    guard(browser)
    known = case.get("current_displayed_review_count")
    reference = case.get("reference_hashes", {})
    compared = set(reference) & set(items)
    return dict(status="observed" if items else "empty_unresolved", items=items,
                stop_reason=stop_reason,
                parse_errors=parse_errors, unresolved_links=len(unresolved),
                completeness=len(items) / known if review_mode and known else None,
                target_attainment=len(items) / case["max_reviews"] if review_mode else None,
                count_changed=bool(review_mode and known and len(items) > known),
                accuracy=(sum(items[k] == reference[k] for k in compared) / len(compared)
                          if compared else None), reference_compared=len(compared))
