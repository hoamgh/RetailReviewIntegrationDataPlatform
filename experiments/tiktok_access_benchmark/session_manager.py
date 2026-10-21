
"""TikTok policy on the shared browser lifecycle, without source UI imports."""

from crawl_experiment.browser.session_manager import BrowserSessionManager


class TikTokSessionManager(BrowserSessionManager):
    """Inherit ownership/cleanup, but permit only one browser creation attempt.

    Google can retire and reacquire browsers. TikTok must stop rather than replace
    a failed, closed or gated session. Manual resume uses the original driver and
    existing crawler checks; acquire never clears a gate or authenticates a user.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._creation_attempted = False

    def acquire(self, store_id, attempt):
        if self._closed:
            raise RuntimeError("TikTok owner is closed; no automatic replacement")

        if self.current is not None:
            if not self.is_alive():
                raise RuntimeError("TikTok session lost; no automatic replacement")

            driver = self.current.driver

            if getattr(driver, "_tiktok_crawler_gate", False):
                raise RuntimeError(
                    "TikTok session gated; manual resume must use original driver"
                )

            if len(driver.window_handles) != 1:
                raise RuntimeError(
                    "TikTok requires one page tab; restore context manually"
                )

            return super().acquire(store_id, attempt)

        if self._creation_attempted:
            raise RuntimeError(
                "TikTok browser was already attempted; no automatic replacement"
            )

        self._creation_attempted = True
        return super().acquire(store_id, attempt)


def standard_browser_factory(*, headless=False, profile_dir=None, driver_path=None):
    """Use Google-style SeleniumBase UC with the existing browser lifecycle.

    driver_path is a version probe only: SeleniumBase selects its own executable.
    """
    from pathlib import Path
    import re
    import subprocess

    from crawl_experiment.browser.browser_factory import BrowserFactory

    options = dict(
        uc=True,
        headless=headless,
        log_cdp_events=True,
    )

    if profile_dir is not None:
        profile = Path(profile_dir).resolve()
        profile.mkdir(parents=True, exist_ok=True)
        options["user_data_dir"] = str(profile)

    if driver_path is not None:
        binary = Path(driver_path).resolve()

        if not binary.is_file():
            raise ValueError("Configured ChromeDriver executable does not exist")

        result = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )

        version = re.search(
            r"ChromeDriver (\d+\.\d+\.\d+\.\d+)",
            result.stdout,
        )

        if not version:
            raise ValueError("Could not identify ChromeDriver version")

        options["driver_version"] = version.group(1)

    def builder(**factory_options):
        from seleniumbase import Driver

        if factory_options.get("proxy"):
            raise ValueError(
                "TikTok UC browser does not accept proxy configuration"
            )

        driver = Driver(**options)

        driver._tiktok_browser_backend = "seleniumbase-uc"
        return driver

    return BrowserFactory(
        driver_builder=builder,
        headless=headless,
    )
