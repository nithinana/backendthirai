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
# This allows all origins for easier testing.
# In a production environment, change '*' to your frontend's domain.
app = Flask(__name__)
CORS(app) 

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
    (re.compile(r'\s*\(\d{4}\)', re.IGNORECASE), ''),
    (re.compile(r'\(hd.*\)|\(hq.*\)', re.IGNORECASE), ''),
    (re.compile(r'\(eSub\)', re.IGNORECASE), ''),
    (re.compile(r'\s*\(.*\)', re.IGNORECASE), ''),
    (re.compile(r'\s*-\s*einthusan', re.IGNORECASE), '')
]

# --- SCRAPING FUNCTIONS ---

@lru_cache(maxsize=128)
def fetch_page(url, stream=False):
    """Fetches the content of a given URL."""
    try:
        response = requests.get(url, headers=COMMON_HEADERS, timeout=10, stream=stream)
        response.raise_for_status()
        return response.content
    except requests.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return None

def extract_movies_from_html(html_content, page_type):
    """
    Parses HTML content and extracts movie details (image, title, URL).
    Args:
        html_content (bytes): The raw HTML content.
        page_type (str): Either 'language' or 'search'.
    """
    if not html_content:
        return []
    soup = BeautifulSoup(html_content, 'html.parser')
    movies = []
    
    # Select the correct container based on page type
    container_id = 'movie_list' if page_type == 'language' else 'search_results'
    movie_list_div = soup.find('div', id=container_id)
    if not movie_list_div:
        return []

    movie_divs = movie_list_div.find_all('div', class_='movie-card')
    for div in movie_divs:
        link = div.find('a', href=True)
        img = div.find('img', src=True)
        title_div = div.find('div', class_='movie-title')
        
        if link and img and title_div:
            movie_url = link['href']
            img_url = img['src']
            title = title_div.get_text(strip=True)
            
            movies.append({
                'page_url': movie_url,
                'img_url': img_url,
                'title': title
            })
    return movies

def extract_video_url_from_content(html_content):
    """
    Extracts the direct video URL from a movie's page HTML content.
    """
    if not html_content:
        return None
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Try to find the iframe with a source
    iframe = soup.find('iframe', src=True)
    if iframe:
        return iframe['src']
        
    return None

def get_title_from_movie_page(page_url, page_content=None):
    """
    Scrapes the movie title from a movie's page URL.
    """
    if not page_content:
        page_content = fetch_page(page_url)
    if not page_content:
        return None
        
    soup = BeautifulSoup(page_content, 'html.parser')
    
    # The title is in the <title> tag. We need to clean it up.
    title_tag = soup.find('title')
    if title_tag:
        title = title_tag.get_text(strip=True)
        # Apply the regex patterns to clean the title
        for pattern, replacement in TITLE_PATTERNS:
            title = pattern.sub(replacement, title)
        return title.strip()
        
    return None

def fetch_movies_by_url(url, page_type='language'):
    """
    A helper function to fetch a page and extract movies.
    """
    html_content = fetch_page(url)
    if html_content:
        return extract_movies_from_html(html_content, page_type)
    return []
    
def get_language_code(language_name):
    """Corrects language name using difflib.get_close_matches."""
    if not language_name:
        return None

    matches = difflib.get_close_matches(language_name.lower(), LANGUAGE_CODES.keys(), n=1, cutoff=0.8)
    if matches:
        return matches[0]
    return None
    
# --- FLASK ENDPOINTS ---

# The root endpoint now just confirms the API is working
@app.route('/')
def api_home():
    return jsonify({"message": "Thirai API is running."})

# Renamed the main page endpoint to reflect its API-only purpose
@app.route('/api/movies/<language>', methods=['GET'])
def api_movies_by_language(language):
    """
    API endpoint to fetch movies for a specific language and category with pagination.
    """
    language_code = get_language_code(language)
    if not language_code:
        return jsonify({"error": "Invalid language"}), 404

    category = request.args.get('category', 'recent') # Default category is 'recent'
    page = request.args.get('page', 1, type=int)
    
    # --- UPDATED URL STRUCTURE ---
    # The new base URL is different and uses 'browse'
    base_url = f"https://einthusan.tv/movie/browse/"
    
    # The new URL seems to handle languages via a parameter
    url = f"{base_url}?lang={LANGUAGE_CODES[language_code]}"
    
    # The old categories 'recent' and 'popular' don't seem to have a direct mapping
    # to URL parameters on the new site. We will continue to pass them for now,
    # but the scraper will simply fetch the default page. You may need to update
    # this logic if the new site has a different way to sort.
    if category == 'popular':
        # You may need to find the correct parameter for 'popular' here.
        # For now, we will just fetch the default page for both categories.
        pass
    
    # The new site seems to use infinite scroll. Pagination may be different.
    # The scraper should start with page 1. The old pagination logic may not work.
    
    movies = fetch_movies_by_url(url)
    
    has_more = len(movies) > 0 # A simple check to indicate if there are more movies.

    return jsonify({
        'movies': movies,
        'has_more': has_more,
        'next_page': page + 1
    })

@app.route('/api/search', methods=['GET'])
def api_search():
    """
    API endpoint to search for movies.
    Query Params:
        - q (str): The search query.
        - lang (str, optional): The language to search within.
    """
    query = request.args.get('q')
    language = request.args.get('lang')
    
    if not query:
        return jsonify({"error": "Search query ('q' parameter) is missing"}), 400

    corrected_language = get_language_code(language)
    lang_code = LANGUAGE_CODES.get(corrected_language, 'all')
    
    search_url = f"https://einthusan.tv/movie/results/?lang={lang_code}&query={quote_plus(query)}"
    
    results = fetch_movies_by_url(search_url, page_type='search')
    
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
        
    return jsonify({"error": "Could not extract video URL"}), 500
    
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=False)
