"""
Song Database - Raw Data Fetcher

This module fetches song data from Last.fm and stores it in a SQLite database.
It retrieves:
- Songs (id_artist, title, duration)

Duration is fetched from:
1. Last.fm API (primary)
2. Spotify API (fallback 1)
3. Gemma 4 API (fallback 2) - with validation
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
# SPOTIFY API FUNCTIONS
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
        # Remove extra spaces, clean up separators
        clean_title = song_title.strip()
        # Remove excessive separators like ":" or "/" for cleaner prompt
        clean_title = re.sub(r'\s*[:/]\s*', ' / ', clean_title)

        prompt = f"""You are a music expert assistant. Your task is to find the exact duration of a specific song.

Artist: {artist_name}
Song: {clean_title}

IMPORTANT: 
- Respond ONLY with the duration in seconds as a number.
- For example: 407, 765, 1020
- Do NOT respond with minutes:seconds format.
- Do NOT add any other text or explanation.
- If you don't know the exact duration, respond with '0'.

What is the duration in seconds of "{clean_title}" by {artist_name}?"""

        response = model.generate_content(
            prompt,
            generation_config={
                'temperature': 0.3,  # Lower temperature for more accurate responses
                'top_p': 0.95,
                'top_k': 64,
            }
        )

        duration_str = response.text.strip()

        # Extract number from response
        match = re.search(r'\d+', duration_str)
        if match:
            duration = int(match.group())
            # Gemma sometimes returns 1-6 seconds incorrectly for long songs
            # If duration < 60 seconds, it's likely wrong for most songs
            if duration > 0 and duration < 60:
                print(f"      ⚠️ Gemma returned suspicious duration: {duration}s (< 1 min). Will verify with DeepSeek.")
                return None  # Force DeepSeek verification
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
        # Clean up the song title
        clean_title = song_title.strip()
        clean_title = re.sub(r'\s*[:/]\s*', ' / ', clean_title)

        prompt = f"""You are a music expert. What is the exact duration of the song "{clean_title}" by {artist_name}?

Respond ONLY with the duration in seconds as a number. For example: 407, 765, 1020.
Do not respond with minutes:seconds format.
If you don't know, respond with '0'."""

        headers = {
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            'Content-Type': 'application/json'
        }

        data = {
            'model': 'deepseek-chat',
            'messages': [
                {'role': 'system', 'content': 'You are a music expert. Respond ONLY with a number (duration in seconds). Do not add any other text.'},
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
            if duration > 0 and duration < 6000:  # Sanity check: less than 100 minutes
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

    # Table: Song
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Song (
            id_song     INTEGER PRIMARY KEY AUTOINCREMENT,
            id_artist   INTEGER NOT NULL,
            title       TEXT    NOT NULL,
            duration    INTEGER,  -- Duration in seconds
            duration_source TEXT, -- Source: 'lastfm', 'spotify', 'gemma4', 'deepseek', 'unknown'
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (id_artist, title)
        )
    ''')

    # Indexes
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_song_artist ON Song (id_artist)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_song_title ON Song (title)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_song_duration ON Song (duration)')

    # Table to track the last update timestamp
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Metadata (
            key     TEXT PRIMARY KEY,
            value   TEXT,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()


def get_last_update_time(conn) -> Optional[str]:
    """Get the last update timestamp from Metadata."""
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM Metadata WHERE key = ?', ('last_update',))
    row = cursor.fetchone()
    return row[0] if row else None


def set_last_update_time(conn, timestamp: str):
    """Set the last update timestamp in Metadata."""
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO Metadata (key, value, last_update)
        VALUES (?, ?, CURRENT_TIMESTAMP)
    ''', ('last_update', timestamp))
    conn.commit()


def get_artist_id(conn, artist_name: str) -> Optional[int]:
    """Get artist ID from the artist database."""
    cursor = conn.cursor()
    cursor.execute('SELECT id_artist FROM Artist WHERE name = ?', (artist_name,))
    row = cursor.fetchone()
    return row[0] if row else None


def song_exists(conn, id_artist: int, title: str) -> bool:
    """Check if a song already exists in the database."""
    cursor = conn.cursor()
    cursor.execute('SELECT id_song FROM Song WHERE id_artist = ? AND title = ?', (id_artist, title))
    return cursor.fetchone() is not None


def save_song(conn, id_artist: int, title: str, duration: Optional[int] = None, source: str = 'unknown'):
    """
    Save a song to the database.
    Returns the song ID if inserted, None if it already exists.
    """
    cursor = conn.cursor()

    # Check if song exists - if so, skip (do NOT overwrite)
    if song_exists(conn, id_artist, title):
        return None  # Already exists, skip

    # Insert new song
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
    """Fetch song duration from Last.fm track.getInfo API."""
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
            return int(duration) // 1000  # Convert ms to seconds

        return None

    except Exception as e:
        return None


def fetch_duration_with_fallback(artist_name: str, song_title: str) -> Tuple[Optional[int], str]:
    """
    Fetch duration using multiple sources in order:
    1. Last.fm
    2. Spotify
    3. Gemma 4 (with validation)
    4. DeepSeek (if Gemma fails or returns suspicious value)

    Returns (duration_seconds, source)
    """
    # 1. Try Last.fm
    duration = fetch_duration_from_lastfm(artist_name, song_title)
    if duration:
        return duration, 'lastfm'

    # 2. Try Spotify
    duration = fetch_duration_from_spotify(artist_name, song_title)
    if duration:
        return duration, 'spotify'

    # 3. Try Gemma 4 (may return None if duration < 60s)
    duration = fetch_duration_from_gemma4(artist_name, song_title)
    if duration:
        return duration, 'gemma4'

    # 4. Try DeepSeek (last resort, especially for complex titles)
    duration = fetch_duration_from_deepseek(artist_name, song_title)
    if duration:
        return duration, 'deepseek'

    return None, 'unknown'


def fetch_scrobbles_page(page: int, limit: int = 200, from_timestamp: Optional[int] = None):
    """Fetch one page of scrobbles from Last.fm."""
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
    """
    Process a single scrobble and save the song (if new).
    If the song already exists, skip it entirely (no API calls).
    """
    # Skip if it's the currently playing track (no timestamp)
    if '@attr' in scrobble_data and scrobble_data['@attr'].get('nowplaying') == 'true':
        return

    artist_name = scrobble_data['artist']['#text']
    song_title = scrobble_data['name']

    # Get artist ID from artist database
    artist_conn = sqlite3.connect(ARTIST_DB_PATH)
    id_artist = get_artist_id(artist_conn, artist_name)
    artist_conn.close()

    if not id_artist:
        print(f"    ⚠️ Artist not found: {artist_name}")
        return

    # Skip if song already exists
    if song_exists(conn, id_artist, song_title):
        return

    # Fetch duration with fallbacks (only for new songs)
    print(f"    🎵 New song: {song_title}")
    duration, source = fetch_duration_with_fallback(artist_name, song_title)

    if duration:
        minutes = duration // 60
        seconds = duration % 60
        print(f"      ⏱️ Duration: {minutes}:{seconds:02d} ({duration}s) [source: {source}]")
    else:
        print(f"      ⏱️ Duration: Unknown [source: none]")

    # Save song
    save_song(conn, id_artist, song_title, duration, source)


def fetch_all_scrobbles(conn, limit: int = 200):
    """
    Fetch all scrobbles from Last.fm.
    If there are existing scrobbles, only fetch new ones.
    """
    # Get the last update time from metadata
    last_update = get_last_update_time(conn)

    # Convert to UNIX timestamp if exists
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

        # Process each track
        for track in tracks:
            process_scrobble(conn, track)
            total_processed += 1

        print(f"   ✅ Page {page}/{total_pages} processed. Total: {total_processed}")

        # Check if we've reached the end
        if page >= total_pages:
            break

        page += 1
        time.sleep(0.3)  # Respect API rate limits

    # Update metadata
    now = datetime.now().isoformat()
    set_last_update_time(conn, now)

    return total_processed


def get_stats(conn):
    """Get database statistics."""
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
    """Create and populate the song database."""

    print("=" * 60)
    print("SONG DATABASE - RAW DATA FETCHER")
    print("=" * 60)

    # Check credentials
    if not LASTFM_API_KEY or not LASTFM_USER:
        print("⚠️ LASTFM_API_KEY / LASTFM_USER not found in environment variables.")
        return

    # Check if artist database exists
    if not os.path.exists(ARTIST_DB_PATH):
        print(f"❌ Artist database not found: {ARTIST_DB_PATH}")
        print("   Please run 0_artist_db_raw.py first.")
        return

    # Create data directory
    os.makedirs(os.path.dirname(SONG_DB_PATH), exist_ok=True)

    # Connect to database
    conn = sqlite3.connect(SONG_DB_PATH)
    create_schema(conn)

    # Check if we have existing data
    last_update = get_last_update_time(conn)
    if last_update:
        print(f"📂 Database already exists. Last update: {last_update}")
        print("   Fetching only new songs...")
    else:
        print("📂 New database. Fetching ALL songs...")

    print("-" * 60)

    # Fetch scrobbles
    try:
        total = fetch_all_scrobbles(conn)
    except Exception as e:
        print(f"❌ Error fetching scrobbles: {e}")
        conn.close()
        sys.exit(1)

    # Get stats
    stats = get_stats(conn)

    # Calculate total duration in hours
    total_hours = stats['total_duration_seconds'] // 3600 if stats['total_duration_seconds'] else 0
    remaining_seconds = stats['total_duration_seconds'] % 3600 if stats['total_duration_seconds'] else 0

    print("-" * 60)
    print(f"\n✅ Database updated successfully")
    print(f"📁 Location: {SONG_DB_PATH}")
    print(f"📋 Table: Song")
    print(f"🎵 Total songs: {stats['total_songs']}")
    print(f"⏱️  Songs with duration: {stats['songs_with_duration']}")

    # Show duration sources
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
