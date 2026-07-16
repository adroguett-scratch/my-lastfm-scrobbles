"""
Song Database - Raw Data Fetcher

This module fetches song data from Last.fm and stores it in a SQLite database.
It retrieves:
- Songs (id_artist, title, duration)

Duration is fetched from:
1. Last.fm API (primary)
2. Spotify API (fallback 1 - optional)
3. Gemma 4 API (fallback 2 - with validation for years)
4. DeepSeek API (fallback 3, last resort)

OPTIMIZATION: Songs are only fetched ONCE. If a song already exists in the database,
it is skipped entirely (no API calls, no overwriting).
"""

import sqlite3
import os
import sys
import requests
import time
import re
from datetime import datetime
from typing import Optional, Tuple
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database paths
ARTIST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', '0_artist_raw.db')
SONG_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', '2_songs_raw.db')

# --- Credentials ---
LASTFM_API_KEY = os.environ.get('LASTFM_API_KEY')
LASTFM_USER = os.environ.get('LASTFM_USER')
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET')
GEMMA4_API_KEY = os.environ.get('GEMMA4_API_KEY')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')

LASTFM_API_URL = 'https://ws.audioscrobbler.com/2.0/'
SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token'
SPOTIFY_API_URL = 'https://api.spotify.com/v1/search'
DEEPSEEK_API_URL = 'https://api.deepseek.com/v1/chat/completions'

# Spotify token cache
spotify_token = None
spotify_token_expires = 0


# ============================================
# SPOTIFY API FUNCTIONS (OPTIONAL)
# ============================================

def get_spotify_token():
    """Get a Spotify access token."""
    global spotify_token, spotify_token_expires

    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        return None

    # Check if token is still valid
    import time as time_module
    if spotify_token and time_module.time() < spotify_token_expires:
        return spotify_token

    try:
        import base64
        auth_string = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}"
        auth_bytes = auth_string.encode('ascii')
        auth_b64 = base64.b64encode(auth_bytes).decode('ascii')

        headers = {
            'Authorization': f'Basic {auth_b64}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        data = {'grant_type': 'client_credentials'}

        resp = requests.post(SPOTIFY_TOKEN_URL, headers=headers, data=data, timeout=10)
        resp.raise_for_status()

        result = resp.json()
        spotify_token = result['access_token']
        spotify_token_expires = time_module.time() + result['expires_in'] - 60

        return spotify_token

    except Exception as e:
        print(f"      ⚠️ Spotify token error: {e}")
        return None


def fetch_duration_from_spotify(artist_name: str, song_title: str) -> Optional[int]:
    """Fetch song duration from Spotify API."""
    token = get_spotify_token()
    if not token:
        return None

    try:
        query = f'artist:{artist_name} track:{song_title}'
        params = {
            'q': query,
            'type': 'track',
            'limit': 1
        }

        headers = {'Authorization': f'Bearer {token}'}

        resp = requests.get(SPOTIFY_API_URL, headers=headers, params=params, timeout=10)
        resp.raise_for_status()

        data = resp.json()
        tracks = data.get('tracks', {}).get('items', [])

        if tracks:
            duration_ms = tracks[0].get('duration_ms')
            if duration_ms:
                return duration_ms // 1000  # Convert ms to seconds

        return None

    except Exception as e:
        print(f"      ⚠️ Spotify error for '{artist_name} - {song_title}': {e}")
        return None


# ============================================
# GEMMA 4 API FUNCTIONS
# ============================================

def is_suspicious_year_duration(duration: int, artist_name: str, song_title: str) -> bool:
    """
    Check if a duration is actually a year (like 1973, 2112, etc.)
    These are common mistakes from LLMs.
    """
    # Common years that appear as song titles or release years
    suspicious_years = {
        1920, 1921, 1922, 1923, 1924, 1925, 1926, 1927, 1928, 1929,
        1930, 1931, 1932, 1933, 1934, 1935, 1936, 1937, 1938, 1939,
        1940, 1941, 1942, 1943, 1944, 1945, 1946, 1947, 1948, 1949,
        1950, 1951, 1952, 1953, 1954, 1955, 1956, 1957, 1958, 1959,
        1960, 1961, 1962, 1963, 1964, 1965, 1966, 1967, 1968, 1969,
        1970, 1971, 1972, 1973, 1974, 1975, 1976, 1977, 1978, 1979,
        1980, 1981, 1982, 1983, 1984, 1985, 1986, 1987, 1988, 1989,
        1990, 1991, 1992, 1993, 1994, 1995, 1996, 1997, 1998, 1999,
        2000, 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008, 2009,
        2010, 2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019,
        2020, 2021, 2022, 2023, 2024, 2025, 2026, 2027, 2028, 2029,
        2030, 2112, 2525, 3000
    }
    
    # Check if duration matches a year
    if duration in suspicious_years:
        return True
    
    # Check if duration is exactly 1973, 2112 (common in Rush's 2112)
    if duration in [1973, 2112, 1974, 1975, 1976, 1977, 1978, 1979]:
        return True
    
    return False


def fetch_duration_from_gemma4(artist_name: str, song_title: str) -> Optional[int]:
    """
    Fetch song duration from Gemma 4 API.
    Uses gemma-4-31b-it for better accuracy on complex song titles.
    """
    if not GEMMA4_API_KEY:
        return None

    try:
        import google.generativeai as genai

        genai.configure(api_key=GEMMA4_API_KEY)

        # Use the more powerful model for better accuracy
        model = genai.GenerativeModel('gemma-4-31b-it')

        # Clean up the song title for better search
        clean_title = song_title.strip()
        clean_title = re.sub(r'\s*[:/]\s*', ' / ', clean_title)

        prompt = f"""You are a music expert assistant. Your task is to find the exact duration of a specific song.

Artist: {artist_name}
Song: {clean_title}

IMPORTANT: 
- Respond ONLY with the duration in seconds as a number.
- For example: 407, 765, 1020
- Do NOT respond with minutes:seconds format.
- Do NOT respond with a year (like 1973 or 2112).
- Do NOT add any other text or explanation.
- If you don't know the exact duration, respond with '0'.

What is the duration in seconds of "{clean_title}" by {artist_name}?"""

        response = model.generate_content(
            prompt,
            generation_config={
                'temperature': 0.3,
                'top_p': 0.95,
                'top_k': 64,
            }
        )

        duration_str = response.text.strip()

        # Extract number from response
        match = re.search(r'\d+', duration_str)
        if match:
            duration = int(match.group())
            
            # Check if it's a suspicious year
            if is_suspicious_year_duration(duration, artist_name, song_title):
                print(f"      ⚠️ Gemma returned a year ({duration}s) instead of duration. Will verify with DeepSeek.")
                return None  # Force DeepSeek
            
            # Gemma sometimes returns 1-6 seconds incorrectly for long songs
            if duration > 0 and duration < 60:
                print(f"      ⚠️ Gemma returned suspicious duration: {duration}s (< 1 min). Will verify with DeepSeek.")
                return None  # Force DeepSeek
            
            if duration > 0 and duration < 6000:  # Sanity check: less than 100 minutes
                return duration

        return None

    except ImportError:
        print(f"      ⚠️ Google GenAI library not installed. Skipping Gemma 4.")
        return None
    except Exception as e:
        print(f"      ⚠️ Gemma 4 error for '{artist_name} - {song_title}': {e}")
        return None


# ============================================
# DEEPSEEK API FUNCTIONS
# ============================================

def fetch_duration_from_deepseek(artist_name: str, song_title: str) -> Optional[int]:
    """
    Fetch song duration from DeepSeek API.
    Used as final verification when Gemma returns suspicious values.
    """
    if not DEEPSEEK_API_KEY:
        return None

    try:
        clean_title = song_title.strip()
        clean_title = re.sub(r'\s*[:/]\s*', ' / ', clean_title)

        prompt = f"""You are a music expert. What is the exact duration of the song "{clean_title}" by {artist_name}?

IMPORTANT: Respond ONLY with the duration in seconds as a number.
Do NOT respond with minutes:seconds format.
Do NOT respond with a year (like 1973).
If you don't know, respond with '0'."""

        headers = {
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            'Content-Type': 'application/json'
        }

        data = {
            'model': 'deepseek-chat',
            'messages': [
                {'role': 'system', 'content': 'You are a music expert. Respond ONLY with a number (duration in seconds). Never respond with a year.'},
                {'role': 'user', 'content': prompt}
            ],
            'temperature': 0.1,
            'max_tokens': 20
        }

        resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=data, timeout=10)
        resp.raise_for_status()

        result = resp.json()
        duration_str = result['choices'][0]['message']['content'].strip()

        # Extract number from response
        match = re.search(r'\d+', duration_str)
        if match:
            duration = int(match.group())
            
            # Check if it's a suspicious year
            if is_suspicious_year_duration(duration, artist_name, song_title):
                print(f"      ⚠️ DeepSeek also returned a year ({duration}s). Marking as unknown.")
                return None
            
            if duration > 0 and duration < 6000:
                return duration

        return None

    except Exception as e:
        print(f"      ⚠️ DeepSeek error for '{artist_name} - {song_title}': {e}")
        return None


# ============================================
# DATABASE FUNCTIONS
# ============================================

def create_schema(conn):
    """Creates the Song table."""
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Song (
            id_song     INTEGER PRIMARY KEY AUTOINCREMENT,
            id_artist   INTEGER NOT NULL,
            title       TEXT    NOT NULL,
            duration    INTEGER,
            duration_source TEXT,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (id_artist, title)
        )
    ''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_song_artist ON Song (id_artist)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_song_title ON Song (title)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_song_duration ON Song (duration)')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Metadata (
            key     TEXT PRIMARY KEY,
            value   TEXT,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()


def get_last_update_time(conn) -> Optional[str]:
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM Metadata WHERE key = ?', ('last_update',))
    row = cursor.fetchone()
    return row[0] if row else None


def set_last_update_time(conn, timestamp: str):
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO Metadata (key, value, last_update)
        VALUES (?, ?, CURRENT_TIMESTAMP)
    ''', ('last_update', timestamp))
    conn.commit()


def get_artist_id(conn, artist_name: str) -> Optional[int]:
    cursor = conn.cursor()
    cursor.execute('SELECT id_artist FROM Artist WHERE name = ?', (artist_name,))
    row = cursor.fetchone()
    return row[0] if row else None


def song_exists(conn, id_artist: int, title: str) -> bool:
    cursor = conn.cursor()
    cursor.execute('SELECT id_song FROM Song WHERE id_artist = ? AND title = ?', (id_artist, title))
    return cursor.fetchone() is not None


def save_song(conn, id_artist: int, title: str, duration: Optional[int] = None, source: str = 'unknown'):
    cursor = conn.cursor()

    if song_exists(conn, id_artist, title):
        return None

    cursor.execute('''
        INSERT INTO Song (id_artist, title, duration, duration_source, last_update)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (id_artist, title, duration, source))
    conn.commit()

    return cursor.lastrowid


# ============================================
# LAST.FM API FUNCTIONS
# ============================================

def fetch_duration_from_lastfm(artist_name: str, song_title: str) -> Optional[int]:
    params = {
        'method': 'track.getInfo',
        'artist': artist_name,
        'track': song_title,
        'api_key': LASTFM_API_KEY,
        'format': 'json'
    }

    try:
        resp = requests.get(LASTFM_API_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if 'error' in data:
            return None

        track = data.get('track', {})
        duration = track.get('duration')

        if duration and duration != '0':
            return int(duration) // 1000

        return None

    except Exception as e:
        return None


def fetch_duration_with_fallback(artist_name: str, song_title: str) -> Tuple[Optional[int], str]:
    """
    Fetch duration using multiple sources in order:
    1. Last.fm
    2. Spotify (optional)
    3. Gemma 4 (with validation for years)
    4. DeepSeek (last resort)
    """
    # 1. Try Last.fm
    duration = fetch_duration_from_lastfm(artist_name, song_title)
    if duration:
        return duration, 'lastfm'

    # 2. Try Spotify (optional)
    duration = fetch_duration_from_spotify(artist_name, song_title)
    if duration:
        return duration, 'spotify'

    # 3. Try Gemma 4 (with validation)
    duration = fetch_duration_from_gemma4(artist_name, song_title)
    if duration:
        return duration, 'gemma4'

    # 4. Try DeepSeek (last resort)
    duration = fetch_duration_from_deepseek(artist_name, song_title)
    if duration:
        return duration, 'deepseek'

    return None, 'unknown'


def fetch_scrobbles_page(page: int, limit: int = 200, from_timestamp: Optional[int] = None):
    params = {
        'method': 'user.getrecenttracks',
        'user': LASTFM_USER,
        'api_key': LASTFM_API_KEY,
        'format': 'json',
        'limit': limit,
        'page': page
    }

    if from_timestamp:
        params['from'] = from_timestamp

    resp = requests.get(LASTFM_API_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if 'error' in data:
        raise RuntimeError(f"Last.fm API error: {data.get('message', data)}")

    return data


def process_scrobble(conn, scrobble_data):
    if '@attr' in scrobble_data and scrobble_data['@attr'].get('nowplaying') == 'true':
        return

    artist_name = scrobble_data['artist']['#text']
    song_title = scrobble_data['name']

    artist_conn = sqlite3.connect(ARTIST_DB_PATH)
    id_artist = get_artist_id(artist_conn, artist_name)
    artist_conn.close()

    if not id_artist:
        print(f"    ⚠️ Artist not found: {artist_name}")
        return

    if song_exists(conn, id_artist, song_title):
        return

    print(f"    🎵 New song: {song_title}")
    duration, source = fetch_duration_with_fallback(artist_name, song_title)

    if duration:
        minutes = duration // 60
        seconds = duration % 60
        print(f"      ⏱️ Duration: {minutes}:{seconds:02d} ({duration}s) [source: {source}]")
    else:
        print(f"      ⏱️ Duration: Unknown [source: none]")

    save_song(conn, id_artist, song_title, duration, source)


def fetch_all_scrobbles(conn, limit: int = 200):
    last_update = get_last_update_time(conn)

    from_timestamp = None
    if last_update:
        try:
            dt = datetime.fromisoformat(last_update)
            from_timestamp = int(dt.timestamp())
            print(f"📌 Last update: {last_update}")
            print("   Fetching only new scrobbles...")
        except ValueError:
            print(f"   ⚠️ Could not parse timestamp: {last_update}. Fetching all scrobbles...")

    page = 1
    total_processed = 0

    while True:
        print(f"📄 Fetching page {page}...")

        try:
            data = fetch_scrobbles_page(page, limit, from_timestamp)
        except Exception as e:
            print(f"❌ Error fetching page {page}: {e}")
            break

        tracks = data.get('recenttracks', {}).get('track', [])

        if not tracks:
            print("   No more tracks found.")
            break

        total_pages = int(data['recenttracks']['@attr']['totalPages'])

        for track in tracks:
            process_scrobble(conn, track)
            total_processed += 1

        print(f"   ✅ Page {page}/{total_pages} processed. Total: {total_processed}")

        if page >= total_pages:
            break

        page += 1
        time.sleep(0.3)

    now = datetime.now().isoformat()
    set_last_update_time(conn, now)

    return total_processed


def get_stats(conn):
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) FROM Song')
    total_songs = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(*) FROM Song WHERE duration IS NOT NULL')
    songs_with_duration = cursor.fetchone()[0]

    cursor.execute('SELECT duration_source, COUNT(*) FROM Song WHERE duration IS NOT NULL GROUP BY duration_source')
    duration_sources = cursor.fetchall()

    cursor.execute('SELECT SUM(duration) FROM Song')
    total_duration = cursor.fetchone()[0]

    return {
        'total_songs': total_songs,
        'songs_with_duration': songs_with_duration,
        'duration_sources': duration_sources,
        'total_duration_seconds': total_duration
    }


# ============================================
# MAIN FUNCTION
# ============================================

def create_database():
    print("=" * 60)
    print("SONG DATABASE - RAW DATA FETCHER")
    print("=" * 60)

    if not LASTFM_API_KEY or not LASTFM_USER:
        print("⚠️ LASTFM_API_KEY / LASTFM_USER not found in environment variables.")
        return

    if not os.path.exists(ARTIST_DB_PATH):
        print(f"❌ Artist database not found: {ARTIST_DB_PATH}")
        print("   Please run 0_artist_db_raw.py first.")
        return

    os.makedirs(os.path.dirname(SONG_DB_PATH), exist_ok=True)

    conn = sqlite3.connect(SONG_DB_PATH)
    create_schema(conn)

    last_update = get_last_update_time(conn)
    if last_update:
        print(f"📂 Database already exists. Last update: {last_update}")
        print("   Fetching only new songs...")
    else:
        print("📂 New database. Fetching ALL songs...")

    print("-" * 60)

    try:
        total = fetch_all_scrobbles(conn)
    except Exception as e:
        print(f"❌ Error fetching scrobbles: {e}")
        conn.close()
        sys.exit(1)

    stats = get_stats(conn)

    total_hours = stats['total_duration_seconds'] // 3600 if stats['total_duration_seconds'] else 0
    remaining_seconds = stats['total_duration_seconds'] % 3600 if stats['total_duration_seconds'] else 0

    print("-" * 60)
    print(f"\n✅ Database updated successfully")
    print(f"📁 Location: {SONG_DB_PATH}")
    print(f"📋 Table: Song")
    print(f"🎵 Total songs: {stats['total_songs']}")
    print(f"⏱️  Songs with duration: {stats['songs_with_duration']}")

    if stats['duration_sources']:
        print("\n📊 Duration sources:")
        for source, count in stats['duration_sources']:
            print(f"   {source}: {count} songs")

    if stats['total_duration_seconds']:
        print(f"\n📊 Total duration: {total_hours}h {remaining_seconds//60}m")
    print("=" * 60)

    conn.close()


if __name__ == "__main__":
    create_database()
