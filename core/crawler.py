from __future__ import annotations

from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

from playwright.async_api import async_playwright

from core.scanner import Scanner, _run_async
from core.browser_launch import launch_browser_and_page


SKIP_EXTENSIONS = (
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".zip", ".tar", ".gz", ".rar",
    ".css", ".js", ".ico", ".mp3", ".mp4", ".webm",
    ".woff", ".woff2", ".ttf",
)


def _normalize_url(url: str) -> str:
    """Drop fragment, lowercase scheme/host, strip trailing slash on root paths.

    For ``file://`` URLs on Windows the drive letter is sometimes parsed as the
    netloc (e.g. ``file://E:/path``).  We canonicalize those to the three-slash
    form (``file:///E:/path``) so that Playwright, ``urljoin``, and same-domain
    checks all operate on a consistent representation.
    """
    no_frag, _ = urldefrag(url)
    parsed = urlparse(no_frag)
    scheme = parsed.scheme.lower()

    # Canonicalize file:// URLs: convert file://DRIVE:/path → file:///DRIVE:/path
    if scheme == "file" and parsed.netloc and len(parsed.netloc) == 2 and parsed.netloc[1] == ":":
        # netloc is a Windows drive letter like "E:" — fold it into the path
        no_frag = f"file:///{parsed.netloc}{parsed.path}"
        parsed = urlparse(no_frag)

    netloc = parsed.netloc.lower()
    path = parsed.path
    if path.endswith("/") and len(path) > 1:
        path = path.rstrip("/")
    rebuilt = f"{scheme}://{netloc}{path}" if scheme else no_frag
    if parsed.query:
        rebuilt += f"?{parsed.query}"
    if rebuilt.endswith("/"):
        rebuilt = rebuilt[:-1]
    return rebuilt


def _is_same_domain(url: str, base_url: str) -> bool:
    base_host = urlparse(base_url).hostname
    url_host = urlparse(url).hostname
    if base_host is None or url_host is None:
        # file:// URLs both have None — treat as same domain for local fixtures
        return urlparse(url).scheme == urlparse(base_url).scheme
    return base_host == url_host


def _is_crawlable_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https", "file"):
        return False
    path_lower = parsed.path.lower()
    if any(path_lower.endswith(ext) for ext in SKIP_EXTENSIONS):
        return False
    return True


class Crawler:
    def __init__(self, scanner: Scanner | None = None):
        self.scanner = scanner or Scanner()

    def crawl(self, base_url: str, max_pages: int = 50, max_depth: int = 5) -> list[dict]:
        """Sync entry point — calls _run_async like Scanner does."""
        return _run_async(self.crawl_async(base_url, max_pages, max_depth))

    async def crawl_async(
        self, base_url: str, max_pages: int = 50, max_depth: int = 5
    ) -> list[dict]:
        """BFS crawl; returns [{'url': ..., 'elements': [...]}, ...]."""
        seen = set()
        results = []
        queue: deque[tuple[str, int]] = deque()
        start = _normalize_url(base_url)
        queue.append((start, 0))
        seen.add(start)

        async with async_playwright() as p:
            browser, page = await launch_browser_and_page(p)

            while queue and len(results) < max_pages:
                current, depth = queue.popleft()
                try:
                    await page.goto(current, wait_until="domcontentloaded", timeout=60000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    elements = await self.scanner.scan_current_page(page)
                    results.append({"url": current, "elements": elements})

                    if depth < max_depth:
                        hrefs = await page.evaluate(
                            "() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href)"
                        )
                        for href in hrefs:
                            absolute = urljoin(current, href)
                            normalized = _normalize_url(absolute)
                            if normalized in seen:
                                continue
                            if not _is_crawlable_url(normalized):
                                continue
                            if not _is_same_domain(normalized, start):
                                continue
                            seen.add(normalized)
                            queue.append((normalized, depth + 1))
                except Exception as exc:
                    results.append({"url": current, "elements": [], "error": str(exc)})

            await browser.close()
        return results
