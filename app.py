import re
import difflib
from functools import lru_cache
from urllib.parse import unquote, quote_plus

import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

# CORS: allow all origins
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

REQUEST_TIMEOUT = 10  # Increased timeout for reliability

# Refined Regular Expressions for cleaning titles
TITLE_PATTERNS = [
    (re.compile(r'^\s*Einthusan\s*[-–—]\s*', re.IGNORECASE), ''),
    (re.compile(r'\s*\(\d{4}\)\s*$'), ''), # Removes (YYYY) from the end
    (re.compile(r'\s*\[(Tamil|Hindi|Telugu|Malayalam|Kannada|Bengali|Marathi|Punjabi)\]', re.IGNORECASE), ''),
    (re.compile(r'\s+Watch\s+Full\s+Movie.*$', re.IGNORECASE), ''),
    (re.compile(r'\s+\|\s+Einthusan.*$', re.IGNORECASE), '')
]

# ----------------- HELPERS -----------------
@lru_cache(maxsize=128)
def correct_spelling(user_input: str):
    """Fuzzy match a language key."""
    options = tuple(LANGUAGE_CODES.keys())
    match = difflib.get_close_matches((user_input or "").lower(), options, n=1, cutoff=0.7)
    return match[0] if match else None

def clean_title(title: str | None) -> str | None:
    """Cleans the movie title using regex patterns."""
    if not title:
        return None
    title = title.strip()
    for pattern, repl in TITLE_PATTERNS:
        title = pattern.sub(repl, title)
    return title.strip()

@lru_cache(maxsize=256)
def fetch_page(url: str) -> str | None:
    """Fetches the content of a URL."""
    try:
        resp = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException:
        return None

def get_title_from_movie_page(page_url: str) -> str | None:
    """Extracts title from the movie's own page for better accuracy."""
    content = fetch_page(page_url)
    if not content:
        return None
    soup = BeautifulSoup(content, 'html.parser')
    
    # Try OpenGraph meta tag first
    meta_title = soup.find('meta', property='og:title')
    if meta_title and meta_title.get('content'):
        cleaned_title = clean_title(meta_title['content'])
        if cleaned_title:
            return cleaned_title
            
    # Fallback to the <title> tag
    if soup.title and soup.title.string:
        cleaned_title = clean_title(soup.title.string)
        if cleaned_title:
            return cleaned_title
            
    return None

def process_movie_block(div) -> dict | None:
    """Processes a single movie block from the search/listing page."""
    a_tag = div.find('a', href=True)
    img_tag = div.find('img', src=True)

    if not (a_tag and img_tag):
        return None

    page_url = f"https://einthusan.tv{a_tag['href']}"
    
    # The most reliable title is often in the 'alt' or 'title' attribute of the image
    raw_title = img_tag.get('title') or img_tag.get('alt')
    title = clean_title(raw_title)

    # If the title from the image tag is poor, fetch the movie page as a fallback
    if not title or len(title) < 2:
        title = get_title_from_movie_page(page_url) or "Title not found"

    img_url = img_tag.get('src')
    # Make image URL absolute if it's relative
    if img_url and not img_url.startswith('http'):
        img_url = f"https://einthusan.tv{img_url}"

    return {
        "title": title,
        "img_url": img_url,
        "page_url": page_url
    }

def fetch_movies_by_url(url: str) -> list[dict]:
    """Fetches and parses movies from a given Einthusan URL."""
    content = fetch_page(url)
    if not content:
        return []
        
    soup = BeautifulSoup(content, 'html.parser')
    # Target the container holding the movie blocks
    movie_list_container = soup.find('div', class_='movie-results-container')
    if not movie_list_container:
        return []

    blocks = movie_list_container.find_all('div', class_='block1')
    movies = []
    for b in blocks:
        item = process_movie_block(b)
        if item:
            movies.append(item)
    return movies

def search_movie(language: str, movie_title: str) -> list[dict]:
    """Searches for a movie by language and title."""
    lang_code = LANGUAGE_CODES.get(language.lower())
    if not lang_code:
        return []
    url = f"https://einthusan.tv/movie/results/?lang={lang_code}&query={quote_plus(movie_title)}"
    return fetch_movies_by_url(url)

def extract_video_url(page_url: str) -> str | None:
    """Extracts the direct video URL from the movie page."""
    content = fetch_page(page_url)
    if not content:
        return None
    
    # The video URL is often stored in a JavaScript variable or a data attribute.
    # We can use regex to find it within the script tags.
    match = re.search(r'data-mp4-link="([^"]+)"', content)
    if match:
        return match.group(1)
        
    # Fallback if the above pattern changes
    match = re.search(r'let videoSrc\s*=\s*"([^"]+)"', content)
    if match:
        return match.group(1)

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
        url = f"https://einthusan.tv/movie/results/?find=Popularity&lang={lang_code}&page={page}"
    else:  # recent (default)
        url = f"https://einthusan.tv/movie/results/?find=Recent&lang={lang_code}&page={page}"

    movies = fetch_movies_by_url(url)
    return jsonify({
        "language": corrected,
        "category": category,
        "page": page,
        "movies": movies,
        "next_page": page + 1 if len(movies) > 0 else page,
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
    
    title = get_title_from_movie_page(movie_url) or "Unknown Title"
    video_url = extract_video_url(movie_url)
    
    if not video_url:
        return jsonify({"error": "Could not find video URL for this movie.", "title": title}), 404
        
    return jsonify({"title": title, "video_url": video_url})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
