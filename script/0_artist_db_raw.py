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

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Artist (
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

    # Migration: add columns if they don't exist
    cursor.execute("PRAGMA table_info(Artist)")
    columns = [row[1] for row in cursor.fetchall()]

    if 'nationality' not in columns:
        cursor.execute('ALTER TABLE Artist ADD COLUMN nationality TEXT')

    if 'artist_image_url' not in columns:
        cursor.execute('ALTER TABLE Artist ADD COLUMN artist_image_url TEXT')

    if 'artist_image_source' not in columns:
        cursor.execute('ALTER TABLE Artist ADD COLUMN artist_image_source TEXT')

    if 'genre' in columns and 'genre_1' not in columns:
        cursor.execute('ALTER TABLE Artist RENAME COLUMN genre TO genre_1')
        cursor.execute("PRAGMA table_info(Artist)")
        columns = [row[1] for row in cursor.fetchall()]

    for n in range(1, 16):
        col = f'genre_{n}'
        if col not in columns:
            cursor.execute(f'ALTER TABLE Artist ADD COLUMN {col} TEXT')

    conn.commit()


def get_artist_status(conn, name):
    """
    Check what data an artist has.
    Returns a dict with boolean flags for what's missing.
    """
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
    
    # Check what's missing
    missing_nationality = row[0] is None or row[0] == '' or row[0] == 'Unknown'
    missing_image = row[1] is None or row[1] == ''
    
    # Check genres
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

        for img in images:
            if img.get('size') == 'extralarge':
                return img.get('#text')

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
            return artists[0].get('picture_big')

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
    if image:
        return image, 'lastfm'

    # 2. Try Deezer
    image = get_artist_image_from_deezer(artist_name)
    if image:
        return image, 'deezer'

    # 3. Try DeepSeek (last resort)
    image = get_artist_image_from_deepseek(artist_name)
    if image:
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
    """Updates an artist's data in the database."""
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
    """
    Intelligently update only missing data for an artist.
    Returns a dict with what was updated.
    """
    updated = {
        'nationality': False,
        'image': False,
        'genres': False
    }
    
    # 1. Update nationality if missing
    if status['missing_nationality']:
        print(f"    🏳️ Fetching nationality...")
        nationality = get_nationality_from_deepseek(name)
        print(f"    📍 Nationality: {nationality}")
        updated['nationality'] = True
    else:
        nationality = None  # Will be filled from existing data
    
    # 2. Update image if missing
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
    
    # 3. Update genres if missing
    if status['missing_genres']:
        print(f"    🏷️ Fetching genres...")
        genres = get_genres_from_lastfm(name)
        print(f"    📋 Genres found: {len([g for g in genres if g])} tags")
        updated['genres'] = True
    else:
        genres = None
    
    # If nothing was missing, skip
    if not any(updated.values()):
        print(f"    ✅ Already complete. Skipping.")
        return updated
    
    # Get existing data to merge with missing data
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
    
    # Merge: use existing values if not updated, otherwise use new values
    final_nationality = nationality if updated['nationality'] else row[0]
    final_image_url = image_url if updated['image'] else row[1]
    final_image_source = image_source if updated['image'] else row[2]
    
    final_genres = list(row[3:])  # Start with existing genres
    if updated['genres']:
        final_genres = genres  # Replace with new genres
    
    # Save the merged data
    update_artist(conn, name, final_genres, final_nationality, final_image_url, final_image_source)
    
    return updated


# ============================================
# MAIN FUNCTION
# ============================================

def create_database():
    """Creates the database and populates it with real data from Last.fm."""

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

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

        # Check current status of the artist
        status = get_artist_status(conn, name)

        if not status['exists']:
            # NEW ARTIST: fetch everything
            print(f"    🆕 New artist. Fetching all data...")
            
            # Get all data
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
            
            # Save all data
            update_artist(conn, name, genres, nationality, image_url, image_source)
            new_count += 1
            print(f"    ✅ New artist added")

        elif status['needs_update']:
            # EXISTING ARTIST WITH MISSING DATA: only fetch what's missing
            print(f"    🔄 Updating missing data...")
            updated = update_missing_data(conn, name, status)
            
            # Print what was updated
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
            # COMPLETE ARTIST: skip
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
