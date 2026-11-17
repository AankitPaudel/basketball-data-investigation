"""Strip session tokens, tracking code and scripts from saved pages before committing them.

Table markup, links and HTML comments are kept (the scoreboard keeps its status in a
comment), so sanitized fixtures parse identically to the originals.
"""
from bs4 import BeautifulSoup

REMOVED_TAGS = ("script", "noscript", "style", "link", "iframe")


def sanitize_html(html):
    html = html.decode("utf-8-sig") if isinstance(html, bytes) else html
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(REMOVED_TAGS):
        tag.decompose()
    for tag in soup.find_all("meta", attrs={"name": ("csrf-token", "csrf-param")}):
        tag.decompose()
    for tag in soup.find_all("input", attrs={"name": "authenticity_token"}):
        tag.decompose()
    return str(soup)
