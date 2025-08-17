import re
import difflib
from functools import lru_cache
from urllib.parse import unquote, quote_plus

import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

# CORS: allow all origins by default (or lock down to your GH Pages origin later)
CORS(app)

# ----------------- CONFIG -----------------
LANGUAGE_CODES = {
    "tamil": "tamil",
    "hindi": "hindi",
    "telugu": "telugu",
    "malayalam": "malayalam",
    "kannada": "kannada",
    "bengali": "bengali",
    "marathi": "marathi",
    "punjabi": "punjabi",
}

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
})

REQUEST_TIMEOUT = 8  # seconds

TITLE_PATTERNS = [
    (re.compile(r'^Einthusan\s*[-–—]\s*', re.IGNORECASE), ''),
    (re.compile(r'\s*\(\d{4}\)\s*$'), ''),
    (re.compile(r'\s*\[(Tamil|Hindi|Telugu|Malayalam|Kannada|Bengali|Marathi|Punjabi)\]', re.IGNORECASE), ''),
    (re.compile(r'\s*\(\d{4}\)\s*(?:Tamil|Hindi|Telugu|Malayalam|Kannada|Bengali|Marathi|Punjabi)\s*in\s*(?:HD|SD)\s*-\s*Einthusan.*$', re.IGNORECASE), ''),
    (re.compile(r'\|\s*Einthusan.*$', re.IGNORECASE), ''),
    (re.compile(r'Watch Full Movie Online Free$', re.IGNORECASE), ''),
    (re.compile(r'Online Watch Free (?:HD|SD)$', re.IGNORECASE), ''),
    (re.compile(r'Free Movies Online$', re.IGNORECASE), ''),
]

# ----------------- HELPERS -----------------
@lru_cache(maxsize=128)
def correct_spelling(user_input: str):
    """Fuzzy match a language key."""
    options = tuple(LANGUAGE_CODES.keys())
    match = difflib.get_close_matches((user_input or "").lower(), options, n=1, cutoff=0.7)
    return match[0] if match else None

def clean_title(title: str | None) -> str | None:
    if not title:
        return None
    title = title.strip()
    for pattern, repl in TITLE_PATTERNS:
        title = pattern.sub(repl, title)
    return title.strip()

def looks_like_code(s: str | None) -> bool:
    """Detect short alphanumeric codes like '53BA', '1S2Q', 'MukD' etc."""
    if not s:
        return False
    s2 = s.strip()
    if not s2:
        return False
    one_token = len(s2.split()) == 1
    simple = re.fullmatch(r'[A-Za-z0-9]+', s2) is not None
    shortish = 2 <= len(s2) <= 8
    has_digit = any(ch.isdigit() for ch in s2)
    alpha = ''.join(ch for ch in s2 if ch.isalpha())
    no_vowel = not re.search(r'[AEIOUaeiou]', alpha) if alpha else False
    return one_token and simple and shortish and (has_digit or no_vowel)

@lru_cache(maxsize=256)
def fetch_page(url: str) -> bytes | None:
    try:
        resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException:
        return None

def try_extract_title_from_dom(soup: BeautifulSoup) -> str | None:
    meta = soup.find('meta', property='og:title')
    if meta and meta.get('content'):
        cleaned = clean_title(meta['content'])
        if cleaned:
            return cleaned
    if soup.title and soup.title.text:
        cleaned = clean_title(soup.title.text)
        if cleaned:
            return cleaned
    h1 = soup.find('h1')
    if h1 and h1.text:
        cleaned = clean_title(h1.text)
        if cleaned:
            return cleaned
    return None

def get_title_from_movie_page(page_url: str) -> str | None:
    content = fetch_page(page_url)
    if not content:
        return None
    soup = BeautifulSoup(content, 'html.parser')
    return try_extract_title_from_dom(soup)

def process_movie_block(div) -> dict | None:
    a = div.find('a')
    img = div.find('img')
    title_div = div.find('div', class_='title')
    if not (a and img):
        return None

    page_url_full = f"https://einthusan.tv{a.get('href','')}"

    # Try multiple sources for title
    candidates = []
    if title_div and title_div.text:
        candidates.append(title_div.text.strip())
    if img and img.get('alt'):
        candidates.append(img.get('alt').strip())
    if img and img.get('title'):
        candidates.append(img.get('title').strip())

    title = None
    for c in candidates:
        cleaned = clean_title(c)
        if cleaned and len(cleaned) > 2 and not cleaned.isdigit():
            title = cleaned
            break

    # 🚨 FIX: The fallback from slug was removed as it produced incorrect titles
    # like "53ba". The logic will now proceed to fetch the page title if the
    # initial scrape from the list page yields no good title.

    # If still missing, too short, or looks like an alphanumeric code → fetch page title
    if not title or len(title) < 3 or looks_like_code(title):
        t = get_title_from_movie_page(page_url_full)
        if t:
            title = t
        else:
            # Fallback if page fetch fails or yields no title
            title = "Untitled Movie"

    img_url = img.get('src') or img.get('data-src') or img.get('data-original') or ''
    if img_url.startswith('//'):
        img_url = 'https:' + img_url

    return {"title": title, "img_url": img_url, "page_url": page_url_full}

def fetch_movies_by_url(url: str) -> list[dict]:
    content = fetch_page(url)
    if not content:
        return []
    soup = BeautifulSoup(content, 'html.parser')
    blocks = soup.find_all('div', class_='block1')
    movies = []
    for b in blocks:
        item = process_movie_block(b)
        if item:
            movies.append(item)
    return movies

def search_movie(language: str, movie_title: str) -> list[dict]:
    lang_code = LANGUAGE_CODES.get(language.lower())
    if not lang_code:
        return []
    url = f"https://einthusan.tv/movie/results/?lang={lang_code}&query={quote_plus(movie_title)}"
    return fetch_movies_by_url(url)

def extract_video_url(page_url: str) -> str | None:
    content = fetch_page(page_url)
    if not content:
        return None
    soup = BeautifulSoup(content, 'html.parser')
    player = soup.find(id="UIVideoPlayer")
    if player:
        mp4_link = player.get('data-mp4-link')
        if mp4_link and "etv" in mp4_link:
            try:
                tail = mp4_link.split("etv", 1)[1]
                return f"https://cdn1.einthusan.io/etv{tail}"
            except Exception:
                pass
    return None

# ----------------- ROUTES -----------------
@app.get("/")
def root():
    return jsonify({"ok": True, "service": "thirai-api", "endpoints": [
        "/language/<language>?category=popular|recent&page=1",
        "/search/<language>?q=QUERY",
        "/watch?url=<encoded_movie_page_url>",
        "/healthz"
    ]})

@app.get("/healthz")
def healthz():
    return "ok", 200

@app.get("/language/<language>")
def language_page(language):
    category = request.args.get("category", "recent").lower()
    page = request.args.get("page", 1, type=int)

    corrected = correct_spelling(language)
    if not corrected:
        return jsonify({"error": "Invalid language"}), 400

    lang_code = LANGUAGE_CODES[corrected]
    if category == "popular":
        url = f"https://einthusan.tv/movie/results/?find=Popularity&lang={lang_code}&ptype=view&tp=alltime&page={page}"
    else:  # recent (default)
        url = f"https://einthusan.tv/movie/results/?find=Recent&lang={lang_code}&page={page}"

    movies = fetch_movies_by_url(url)
    return jsonify({
        "language": corrected,
        "category": category,
        "page": page,
        "movies": movies,
        "next_page": page + 1,
        "has_more": len(movies) > 0
    })

@app.get("/search/<language>")
def search_route(language):
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "Query parameter 'q' is required"}), 400
    corrected = correct_spelling(language)
    if not corrected:
        return jsonify({"error": "Invalid language"}), 400
    results = search_movie(corrected, q)
    return jsonify({"language": corrected, "q": q, "movies": results})

@app.get("/watch")
def watch():
    movie_url = request.args.get("url", "").strip()
    if not movie_url:
        return jsonify({"error": "Movie URL missing"}), 400

    title = get_title_from_movie_page(movie_url)
    if title:
        title = clean_title(title)
    if not title or looks_like_code(title):
        title = "Unknown"

    video_url = extract_video_url(movie_url)
    return jsonify({"title": title, "video_url": video_url})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
