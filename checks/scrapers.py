"""Checks for projects that read a website rather than a documented API.

Each one makes the project's own requests, with its own headers, and runs the
same parsing it relies on, so a markup or endpoint change shows up here instead
of as an empty download folder.
"""
import re
import xml.etree.ElementTree as ElementTree

from checks.probe import Probe, StepFailure, field, get_json, http, non_empty_list, require_fields
from checks.result import timed

# RedditMediaDL
REDDIT_USER_AGENT = "linux:reddit-image-downloader:v1.0 (by /u/drew-gnr)"
REDDIT_FEED_URL = "https://www.reddit.com/r/pics/.rss"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
REDDIT_MEDIA_URL = re.compile(r"https?://(?:i|preview)\.redd\.it/[^\"' <>]+")

# MangaDL
ATSU_BASE = "https://atsu.moe"
ATSU_CDN = "https://cdn.atsu.moe"
ATSU_SITEMAP = f"{ATSU_BASE}/sitemap-manga.xml"
ATSU_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Referer": f"{ATSU_BASE}/",
}

# YtsSearch
DVD_HOME_URL = "https://www.dvdsreleasedates.com/"
DVD_SEARCH_URL = "https://www.dvdsreleasedates.com/search/"
DVD_USER_AGENT = "Mozilla/5.0"
DVD_DETAIL_URL = re.compile(r"^https://www\.dvdsreleasedates\.com/movies/\d+/")
DVD_MOVIE_LINK = re.compile(r"/movies/\d+/[^'\"]+")
DVD_RESULT_LINK = re.compile(r"<a style='color:#000;' href='(/movies/\d+/[^']+)'>([^<]+)</a>")
DVD_TITLE_YEAR = re.compile(
    r"<span itemprop='name'>(?P<title>.*?)</span>\s*\(<a class='(?:future|past)' href='/new-movies-(?P<year>\d{4})/'>")


def run_reddit_rss():
    def feed():
        response = http("GET", REDDIT_FEED_URL, headers={"User-Agent": REDDIT_USER_AGENT}, timeout=45)
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError as error:
            raise StepFailure(f"the feed isn't valid XML any more: {error}") from error
        entries = root.findall("atom:entry", ATOM_NS)
        if not entries:
            raise StepFailure("the feed has no Atom <entry> items, so RedditMediaDL finds no posts")
        return f"{len(entries)} posts in the feed", entries

    def entry_fields(entries):
        first = entries[0]
        for name in ("title", "content", "id"):
            if first.find(f"atom:{name}", ATOM_NS) is None:
                raise StepFailure(f"entries have no <{name}>, which RedditMediaDL reads")
        links = [link.get("href") for link in first.findall("atom:link", ATOM_NS) if link.get("href")]
        if not any("/comments/" in href for href in links):
            raise StepFailure("entry links don't point at /comments/ any more, so post IDs can't be read")
        return "title, content and permalink present"

    def media_links(entries):
        found = sum(1 for e in entries if REDDIT_MEDIA_URL.search(e.findtext("atom:content", "", ATOM_NS)))
        if not found:
            raise StepFailure("no i.redd.it/preview.redd.it links in any post's content, so nothing would download")
        return f"{found}/{len(entries)} posts carry a Reddit image link"

    def do_check():
        probe = Probe()
        entries = probe.step("RedditMediaDL feed (r/pics/.rss, its own User-Agent)", feed)
        needs_feed = [("the feed", entries)]
        probe.step("Post fields (title, content, permalink)", lambda: entry_fields(entries), needs=needs_feed)
        probe.step("Media links in post content", lambda: media_links(entries), needs=needs_feed)
        return probe.result()

    return timed(do_check)


def run_atsu():
    def atsu(path, **params):
        return get_json(f"{ATSU_BASE}{path}", params=params, headers=ATSU_HEADERS)

    def sample_manga():
        # Any listed manga will do; the sitemap is the one stable public index of IDs.
        response = http("GET", ATSU_SITEMAP, headers=ATSU_HEADERS)
        match = re.search(r"atsu\.moe/manga/([A-Za-z0-9_-]+)", response.text)
        if not match:
            raise StepFailure("the manga sitemap lists no manga, so there's nothing to test against")
        return f"testing with manga {match.group(1)}", match.group(1)

    def manga_info(manga_id):
        body = atsu("/api/manga/info", mangaId=manga_id)
        chapters = non_empty_list(body, "chapters")
        require_fields(body, "title", "type")
        require_fields(chapters[0], "id", "number", "scanId")
        return f"'{body['title']}' with {len(chapters)} chapters", chapters[0]["id"]

    def manga_page(manga_id):
        body = atsu("/api/manga/page", id=manga_id)
        page = body.get("mangaPage") if "mangaPage" in body else body
        if not page:
            raise StepFailure("'mangaPage' is empty for a manga the sitemap lists")
        require_fields(page, "title", "poster", "otherNames", "chapters")
        return f"series page has {len(page['chapters'])} chapters listed"

    def chapter_pages(manga_id, chapter_id):
        body = atsu("/api/read/chapter", mangaId=manga_id, chapterId=chapter_id)
        pages = non_empty_list(body, "readChapter.pages")
        image = field(pages[0], "image")
        return f"{len(pages)} pages", image

    def page_image(image_path):
        response = http("GET", f"{ATSU_CDN}{image_path}", headers=ATSU_HEADERS, stream=True)
        content_type = response.headers.get("Content-Type", "")
        response.close()
        if not content_type.startswith("image/"):
            raise StepFailure(f"the CDN returned {content_type or 'no content type'}, not an image")
        return f"served {content_type}"

    def do_check():
        probe = Probe()
        manga_id = probe.step("Pick a manga (sitemap-manga.xml)", sample_manga)
        needs_manga = [("a manga id", manga_id)]
        chapter_id = probe.step("MangaDL info (/api/manga/info)", lambda: manga_info(manga_id), needs=needs_manga)
        probe.step("MangaDL series page (/api/manga/page)", lambda: manga_page(manga_id), needs=needs_manga)
        image_path = probe.step("MangaDL chapter pages (/api/read/chapter)",
                                lambda: chapter_pages(manga_id, chapter_id), needs=[("a chapter id", chapter_id)])
        probe.step("MangaDL page download (cdn.atsu.moe)", lambda: page_image(image_path),
                   needs=[("a page image", image_path)])
        return probe.result()

    return timed(do_check)


def run_dvdsreleasedates():
    def search(title):
        return http("POST", DVD_SEARCH_URL, data={"searchStr": title}, headers={"User-Agent": DVD_USER_AGENT})

    def exact_match():
        # A unique title redirects straight to its detail page, which YtsSearch then parses.
        response = search("Inception")
        if not DVD_DETAIL_URL.match(response.url):
            raise StepFailure(f"an exact title no longer redirects to a /movies/ page (landed on {response.url})")
        heading = re.search(r"<h2>(.*?)</h2>", response.text, re.DOTALL)
        if not heading:
            raise StepFailure("the detail page has no <h2>, so the release status can't be read")
        status = re.search(r"class='(future|past)\s*'", heading.group(1))
        if not status:
            raise StepFailure("the <h2> has no future/past class, so 'not out yet' can't be detected")
        return f"redirected to the detail page, release status '{status.group(1)}'"

    def upcoming_title_year():
        # Films from this year on link their year to /new-movies-YYYY/, which is where YtsSearch
        # reads the year for a "not out yet" answer; the homepage always lists some.
        home = http("GET", DVD_HOME_URL, headers={"User-Agent": DVD_USER_AGENT})
        link = DVD_MOVIE_LINK.search(home.text)
        if not link:
            raise StepFailure("the homepage lists no /movies/ links to test with")
        detail = http("GET", DVD_HOME_URL.rstrip("/") + link.group(0), headers={"User-Agent": DVD_USER_AGENT})
        title_year = DVD_TITLE_YEAR.search(detail.text)
        if not title_year:
            raise StepFailure(f"the title/year markup on {link.group(0)} changed, so YtsSearch can't read the year")
        return f"parsed '{title_year.group('title')}' ({title_year.group('year')})"

    def results_list():
        # A vague title shows a results list, which YtsSearch scopes before matching links.
        response = search("Batman")
        if DVD_DETAIL_URL.match(response.url):
            raise StepFailure("a vague search jumped to one film, so the results-list parsing can't be tested")
        start = response.text.find("Search results for")
        if start == -1:
            raise StepFailure("the 'Search results for' marker is gone, so results can't be found")
        end = response.text.find("id='rightcolumn'")
        links = DVD_RESULT_LINK.findall(response.text[start:end if end != -1 else len(response.text)])
        if not links:
            raise StepFailure("result links don't match the expected markup any more")
        return f"{len(links)} result links parsed"

    def do_check():
        probe = Probe()
        probe.step("YtsSearch exact title (redirect + detail page)", exact_match)
        probe.step("YtsSearch release year (upcoming film's detail page)", upcoming_title_year)
        probe.step("YtsSearch results list (search page)", results_list)
        return probe.result()

    return timed(do_check)
