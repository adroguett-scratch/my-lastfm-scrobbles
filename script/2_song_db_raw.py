"""
Song Database - Raw Data Fetcher

This module fetches song data from Last.fm and stores it in a SQLite database.
It retrieves:
- Songs (id_artist, title, duration)

The first run scans ALL scrobbles. Subsequent runs only fetch new ones (incremental update).
"""

import sqlite3
import os
import sys
import requests
import time
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database paths
ARTIST_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', '0_artist_raw.db')
SONG_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', '2_songs_raw.db')

# --- Credentials ---
LASTFM_API_KEY = os.environ.get('LASTFM_API_KEY')
LASTFM_USER = os.environ.get('LASTFM_USER')

LASTFM_API_URL = 'https://ws.audioscrobbler.com/2.0/'


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
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (id_artist, title)
        )
    ''')

    # Indexes
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_song_artist ON Song (id_artist)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_song_title ON Song (title)')

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


def save_song(conn, id_artist: int, title: str, duration: Optional[int] = None):
    """Save a song to the database. Returns the song ID."""
    cursor = conn.cursor()
    
    # Check if song exists
    if song_exists(conn, id_artist, title):
        return None  # Already exists, skip
    
    # Insert new song
    cursor.execute('''
        INSERT INTO Song (id_artist, title, duration, last_update)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    ''', (id_artist, title, duration))
    conn.commit()
    
    return cursor.lastrowid


# ============================================
# LAST.FM API FUNCTIONS
# ============================================

def fetch_song_info(artist_name: str, song_title: str) -> Optional[int]:
    """
    Fetch song duration from Last.fm track.getInfo API.
    Returns duration in seconds, or None if not found.
    """
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
        
        if duration:
            return int(duration) // 1000  # Convert ms to seconds
        
        return None
        
    except Exception as e:
        print(f"      ⚠️ Could not fetch duration for '{artist_name} - {song_title}': {e}")
        return None


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
    """Process a single scrobble and save the song (if new)."""
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
    
    # Check if song already exists
    if song_exists(conn, id_artist, song_title):
        return
    
    # Fetch duration from Last.fm
    print(f"    🎵 New song: {song_title}")
    duration = fetch_song_info(artist_name, song_title)
    if duration:
        minutes = duration // 60
        seconds = duration % 60
        print(f"      ⏱️ Duration: {minutes}:{seconds:02d} ({duration}s)")
    else:
        print(f"      ⏱️ Duration: Unknown")
    
    # Save song
    save_song(conn, id_artist, song_title, duration)


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
    total_new_songs = 0
    
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
    
    cursor.execute('SELECT SUM(duration) FROM Song')
    total_duration = cursor.fetchone()[0]
    
    return {
        'total_songs': total_songs,
        'songs_with_duration': songs_with_duration,
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
    if stats['total_duration_seconds']:
        print(f"📊 Total duration: {total_hours}h {remaining_seconds//60}m")
    print("=" * 60)
    
    conn.close()


if __name__ == "__main__":
    create_database()
