import sqlite3
import os
import sys
import requests
import time
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database path
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', '1_artist.db')

# --- Credentials ---
LASTFM_API_KEY = os.environ.get('LASTFM_API_KEY')
LASTFM_USER = os.environ.get('LASTFM_USER')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')

LASTFM_API_URL = 'https://ws.audioscrobbler.com/2.0/'
DEEPSEEK_API_URL = 'https://api.deepseek.com/v1/chat/completions'

# Nationality cache (to avoid repeated API calls)
nationality_cache = {}


def create_schema(conn):
    """Creates the Artist table with genre and nationality columns."""
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Artist (
            id_artist   INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            nationality TEXT,
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

    # Migration: add nationality column if it doesn't exist
    cursor.execute("PRAGMA table_info(Artist)")
    columns = [row[1] for row in cursor.fetchall()]

    if 'nationality' not in columns:
        cursor.execute('ALTER TABLE Artist ADD COLUMN nationality TEXT')

    # Rename 'genre' -> 'genre_1' if coming from a very old version
    if 'genre' in columns and 'genre_1' not in columns:
        cursor.execute('ALTER TABLE Artist RENAME COLUMN genre TO genre_1')
        cursor.execute("PRAGMA table_info(Artist)")
        columns = [row[1] for row in cursor.fetchall()]

    for n in range(1, 16):
        col = f'genre_{n}'
        if col not in columns:
            cursor.execute(f'ALTER TABLE Artist ADD COLUMN {col} TEXT')

    conn.commit()


def artist_exists(conn, name):
    """Check if an artist already exists in the database."""
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM Artist WHERE name = ?', (name,))
    count = cursor.fetchone()[0]
    return count > 0


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
        print(f"  ⚠️ Could not fetch genres for '{artist_name}': {e}")
        names = []

    while len(names) < 15:
        names.append(None)

    return names


def get_nationality_from_deepseek(artist_name):
    """Uses DeepSeek API to get the nationality of an artist."""
    if not DEEPSEEK_API_KEY:
        print("    ⚠️ DeepSeek API Key not configured. Nationality: Unknown")
        return 'Unknown'
    
    # Check cache
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
                {'role': 'system', 'content': 'You are a music assistant that answers questions about artists. Respond concisely and accurately.'},
                {'role': 'user', 'content': prompt}
            ],
            'temperature': 0.1,
            'max_tokens': 50
        }
        
        resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=data, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        
        nationality = result['choices'][0]['message']['content'].strip()
        
        # Clean up response
        if len(nationality) > 50 or 'not sure' in nationality.lower():
            nationality = 'Unknown'
        
        # Save to cache
        nationality_cache[artist_name] = nationality
        return nationality
        
    except Exception as e:
        print(f"    ⚠️ DeepSeek API error for '{artist_name}': {e}")
        return 'Unknown'


def save_artist(conn, name, genres, nationality):
    """Inserts or updates an artist in the database."""
    genre_columns = [f'genre_{n}' for n in range(1, 16)]
    placeholders = ', '.join(['?'] * len(genre_columns))
    set_clause = ', '.join([f'{c} = excluded.{c}' for c in genre_columns])

    cursor = conn.cursor()
    cursor.execute(f'''
        INSERT INTO Artist (name, nationality, {', '.join(genre_columns)}, last_update)
        VALUES (?, ?, {placeholders}, CURRENT_TIMESTAMP)
        ON CONFLICT(name) DO UPDATE SET
            nationality = excluded.nationality,
            {set_clause},
            last_update = CURRENT_TIMESTAMP
    ''', (name, nationality, *genres))
    conn.commit()


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

    skipped_count = 0
    new_count = 0

    for i, name in enumerate(artists, start=1):
        # ============================================================
        # IMPORTANT: Skip if artist already exists in the database
        # ============================================================
        if artist_exists(conn, name):
            print(f"  [{i}/{len(artists)}] {name} ⏭️  Already exists. Skipping.")
            skipped_count += 1
            continue

        print(f"  [{i}/{len(artists)}] {name}")
        
        # Get genres from Last.fm
        genres = get_genres_from_lastfm(name)
        
        # Get nationality with DeepSeek
        print(f"    🏳️ Fetching nationality...")
        nationality = get_nationality_from_deepseek(name)
        print(f"    📍 Nationality: {nationality}")
        
        save_artist(conn, name, genres, nationality)
        new_count += 1
        
        # Small delay to avoid API rate limits
        time.sleep(0.3)

    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM Artist')
    total = cursor.fetchone()[0]

    print(f"\n✅ Database updated successfully")
    print(f"📁 Location: {DB_PATH}")
    print(f"📋 Table 'Artist' with genres and nationality")
    print(f"🎵 Total artists in DB: {total}")
    print(f"   New artists added: {new_count}")
    print(f"   Skipped (already existed): {skipped_count}")

    conn.close()


if __name__ == "__main__":
    create_database()
