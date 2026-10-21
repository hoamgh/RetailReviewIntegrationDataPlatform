import pytest

from crawl_experiment.core.errors import PlaceResolutionError
from crawl_experiment.core.models import Store
from crawl_experiment.sources.google_maps import selectors
from crawl_experiment.sources.google_maps.health import (
    HealthState,
    PageEvidence,
    classify_page,
)
from crawl_experiment.sources.google_maps.navigator import GoogleMapsNavigator
from crawl_experiment.sources.google_maps.review_surface import ReviewSurface, ReviewSurfaceState


def test_true_limited_page_requires_strong_evidence():
    evidence = PageEvidence(has_sign_in_prompt=True)

    assert classify_page(
        "https://www.google.com/maps/search/Store/",
        "Sign in to see this place in limited view",
        evidence=evidence,
    ) is HealthState.LIMITED


def test_healthy_place_ui_overrides_limited_prompt_text():
    evidence = PageEvidence(
        has_place_tabs=True,
        has_place_shell=True,
        has_sign_in_prompt=True,
    )

    assert classify_page(
        "https://www.google.com/maps/place/Store/",
        "Sign in to see more. Limited view.",
        evidence=evidence,
    ) is HealthState.HEALTHY


def test_resolving_page_is_not_limited_without_structural_prompt():
    assert classify_page(
        "https://www.google.com/maps/search/Store/",
        "Sign in to see more",
        evidence=PageEvidence(),
    ) is HealthState.DEGRADED


class ResolvingDriver:
    title = "Coles World Square - Google Maps"

    def __init__(self):
        self.resolved = False

    @property
    def current_url(self):
        page = "place" if self.resolved else "search"
        return f"https://www.google.com/maps/{page}/Coles+World+Square/"

    def get(self, _):
        pass

    def find_element(self, *_):
        return Element("Sign in to see more")

    def find_elements(self, _, selector):
        if self.resolved:
            if selector == selectors.PLACE_TITLE:
                return [Element("Coles World Square")]
            if selector in (selectors.PLACE_TABS, selectors.PLACE_SHELL,
                            selectors.PLACE_DETAIL_PANE, selectors.PLACE_ENTITY_MARKER,
                            selectors.REVIEWS_TAB_CSS):
                return [Element("Reviews")]
        return []


def test_navigator_waits_for_resolving_page_instead_of_marking_limited():
    driver = ResolvingDriver()
    clock = AdvancingClock()

    def resolve_after_poll(seconds):
        clock.sleep(seconds)
        driver.resolved = True

    navigator = GoogleMapsNavigator(
        driver, resolve_timeout_seconds=1,
        clock=clock, sleeper=resolve_after_poll,
    )

    resolved_url = navigator.open_store(
        Store("coles-710", "Coles World Square", "Coles World Square")
    )

    assert "/maps/place/" in resolved_url
    assert clock.now == 0.25
    assert navigator.last_resolution_diagnostics["place_entity_confirmed"] is True
    assert navigator.last_resolution_diagnostics["resolved_place_title"] == "Coles World Square"


class Element:
    def __init__(self, text=""):
        self.text = text


class LimitedDriver:
    current_url = "https://www.google.com/maps/search/Store/"
    title = "Store - Google Maps"

    def get(self, _):
        pass

    def find_element(self, *_):
        return Element("Sign in to see this place in limited view")

    def find_elements(self, _, selector):
        if selector == selectors.SIGN_IN_PROMPT:
            return [Element()]
        return []


class AdvancingClock:
    def __init__(self):
        self.now = 0.0
        self.polls = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.polls += 1
        assert self.polls <= 10, "Resolver exceeded deterministic polling budget"
        self.now += max(seconds, 0.001)


@pytest.mark.parametrize("missing", ["title", "shell", "detail", "marker", "matching_title"])
def test_place_url_and_tabs_do_not_replace_entity_evidence(missing):
    class IncompleteDriver(ResolvingDriver):
        def find_elements(self, method, selector):
            absent = {"title": selectors.PLACE_TITLE, "shell": selectors.PLACE_SHELL,
                      "detail": selectors.PLACE_DETAIL_PANE, "marker": selectors.PLACE_ENTITY_MARKER}
            if selector == absent.get(missing):
                return []
            if missing == "matching_title" and selector == selectors.PLACE_TITLE:
                return [Element("Different business")]
            return super().find_elements(method, selector)

    driver = IncompleteDriver()
    driver.resolved = True
    clock = AdvancingClock()
    navigator = GoogleMapsNavigator(driver, resolve_timeout_seconds=0.5, clock=clock, sleeper=clock.sleep)
    with pytest.raises(PlaceResolutionError, match="exact place entity did not resolve"):
        navigator.open_store(Store("s", "Coles World Square", "Coles World Square"))
    assert clock.now == 0.5
    assert clock.polls == 2
    assert navigator.last_resolution_diagnostics["place_entity_confirmed"] is False


def test_unresolved_limited_search_is_place_resolution_failure():
    navigator = GoogleMapsNavigator(LimitedDriver(), resolve_timeout_seconds=0)
    with pytest.raises(PlaceResolutionError) as captured:
        navigator.open_store(Store("s", "Store", "Store"))
    assert "maps/search/Store" in str(captured.value)
    assert navigator.last_resolution_diagnostics["place_entity_confirmed"] is False


def test_resolved_place_with_limited_reviews_is_not_full_access():
    class LimitedPlaceDriver(ResolvingDriver):
        def find_elements(self, method, selector):
            if selector in (selectors.PLACE_DETAIL_PANE, "[role='main']"):
                return [Element("Sign in to see all reviews. Limited review view.")]
            if selector == selectors.SIGN_IN_PROMPT:
                return [Element("Sign in")]
            return super().find_elements(method, selector)

    driver = LimitedPlaceDriver()
    driver.resolved = True
    navigator = GoogleMapsNavigator(driver, resolve_timeout_seconds=0)
    url = navigator.open_store(Store("s", "Coles World Square", "Coles World Square"))
    assert "/maps/place/" in url
    assert navigator.last_resolution_diagnostics["place_entity_confirmed"] is True
    state, pane, evidence = ReviewSurface(driver).prepare_and_classify()
    assert state is ReviewSurfaceState.LIMITED
    assert pane is None
    assert evidence["review_tabs"] == 1
    assert "limited review view" in evidence["limited_evidence"]
    assert evidence["sort_control"] is False
