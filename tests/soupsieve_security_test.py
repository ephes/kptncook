"""Keep notebook Beautiful Soup selectors safe on deeply nested input."""

import subprocess
import sys

import pytest

# On SoupSieve < 4.0 (CVE-2026-126190) the descendant selector below backtracks
# through every ancestor combination of the 200 nested divs and takes well over
# 30 seconds; on 4.1 it finishes in milliseconds, so the 5 second timeout fails
# the test on a vulnerable lock.
CHECK = (
    "from bs4 import BeautifulSoup; "
    "soup = BeautifulSoup('<section><div><div>recipe</div></div></section>', 'html.parser'); "
    "assert soup.select_one('section div div').get_text() == 'recipe'; "
    "nested = BeautifulSoup('<div>' * 200, 'html.parser'); "
    "assert nested.select('section div div div') == []"
)


def test_descendant_selectors_on_nested_html():
    pytest.importorskip("bs4")  # Notebook dependencies require Python 3.11.5+.
    try:
        subprocess.run(
            [sys.executable, "-c", CHECK],
            check=True,
            timeout=5,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        pytest.fail(f"selector check failed:\n{exc.stderr}")
    except subprocess.TimeoutExpired:
        pytest.fail("descendant selector on nested HTML exceeded 5 seconds")
