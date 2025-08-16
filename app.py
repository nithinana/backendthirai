from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import difflib
import re
from urllib.parse import unquote, quote_plus
import concurrent.futures
from functools import lru_cache

# Initialize Flask App and configure CORS
# Replace "https://yourusername.github.io" with the actual URL of your frontend
app = Flask(__name__)
CORS(app, origins=["https://yourusername.github.io", "http://127.0.0.1:5500"]) # Added local dev origin

# --- DATA AND CONFIGURATION ---

# Language Codes Dictionary - made immutable
LANGUAGE_CODES = {
    "tamil": "tamil",
    "hindi": "hindi",
    "telugu": "telugu",
    "malayalam": "malayalam",
    "kannada": "kannada",
    "bengali": "bengali",
    "marathi": "marathi",
    "punjabi": "punjabi"
}

# Common headers reused across requests
COMMON_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# Precompiled regex patterns for title cleaning
TITLE_PATTERNS = [
    (re.compile(r'^Einthusan\s*[-–—]\s*', re.IGNORECASE), ''),
    (re.compile(r'\s*\(\d{4}\)\s*$'), ''),
    (re.compile(r'\s*\[(Tamil|Hindi|Telugu|Malayalam|Kannada|Bengali|Marathi|Punjabi)\]', re.IGNORECASE), ''),
    (re.compile(r'\s*\(\d{4}\)\s*(?:Tamil|Hindi|Telugu|Malayalam|Kannada|Bengali|Marathi|Punjabi)\s*in\s*(?:HD|SD)\s*-\s*Einthusan(?:\s*-\s*Watch Movies Online)?$', re.IGNORECASE), ''),
    (re.compile(r'\|\s*Einthusan(?:\s*-\s*Watch Movies Online)?$', re.IGNORECASE), ''),
    (re.compile(r'Watch Full Movie Online Free$', re.IGNORECASE), ''),
    (re.compile(r'Online Watch Free (?:HD|SD)$', re.IGNORECASE), ''),
    (re.compile(r'Free Movies Online$', re.IGNORECASE), '')
]

# Session object for connection pooling
SESSION = requests.Session()
SESSION.headers.update(COMMON_HEADERS)

# --- HELPER FUNCTIONS (SCRAPING LOGIC) ---

@lru_cache(maxsize=128)
def correct_spelling(user_input):
    """Cached spelling correction for common inputs"""
    valid_options = tuple(LANGUAGE_CODES.keys())
    close_matches = difflib.get_close_matches(user_input.lower(), valid_options, n=1, cutoff=0.7)
    return close_matches[0] if close_matches else None

def clean_title(title):
    """Optimized title cleaning with precompiled patterns"""
    if not title:
        return None
    title = title.strip()
    for pattern, repl in TITLE_PATTERNS:
        title = pattern.sub(repl, title)
    return title.strip()

@lru_cache(maxsize=128)
def fetch_page(url):
    """Cached and optimized page fetching with session reuse"""
    try:
        response = SESSION.get(url, timeout=5)
        response.raise_for_status()
        return response.content
    except requests.RequestException as e:
        print(f"Request failed for {url}: {e}")
        return None

def get_title_from_movie_page(page_url, page_content=None):
    """Optimized title extraction from movie page, can take pre-fetched content"""
    content = page_content if page_content else fetch_page(page_url)
    if not content:
        return None
    
    soup = BeautifulSoup(content, 'html.parser')
    
    meta_og_title = soup.find('meta', property='og:title')
    if meta_og_title and meta_og_title.get('content'):
        return clean_title(meta_og_title['content'])
    
    html_title_tag = soup.find('title')
    if html_title_tag and html_title_tag.text.strip():
        return clean_title(html_title_tag.text.strip())
    
    h1_tag = soup.find('h1')
    if h1_tag and h1_tag.text.strip():
        return clean_title(h1_tag.text.strip())
    
    return None

def process_movie_block(div):
    """Process individual movie block in parallel"""
    link_tag = div.find('a')
    img_tag = div.find('img')
    title_div_tag = div.find('div', class_='title')

    if not (link_tag and img_tag):
        return None

    page_url_full = f"https://einthusan.tv{link_tag['href']}"
    title = None

    title_sources = [
        (title_div_tag, lambda x: x.text.strip()),
        (img_tag, lambda x: x.get('alt', '').strip()),
        (img_tag, lambda x: x.get('title', '').strip())
    ]

    for source, extractor in title_sources:
        if source:
            extracted = extractor(source)
            if extracted:
                cleaned = clean_title(extracted)
                if cleaned and len(cleaned) > 3 and not cleaned.isdigit():
                    title = cleaned
                    break

    if not title and link_tag.has_attr('href'):
        href = link_tag['href']
        if '/watch/' in href:
            try:
                slug = href.split('/watch/')[1].split('/')[0]
                decoded_slug = unquote(slug)
                temp_title = ' '.join([word.capitalize() for word in decoded_slug.replace('-', ' ').split() if word and not word.isdigit()])
                if temp_title:
                    title = clean_title(temp_title)
            except Exception:
                pass
        elif 'title=' in href:
            try:
                title_part = href.split('title=')[1].split('&')[0]
                decoded_title_part = unquote(title_part)
                title = clean_title(decoded_title_part.replace('+', ' ').title())
            except Exception:
                pass

    if not title or title == 'Untitled' or title == 'Untitled Movie (Title Not Found)' or (title and (title.isdigit() or len(title) <= 4)):
        accurate_title = get_title_from_movie_page(page_url_full)
        title = accurate_title if accurate_title else 'Untitled Movie (Title Not Found)'

    return {
        'title': title,
        'img_url': img_tag['src'],
        'page_url': page_url_full
    }

def fetch_movies_by_url(url):
    """Optimized movie fetching with parallel processing"""
    content = fetch_page(url)
    if not content:
        return []

    soup = BeautifulSoup(content, 'html.parser')
    movie_blocks = soup.find_all('div', class_='block1')

    with concurrent.futures.ThreadPoolExecutor() as executor:
        movies = list(filter(None, executor.map(process_movie_block, movie_blocks)))

    return movies

def extract_video_url_from_content(page_content):
    """Extracts video URL from pre-fetched page content."""
    if not page_content:
        return None
    soup = BeautifulSoup(page_content, 'html.parser')
    video_player = soup.find(id="UIVideoPlayer")
    if video_player:
        mp4_link = video_player.get('data-mp4-link')
        if mp4_link:
            video_data = mp4_link.split("etv")[1]
            return f"https://cdn1.einthusan.io/etv{video_data}"
    return None

# --- API ENDPOINTS ---

@app.route('/')
def home():
    """A simple endpoint to confirm the backend is running."""
    return jsonify({"message": "Backend is running!"})

@app.route('/api/languages')
def get_languages():
    """Returns a list of supported languages."""
    return jsonify(list(LANGUAGE_CODES.keys()))

@app.route('/api/movies/<language>')
def get_movies_by_language(language):
    """
    Fetches movies for a given language.
    Query Params:
        - category (str): 'recent' or 'popular'. Default: 'recent'.
        - page (int): The page number for pagination. Default: 1.
    """
    category = request.args.get('category', 'recent')
    page = request.args.get('page', 1, type=int)
    
    corrected_language = correct_spelling(language)
    if corrected_language is None:
        return jsonify({"error": "Invalid language specified"}), 400

    lang_code = LANGUAGE_CODES[corrected_language]

    if category == "popular":
        url = f"https://einthusan.tv/movie/results/?find=Popularity&lang={lang_code}&ptype=view&tp=alltime&page={page}"
    else: # Default to recent
        url = f"https://einthusan.tv/movie/results/?find=Recent&lang={lang_code}&page={page}"

    movies = fetch_movies_by_url(url)
    
    return jsonify({
        'language': corrected_language,
        'category': category,
        'page': page,
        'movies': movies,
        'next_page': page + 1 if movies else None
    })

@app.route('/api/search', methods=['GET'])
def search_movies():
    """
    Searches for movies by title in a specific language.
    Query Params:
        - lang (str): The language to search in (e.g., 'tamil').
        - q (str): The movie title/query to search for.
    """
    language = request.args.get('lang')
    query = request.args.get('q')

    if not language or not query:
        return jsonify({"error": "Missing 'lang' or 'q' query parameters"}), 400
        
    corrected_language = correct_spelling(language)
    if corrected_language is None:
        return jsonify({"error": "Invalid language specified"}), 400

    lang_code = LANGUAGE_CODES[corrected_language]
    search_url = f"https://einthusan.tv/movie/results/?lang={lang_code}&query={quote_plus(query)}"
    
    results = fetch_movies_by_url(search_url)
    
    return jsonify({
        'language': corrected_language,
        'query': query,
        'results': results
    })

@app.route('/api/watch', methods=['GET'])
def get_video_url():
    """
    Extracts the direct video URL from a movie's page URL.
    Query Params:
        - url (str): The einthusan page URL for the movie.
        - title (str, optional): The movie title.
    """
    movie_page_url = request.args.get('url')
    movie_title = request.args.get('title', 'Unknown Movie')

    if not movie_page_url:
        return jsonify({"error": "Movie page URL ('url' parameter) is missing"}), 400

    page_content = fetch_page(movie_page_url)
    if not page_content:
        return jsonify({"error": "Could not fetch content from the provided URL"}), 500

    video_url = extract_video_url_from_content(page_content)

    # If title was unknown, try to scrape it
    if movie_title == 'Unknown Movie':
        fetched_title = get_title_from_movie_page(movie_page_url, page_content=page_content)
        if fetched_title:
            movie_title = fetched_title

    if video_url:
        return jsonify({
            "movie_title": movie_title,
            "video_url": video_url
        })
    else:
        return jsonify({
            "movie_title": movie_title,
            "error": "Video URL not found or failed to extract."
        }), 404

if __name__ == '__main__':
    app.run(debug=True)
