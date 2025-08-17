import re
import difflib
import time
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
    (re.compile(r'\s*\[(Tamil|Hindi|Telugu|Malayalam|Kannada|Bengali|Marathi|Punjabi)\]\s*'), ''),
]

# --- CACHE CONFIGURATION ---
POPULAR_MOVIES_CACHE = {}
CACHE_TTL = 3600  # Cache for 1 hour (3600 seconds)

# --- UTILS (unchanged) ---

def correct_spelling(text):
    """Corrects spelling of a language against a known list."""
    matches = difflib.get_close_matches(text.lower(), LANGUAGE_CODES.keys(), n=1, cutoff=0.7)
    return matches[0] if matches else None

def get_movie_list_items(soup):
    """Extracts movie list items from BeautifulSoup object."""
    return soup.find_all("li", class_="movie-list-item")

def extract_movie_info(li):
    """Extracts movie details from a single list item."""
    link = li.find("a", class_="movie-url")
    image = li.find("img")
    title_span = li.find("span", class_="movie-title")

    if not all([link, image, title_span]):
        return None

    title = title_span.get_text(strip=True)
    for pattern, replacement in TITLE_PATTERNS:
        title = pattern.sub(replacement, title)

    return {
        "title": title.strip(),
        "url": link["href"].strip(),
        "img": image["data-src"].strip(),
    }

@lru_cache(maxsize=128)
def get_title_from_movie_page(movie_url):
    """Fetches the title from a movie's page."""
    try:
        response = SESSION.get(movie_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        title_tag = soup.find("title")
        return title_tag.get_text(strip=True) if title_tag else "Title not found"
    except requests.exceptions.RequestException:
        return "Failed to fetch title"

def search_movie(language, query):
    """Searches for movies on Einthusan."""
    lang_code = LANGUAGE_CODES[language]
    search_query = quote_plus(query)
    url = f"https://einthusan.tv/movie/results/?lang={lang_code}&query={search_query}"
    return fetch_movies_by_url(url)

def fetch_movies_by_url(url):
    """Fetches and parses a list of movies from a given URL."""
    try:
        response = SESSION.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        movie_items = get_movie_list_items(soup)
        movies = [extract_movie_info(item) for item in movie_items]
        return [movie for movie in movies if movie]
    except requests.exceptions.RequestException:
        return []

# ----------------- ROUTES -----------------

@app.get("/language/<language>")
def language_page(language):
    category = request.args.get("category", "recent").lower()
    page = request.args.get("page", 1, type=int)

    corrected = correct_spelling(language)
    if not corrected:
        return jsonify({"error": "Invalid language"}), 400

    lang_code = LANGUAGE_CODES[corrected]

    # --- NEW CACHING LOGIC ---
    # Check cache only for popular movies and the first two pages
    if category == "popular" and page in [1, 2]:
        cache_key = f'popular_{lang_code}_page_{page}'
        cache_entry = POPULAR_MOVIES_CACHE.get(cache_key)

        # Check if the cache entry exists and is not expired
        if cache_entry and (time.time() - cache_entry['timestamp']) < CACHE_TTL:
            # Cache hit: return the cached data immediately
            return jsonify({
                "language": corrected,
                "category": category,
                "page": page,
                "movies": cache_entry['movies'],
                "next_page": page + 1,
                "has_more": len(cache_entry['movies']) > 0
            })
    # --- END CACHING LOGIC ---

    # Existing logic for fetching data
    if category == "popular":
        url = f"https://einthusan.tv/movie/results/?find=Popularity&lang={lang_code}&ptype=view&tp=alltime&page={page}"
    else:  # recent (default)
        url = f"https://einthusan.tv/movie/results/?find=Recent&lang={lang_code}&page={page}"

    movies = fetch_movies_by_url(url)

    # --- NEW CACHING LOGIC ---
    # Store the fetched data in the cache if it's a popular page
    if category == "popular" and page in [1, 2] and movies:
        POPULAR_MOVIES_CACHE[cache_key] = {
            'movies': movies,
            'timestamp': time.time()
        }
    # --- END CACHING LOGIC ---

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

    return jsonify({
        "url": movie_url,
        "title": title
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
