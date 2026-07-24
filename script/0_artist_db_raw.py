"""
Artist Database - Raw Data Fetcher

This module fetches artist data from Last.fm and stores it in a SQLite database.
It retrieves:
- Artist name
- Nationality (via DeepSeek API, in English)
- Up to 15 raw genre tags from Last.fm
- Artist image URL (from Last.fm, with fallbacks to Deezer and DeepSeek)

BEHAVIOR:
- If artist is new: fetches ALL data
- If artist exists but missing some data: only fetches missing data
- If artist has all data: skips entirely
- id_artist NEVER changes (preserved across updates)
"""

import sqlite3
import os
import sys
import requests
import time
import re
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database path
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', '0_artist_raw.db')

# --- Credentials ---
LASTFM_API_KEY = os.environ.get('LASTFM_API_KEY')
LASTFM_USER = os.environ.get('LASTFM_USER')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')

LASTFM_API_URL = 'https://ws.audioscrobbler.com/2.0/'
DEEPSEEK_API_URL = 'https://api.deepseek.com/v1/chat/completions'
DEEZER_API_URL = 'https://api.deezer.com/search/artist'

# Nationality cache
nationality_cache = {}


# ============================================
# DATABASE FUNCTIONS
# ============================================

def create_schema(conn):
    """Creates the Artist table with genre, nationality and image columns."""
    cursor = conn.cursor()

    # Check existing columns
    cursor.execute("PRAGMA table_info(Artist)")
    existing_columns = [row[1] for row in cursor.fetchall()]

    # If table doesn't exist, create it from scratch
    if 'Artist' not in [t[0] for t in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]:
        cursor.execute('''
            CREATE TABLE Artist (
                id_artist   INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL UNIQUE,
                nationality TEXT,
                artist_image_url TEXT,
                artist_image_source TEXT,
                genre_1     TEXT,
                genre_2     TEXT,
                genre_3     TEXT,
                genre_4     TEXT,
                genre_5     TEXT,
                genre_6     TEXT,
                genre_7     TEXT,
                genre_8     TEXT,
                genre_9     TEXT,
                genre_10    TEXT,
                genre_11    TEXT,
                genre_12    TEXT,
                genre_13    TEXT,
                genre_14    TEXT,
                genre_15    TEXT,
                last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_artist_name ON Artist (name)')
        conn.commit()
        return

    # --- MIGRATION: Add missing columns without dropping data ---
    
    if 'nationality' not in existing_columns:
        cursor.execute('ALTER TABLE Artist ADD COLUMN nationality TEXT')
        print("   ✅ Added column: nationality")

    if 'artist_image_url' not in existing_columns:
        cursor.execute('ALTER TABLE Artist ADD COLUMN artist_image_url TEXT')
        print("   ✅ Added column: artist_image_url")

    if 'artist_image_source' not in existing_columns:
        cursor.execute('ALTER TABLE Artist ADD COLUMN artist_image_source TEXT')
        print("   ✅ Added column: artist_image_source")

    if 'genre' in existing_columns and 'genre_1' not in existing_columns:
        cursor.execute('ALTER TABLE Artist RENAME COLUMN genre TO genre_1')
        print("   ✅ Renamed column: genre → genre_1")
        cursor.execute("PRAGMA table_info(Artist)")
        existing_columns = [row[1] for row in cursor.fetchall()]

    for n in range(1, 16):
        col = f'genre_{n}'
        if col not in existing_columns:
            cursor.execute(f'ALTER TABLE Artist ADD COLUMN {col} TEXT')
            print(f"   ✅ Added column: {col}")

    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_artist_name'")
    if not cursor.fetchone():
        cursor.execute('CREATE INDEX idx_artist_name ON Artist (name)')
        print("   ✅ Added index: idx_artist_name")

    conn.commit()


def get_artist_status(conn, name):
    """Check what data an artist has. Returns a dict with boolean flags for what's missing."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 
            nationality,
            artist_image_url,
            genre_1, genre_2, genre_3, genre_4, genre_5,
            genre_6, genre_7, genre_8, genre_9, genre_10,
            genre_11, genre_12, genre_13, genre_14, genre_15
        FROM Artist
        WHERE name = ?
    ''', (name,))
    
    row = cursor.fetchone()
    if not row:
        return {'exists': False}
    
    missing_nationality = row[0] is None or row[0] == '' or row[0] == 'Unknown'
    missing_image = row[1] is None or row[1] == ''
    
    genres = list(row[2:])
    has_any_genre = any(g is not None and g != '' for g in genres)
    
    return {
        'exists': True,
        'missing_nationality': missing_nationality,
        'missing_image': missing_image,
        'missing_genres': not has_any_genre,
        'needs_update': missing_nationality or missing_image or not has_any_genre
    }


# ============================================
# IMAGE FETCHING FUNCTIONS
# ============================================

def is_generic_image(url):
    """
    Check if an image URL is a generic/placeholder image.
    Returns True if the image is generic (should be discarded).
    """
    if not url:
        return True
    
    url_lower = url.lower()
    
    # Last.fm generic placeholder patterns
    generic_patterns = [
        'lastfm.freetls.fastly.net/i/u/',
        '2a96fbd4b0e3e8c4',
        'avatar170s',
        'default_artist',
        'placeholder',
        'noimage',
        'generic',
        'unknown'
    ]
    
    for pattern in generic_patterns:
        if pattern in url_lower:
            return True
    
    # Check if URL is too short or looks like a placeholder
    if len(url) < 20:
        return True
    
    # Check if URL contains only generic numbers
    if re.match(r'^https?://[^/]+/\d+x\d+/[a-f0-9]+\.(jpg|png|gif)$', url_lower):
        # This might be a generic avatar
        return True
    
    return False


def get_artist_image_from_lastfm(artist_name):
    """Fetches the artist image URL from Last.fm."""
    params = {
        'method': 'artist.getinfo',
        'artist': artist_name,
        'api_key': LASTFM_API_KEY,
        'format': 'json'
    }

    try:
        resp = requests.get(LASTFM_API_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if 'error' in data:
            return None

        artist = data.get('artist', {})
        images = artist.get('image', [])

        # Try to get the largest image first
        for size in ['extralarge', 'mega', 'large']:
            for img in images:
                if img.get('size') == size:
                    url = img.get('#text')
                    if url and not is_generic_image(url):
                        return url
        
        # If no good image found, return None
        return None

    except Exception as e:
        print(f"      ⚠️ Last.fm image error: {e}")
        return None


def get_artist_image_from_deezer(artist_name):
    """Fetches the artist image URL from Deezer API (no auth required)."""
    try:
        params = {'q': artist_name, 'limit': 1}
        resp = requests.get(DEEZER_API_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        artists = data.get('data', [])
        if artists:
            url = artists[0].get('picture_big')
            if url and not is_generic_image(url):
                return url

        return None

    except Exception as e:
        print(f"      ⚠️ Deezer image error: {e}")
        return None


def get_artist_image_from_deepseek(artist_name):
    """Fetches artist image URL using DeepSeek API (last resort)."""
    if not DEEPSEEK_API_KEY:
        return None

    try:
        prompt = f"""You are a music assistant. Find the official artist image URL for the musician '{artist_name}'.

IMPORTANT:
- Respond ONLY with the image URL.
- Do not add any other text or explanation.
- If you can't find a valid image URL, respond with 'NONE'."""

        headers = {
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            'Content-Type': 'application/json'
        }

        data = {
            'model': 'deepseek-chat',
            'messages': [
                {'role': 'system', 'content': 'You are a music assistant. Respond ONLY with a valid image URL or "NONE".'},
                {'role': 'user', 'content': prompt}
            ],
            'temperature': 0.1,
            'max_tokens': 100
        }

        resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=data, timeout=15)
        resp.raise_for_status()

        result = resp.json()
        image_url = result['choices'][0]['message']['content'].strip()

        if image_url and image_url != 'NONE' and image_url.startswith('http'):
            if not is_generic_image(image_url):
                return image_url

        return None

    except Exception as e:
        print(f"      ⚠️ DeepSeek image error: {e}")
        return None


def get_artist_image(artist_name):
    """
    Fetch artist image using multiple sources in order:
    1. Last.fm
    2. Deezer (no auth required)
    3. DeepSeek (last resort)
    """
    # 1. Try Last.fm
    image = get_artist_image_from_lastfm(artist_name)
    if image and not is_generic_image(image):
        return image, 'lastfm'

    # 2. Try Deezer
    image = get_artist_image_from_deezer(artist_name)
    if image and not is_generic_image(image):
        return image, 'deezer'

    # 3. Try DeepSeek (last resort)
    image = get_artist_image_from_deepseek(artist_name)
    if image and not is_generic_image(image):
        return image, 'deepseek'

    return None, None


# ============================================
# DATA FETCHING FUNCTIONS
# ============================================

def get_top_artists(limit=50):
    """Fetches the top artists from Last.fm for the user."""
    params = {
        'method': 'user.gettopartists',
        'user': LASTFM_USER,
        'api_key': LASTFM_API_KEY,
        'format': 'json',
        'period': 'overall',
        'limit': limit
    }

    resp = requests.get(LASTFM_API_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if 'error' in data:
        raise RuntimeError(f"Last.fm API error: {data.get('message', data)}")

    artists = data.get('topartists', {}).get('artist', [])
    return [a['name'] for a in artists]


def get_genres_from_lastfm(artist_name):
    """Fetches up to 15 raw tags (genres) from Last.fm for an artist."""
    params = {
        'method': 'artist.gettoptags',
        'artist': artist_name,
        'api_key': LASTFM_API_KEY,
        'format': 'json'
    }

    try:
        resp = requests.get(LASTFM_API_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        tags = data.get('toptags', {}).get('tag', [])
        names = [t['name'] for t in tags[:15]]
    except Exception as e:
        print(f"      ⚠️ Could not fetch genres for '{artist_name}': {e}")
        names = []

    while len(names) < 15:
        names.append(None)

    return names


def get_nationality_from_deepseek(artist_name):
    """Uses DeepSeek API to get the nationality of an artist."""
    if not DEEPSEEK_API_KEY:
        return 'Unknown'

    if artist_name in nationality_cache:
        return nationality_cache[artist_name]

    try:
        prompt = f"What country is the musical artist '{artist_name}' from? Respond ONLY with the country name in English, without any additional explanation. If you are not sure, respond 'Unknown'."

        headers = {
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            'Content-Type': 'application/json'
        }

        data = {
            'model': 'deepseek-chat',
            'messages': [
                {'role': 'system', 'content': 'You are a music assistant that answers questions about artists. Respond concisely and accurately, always using English country names.'},
                {'role': 'user', 'content': prompt}
            ],
            'temperature': 0.1,
            'max_tokens': 50
        }

        resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=data, timeout=10)
        resp.raise_for_status()

        result = resp.json()
        nationality = result['choices'][0]['message']['content'].strip()

        if len(nationality) > 50 or 'not sure' in nationality.lower():
            nationality = 'Unknown'

        nationality_cache[artist_name] = nationality
        return nationality

    except Exception as e:
        print(f"      ⚠️ DeepSeek API error for '{artist_name}': {e}")
        return 'Unknown'


# ============================================
# UPDATE FUNCTIONS
# ============================================

def update_artist(conn, name, genres, nationality, image_url, image_source):
    """Updates an artist's data in the database. Preserves id_artist."""
    genre_columns = [f'genre_{n}' for n in range(1, 16)]
    placeholders = ', '.join(['?'] * len(genre_columns))
    set_clause = ', '.join([f'{c} = excluded.{c}' for c in genre_columns])

    cursor = conn.cursor()
    cursor.execute(f'''
        INSERT INTO Artist (name, nationality, artist_image_url, artist_image_source, {', '.join(genre_columns)}, last_update)
        VALUES (?, ?, ?, ?, {placeholders}, CURRENT_TIMESTAMP)
        ON CONFLICT(name) DO UPDATE SET
            nationality = excluded.nationality,
            artist_image_url = excluded.artist_image_url,
            artist_image_source = excluded.artist_image_source,
            {set_clause},
            last_update = CURRENT_TIMESTAMP
    ''', (name, nationality, image_url, image_source, *genres))
    conn.commit()


def update_missing_data(conn, name, status):
    """Intelligently update only missing data for an artist. Preserves id_artist."""
    updated = {
        'nationality': False,
        'image': False,
        'genres': False
    }
    
    if status['missing_nationality']:
        print(f"    🏳️ Fetching nationality...")
        nationality = get_nationality_from_deepseek(name)
        print(f"    📍 Nationality: {nationality}")
        updated['nationality'] = True
    else:
        nationality = None
    
    if status['missing_image']:
        print(f"    🖼️ Fetching image...")
        image_url, image_source = get_artist_image(name)
        if image_url:
            print(f"    ✅ Image found [source: {image_source}]")
        else:
            print(f"    ⚠️ No image found")
            image_url = None
            image_source = None
        updated['image'] = True
    else:
        image_url = None
        image_source = None
    
    if status['missing_genres']:
        print(f"    🏷️ Fetching genres...")
        genres = get_genres_from_lastfm(name)
        print(f"    📋 Genres found: {len([g for g in genres if g])} tags")
        updated['genres'] = True
    else:
        genres = None
    
    if not any(updated.values()):
        print(f"    ✅ Already complete. Skipping.")
        return updated
    
    cursor = conn.cursor()
    cursor.execute('''
        SELECT nationality, artist_image_url, artist_image_source,
               genre_1, genre_2, genre_3, genre_4, genre_5,
               genre_6, genre_7, genre_8, genre_9, genre_10,
               genre_11, genre_12, genre_13, genre_14, genre_15
        FROM Artist
        WHERE name = ?
    ''', (name,))
    row = cursor.fetchone()
    
    if not row:
        return updated
    
    final_nationality = nationality if updated['nationality'] else row[0]
    final_image_url = image_url if updated['image'] else row[1]
    final_image_source = image_source if updated['image'] else row[2]
    
    final_genres = list(row[3:])
    if updated['genres']:
        final_genres = genres
    
    update_artist(conn, name, final_genres, final_nationality, final_image_url, final_image_source)
    
    return updated


def get_artist_id(conn, name):
    """Helper function to get an artist's ID."""
    cursor = conn.cursor()
    cursor.execute('SELECT id_artist FROM Artist WHERE name = ?', (name,))
    row = cursor.fetchone()
    return row[0] if row else None


# ============================================
# MAIN FUNCTION
# ============================================

def create_database():
    """Creates the database and populates it with real data from Last.fm."""

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    print("📋 Checking database schema...")
    create_schema(conn)

    if not LASTFM_API_KEY or not LASTFM_USER:
        print("⚠️ LASTFM_API_KEY / LASTFM_USER not found in environment variables.")
        print("   The table was created, but no artists were imported from Last.fm.")
        conn.close()
        return

    print(f"🔎 Fetching artists from '{LASTFM_USER}' on Last.fm...")
    try:
        artists = get_top_artists(limit=50)
    except Exception as e:
        print(f"❌ Error fetching from Last.fm: {e}")
        conn.close()
        sys.exit(1)

    print(f"🎧 {len(artists)} artists found. Processing...")
    print("=" * 60)

    skipped_count = 0
    updated_count = 0
    new_count = 0

    for i, name in enumerate(artists, start=1):
        print(f"\n  [{i}/{len(artists)}] {name}")

        status = get_artist_status(conn, name)

        if not status['exists']:
            print(f"    🆕 New artist. Fetching all data...")
            
            print(f"    🏳️ Fetching nationality...")
            nationality = get_nationality_from_deepseek(name)
            print(f"    📍 Nationality: {nationality}")
            
            print(f"    🖼️ Fetching image...")
            image_url, image_source = get_artist_image(name)
            if image_url:
                print(f"    ✅ Image found [source: {image_source}]")
            else:
                print(f"    ⚠️ No image found")
            
            print(f"    🏷️ Fetching genres...")
            genres = get_genres_from_lastfm(name)
            print(f"    📋 Genres found: {len([g for g in genres if g])} tags")
            
            update_artist(conn, name, genres, nationality, image_url, image_source)
            new_count += 1
            print(f"    ✅ New artist added (ID: {get_artist_id(conn, name)})")

        elif status['needs_update']:
            print(f"    🔄 Updating missing data...")
            
            current_id = get_artist_id(conn, name)
            
            updated = update_missing_data(conn, name, status)
            
            new_id = get_artist_id(conn, name)
            if current_id == new_id:
                print(f"    ✅ ID preserved: {current_id}")
            else:
                print(f"    ⚠️ WARNING: ID changed from {current_id} to {new_id}!")
            
            updated_fields = []
            if updated['nationality']:
                updated_fields.append('nationality')
            if updated['image']:
                updated_fields.append('image')
            if updated['genres']:
                updated_fields.append('genres')
            
            print(f"    ✅ Updated: {', '.join(updated_fields) if updated_fields else 'nothing (already complete)'}")
            updated_count += 1

        else:
            print(f"    ✅ Already complete. Skipping.")
            skipped_count += 1

        time.sleep(0.3)

    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM Artist')
    total = cursor.fetchone()[0]

    print("\n" + "=" * 60)
    print(f"\n✅ Database updated successfully")
    print(f"📁 Location: {DB_PATH}")
    print(f"📋 Table 'Artist' with genres, nationality and image")
    print(f"🎵 Total artists in DB: {total}")
    print(f"   New artists added: {new_count}")
    print(f"   Artists updated (missing data): {updated_count}")
    print(f"   Artists skipped (already complete): {skipped_count}")
    print("=" * 60)

    conn.close()


if __name__ == "__main__":
    create_database()
