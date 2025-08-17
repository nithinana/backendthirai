from flask import Flask, request, jsonify
import requests
from bs4 import BeautifulSoup
import difflib
import re
from urllib.parse import unquote, quote_plus
import concurrent.futures
from functools import lru_cache

app = Flask(__name__)

# --------- CONFIG ---------
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

COMMON_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

TITLE_PATTERNS = [
    (re.compile(r'^Einthusan\s*[-–—]\s*', re.IGNORECASE), ''),
    (re.compile(r'\s*\(\d{4}\)\s*$'), ''),
    (re.compile(r'\s*\[(Tamil|Hindi|Telugu|Malayalam|Kannada|Bengali|Marathi|Punjabi)\]', re.IGNORECASE), ''),
    (re.compile(r'\s*\(\d{4}\)\s*(?:Tamil|Hindi|Telugu|Malayalam|Kannada|Bengali|Marathi|Punjabi)\s*in\s*(?:HD|SD)\s*-\s*Einthusan.*$', re.IGNORECASE), ''),
    (re.compile(r'\|\s*Einthusan.*$', re.IGNORECASE), ''),
    (re.compile(r'Watch Full Movie Online Free$', re.IGNORECASE), ''),
    (re.compile(r'Online Watch Free (?:HD|SD)$', re.IGNORECASE), ''),
    (re.compile(r'Free Movies Online$', re.IGNORECASE), '')
]

SESSION = requests.Session()
SESSION.headers.update(COMMON_HEADERS)

# --------- HELPERS ---------
@lru_cache(maxsize=128)
def correct_spelling(user_input):
    valid_options = tuple(LANGUAGE_CODES.keys())
    close_matches = difflib.get_close_matches(user_input.lower(), valid_options, n=1, cutoff=0.7)
    return close_matches[0] if close_matches else None

def clean_title(title):
    if not title:
        return None
    title = title.strip()
    for pattern, repl in TITLE_PATTERNS:
        title = pattern.sub(repl, title)
    return title.strip()

@lru_cache(maxsize=128)
def fetch_page(url):
    try:
        response = SESSION.get(url, timeout=5)
        response.raise_for_status()
        return response.content
    except requests.RequestException as e:
        print(f"Request failed for {url}: {e}")
        return None

def get_title_from_movie_page(page_url, page_content=None):
    content = page_content if page_content else fetch_page(page_url)
    if not content:
        return None
    soup = BeautifulSoup(content, 'html.parser')
    meta = soup.find('meta', property='og:title')
    if meta and meta.get('content'):
        return clean_title(meta['content'])
    title_tag = soup.find('title')
    if title_tag and title_tag.text.strip():
        return clean_title(title_tag.text.strip())
    h1 = soup.find('h1')
    if h1 and h1.text.strip():
        return clean_title(h1.text.strip())
    return None

def process_movie_block(div):
    link_tag = div.find('a')
    img_tag = div.find('img')
    title_div = div.find('div', class_='title')
    if not (link_tag and img_tag):
        return None
    page_url_full = f"https://einthusan.tv{link_tag['href']}"
    title = None
    title_sources = [
        (title_div, lambda x: x.text.strip()),
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
                temp_title = ' '.join([w.capitalize() for w in decoded_slug.replace('-', ' ').split() if w and not w.isdigit()])
                if temp_title:
                    title = clean_title(temp_title)
            except Exception:
                pass
    if not title or title in ['Untitled', 'Untitled Movie (Title Not Found)'] or title.isdigit() or len(title) <= 4:
        accurate_title = get_title_from_movie_page(page_url_full)
        title = accurate_title if accurate_title else 'Untitled Movie (Title Not Found)'
    return {"title": title, "img_url": img_tag['src'], "page_url": page_url_full}

def fetch_movies_by_url(url):
    content = fetch_page(url)
    if not content:
        return []
    soup = BeautifulSoup(content, 'html.parser')
    movie_blocks = soup.find_all('div', class_='block1')
    with concurrent.futures.ThreadPoolExecutor() as executor:
        movies = list(filter(None, executor.map(process_movie_block, movie_blocks)))
    return movies

def search_movie(language, movie_title):
    lang_code = LANGUAGE_CODES.get(language.lower())
    if not lang_code:
        return []
    url = f"https://einthusan.tv/movie/results/?lang={lang_code}&query={quote_plus(movie_title)}"
    return fetch_movies_by_url(url)

def extract_video_url(page_url):
    content = fetch_page(page_url)
    if not content:
        return None
    soup = BeautifulSoup(content, 'html.parser')
    player = soup.find(id="UIVideoPlayer")
    if player:
        mp4_link = player.get('data-mp4-link')
        if mp4_link:
            video_data = mp4_link.split("etv")[1]
            return f"https://cdn1.einthusan.io/etv{video_data}"
    return None

# --------- ROUTES ---------
@app.route("/language/<language>")
def language_page(language):
    category = request.args.get('category', 'recent')
    page = request.args.get('page', 1, type=int)
    corrected_language = correct_spelling(language)
    if corrected_language is None:
        return jsonify({"error": "Invalid language"}), 400
    lang_code = LANGUAGE_CODES[corrected_language]
    if category == "recent":
        url = f"https://einthusan.tv/movie/results/?find=Recent&lang={lang_code}&page={page}"
    elif category == "popular":
        url = f"https://einthusan.tv/movie/results/?find=Popularity&lang={lang_code}&ptype=view&tp=alltime&page={page}"
    else:
        url = f"https://einthusan.tv/movie/results/?find=Recent&lang={lang_code}&page={page}"
    movies = fetch_movies_by_url(url)
    return jsonify({"movies": movies, "next_page": page + 1, "has_more": len(movies) > 0})

@app.route("/search/<language>", methods=["GET"])
def search(language):
    movie_title = request.args.get("q")
    if not movie_title:
        return jsonify({"error": "No query provided"}), 400
    results = search_movie(language, movie_title)
    return jsonify({"movies": results})

@app.route("/watch")
def watch():
    movie_url = request.args.get("url")
    if not movie_url:
        return jsonify({"error": "Movie URL missing"}), 400
    title = get_title_from_movie_page(movie_url)
    video_url = extract_video_url(movie_url)
    return jsonify({"title": title or "Unknown", "video_url": video_url})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
