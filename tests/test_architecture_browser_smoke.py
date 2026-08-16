"""Collectible real-Chromium gate for the architecture HTML asset.

The ordinary test suite collects this test and skips it when the optional Python
Playwright package is absent. The release gate must run it explicitly with:
    uv run --with playwright pytest -q tests/test_architecture_browser_smoke.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

HTML_PATH = Path(__file__).parents[1] / "docs" / "assets" / "A2AMesh_V1.6_Architecture.html"
VIEWPORT_WIDTHS = (375, 760, 1440, 1800)


@pytest.mark.browser
def test_architecture_html_in_real_chromium_across_viewports() -> None:
    playwright_api = pytest.importorskip(
        "playwright.sync_api",
        reason=(
            "real Chromium gate requires the optional Playwright package; "
            "run with `uv run --with playwright pytest "
            "tests/test_architecture_browser_smoke.py`"
        ),
    )
    results: list[dict[str, Any]] = []
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for width in VIEWPORT_WIDTHS:
            page = browser.new_page(viewport={"width": width, "height": 1000})
            page_errors: list[str] = []
            page.on(
                "pageerror",
                lambda error, errors=page_errors: errors.append(str(error)),
            )
            page.goto(HTML_PATH.as_uri(), wait_until="load")
            page.wait_for_timeout(180)

            initial_transform = page.locator("svg").evaluate(
                "(node) => node.style.transform"
            )
            initial = page.evaluate(
                """
                () => {
                    const overlaps = (a, b) => !(
                        a.right <= b.left ||
                        a.left >= b.right ||
                        a.bottom <= b.top ||
                        a.top >= b.bottom
                    );
                    const actorIds = [
                        'artifact-adapter',
                        'artifact-delete-worker',
                        'artifact-hold-reaper',
                    ];
                    const overflowingText = actorIds.flatMap((id) => {
                        const group = document.getElementById(id);
                        const card = group.querySelector('rect').getBoundingClientRect();
                        return [...group.querySelectorAll('text')]
                            .filter((text) => {
                                const box = text.getBoundingClientRect();
                                return (
                                    box.left < card.left - 1 ||
                                    box.right > card.right + 1 ||
                                    box.top < card.top - 1 ||
                                    box.bottom > card.bottom + 1
                                );
                            })
                            .map((text) => `${id}:${text.textContent}`);
                    });
                    const callout = document.querySelector(
                        '#artifact-hold-cas-contract rect'
                    );
                    const x = Number(callout.getAttribute('x'));
                    const y = Number(callout.getAttribute('y'));
                    const right = x + Number(callout.getAttribute('width'));
                    const bottom = y + Number(callout.getAttribute('height'));
                    const calloutFlowIntersections = [];
                    for (const path of document.querySelectorAll(
                        'svg path[class*="flow"]'
                    )) {
                        const length = path.getTotalLength();
                        for (let index = 0; index <= 500; index += 1) {
                            const point = path.getPointAtLength(length * index / 500);
                            if (
                                point.x >= x - 2 && point.x <= right + 2 &&
                                point.y >= y - 2 && point.y <= bottom + 2
                            ) {
                                calloutFlowIntersections.push(
                                    path.id || path.getAttribute('d')
                                );
                                break;
                            }
                        }
                    }
                    const viewport = document.getElementById('viewport');
                    const svg = document.querySelector('svg').getBoundingClientRect();
                    const stage = document.getElementById('stage').getBoundingClientRect();
                    const note = document.querySelector('.note');
                    const noteRect = note.getBoundingClientRect();
                    return {
                        bodyOverflow:
                            document.body.scrollWidth > window.innerWidth + 1,
                        calloutFlowIntersections,
                        noteDisplay: getComputedStyle(note).display,
                        noteSvgOverlap: overlaps(svg, noteRect),
                        overflowingText,
                        stageFitsViewport:
                            Math.max(stage.width, svg.width) <=
                            viewport.clientWidth + 1,
                        viewportFits:
                            viewport.scrollWidth <= viewport.clientWidth + 1,
                    };
                }
                """
            )

            page.locator("#plus").click()
            page.wait_for_timeout(180)
            plus_transform = page.locator("svg").evaluate(
                "(node) => node.style.transform"
            )
            zoomed = page.evaluate(
                """
                () => {
                    const viewport = document.getElementById('viewport');
                    const before = viewport.scrollLeft;
                    viewport.scrollLeft = viewport.scrollWidth;
                    return {
                        bodyOverflow:
                            document.body.scrollWidth > window.innerWidth + 1,
                        canScrollHorizontally:
                            viewport.scrollWidth > viewport.clientWidth + 1,
                        before,
                        after: viewport.scrollLeft,
                    };
                }
                """
            )

            page.locator("#reset").click()
            page.wait_for_timeout(180)
            reset_transform = page.locator("svg").evaluate(
                "(node) => node.style.transform"
            )
            results.append(
                {
                    "width": width,
                    "compatMode": page.evaluate("document.compatMode"),
                    "headChildren": page.locator("head").evaluate(
                        "(node) => node.children.length"
                    ),
                    "pageErrors": page_errors,
                    "initialTransform": initial_transform,
                    "plusTransform": plus_transform,
                    "resetTransform": reset_transform,
                    "initial": initial,
                    "zoomed": zoomed,
                }
            )
            page.close()
        browser.close()

    assert all(item["compatMode"] == "CSS1Compat" for item in results)
    assert all(item["headChildren"] >= 3 for item in results)
    assert all(not item["pageErrors"] for item in results)
    assert all(item["initialTransform"] != "none" for item in results)
    assert all(item["plusTransform"] != item["initialTransform"] for item in results)
    assert all(item["resetTransform"] == item["initialTransform"] for item in results)
    assert all(not item["initial"]["bodyOverflow"] for item in results)
    assert all(item["initial"]["stageFitsViewport"] for item in results)
    assert all(item["initial"]["viewportFits"] for item in results)
    assert all(item["initial"]["noteDisplay"] != "none" for item in results)
    assert all(not item["initial"]["noteSvgOverlap"] for item in results)
    assert all(not item["initial"]["overflowingText"] for item in results)
    assert all(
        not item["initial"]["calloutFlowIntersections"] for item in results
    )
    assert all(not item["zoomed"]["bodyOverflow"] for item in results)
    assert all(item["zoomed"]["canScrollHorizontally"] for item in results)
    assert all(item["zoomed"]["after"] > item["zoomed"]["before"] for item in results)
