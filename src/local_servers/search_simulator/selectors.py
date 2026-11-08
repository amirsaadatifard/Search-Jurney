"""DOM contract between the simulator's pages and the automation engine.

Both the templates and the browser automation import these names, so a markup
change cannot silently break result parsing.
"""

from __future__ import annotations

from typing import Final

SEARCH_FORM_ID: Final = "search-form"
SEARCH_INPUT_ID: Final = "search-input"
SEARCH_BUTTON_ID: Final = "search-button"
QUERY_PARAMETER: Final = "q"

RESULTS_LIST_ID: Final = "results"
RESULT_CLASS: Final = "result"
RESULT_LINK_CLASS: Final = "result-link"
RESULT_TITLE_CLASS: Final = "result-title"
RESULT_URL_CLASS: Final = "result-url"
RESULT_DESCRIPTION_CLASS: Final = "result-description"
RESULT_POSITION_ATTRIBUTE: Final = "data-position"
NO_RESULTS_ID: Final = "no-results"

SEARCH_PATH: Final = "/search"
CONFIG_API_PATH: Final = "/api/config"
SEARCH_API_PATH: Final = "/api/search"
