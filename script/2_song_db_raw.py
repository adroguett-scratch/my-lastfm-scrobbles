"""
Song Database - Raw Data Fetcher

This module fetches song data and scrobble history from Last.fm and stores it in a SQLite database.
It retrieves:
- Songs (id_artist, title, duration, playcount)
- Scrobbles (id_song, timestamp)

The first run scans ALL scrobbles. Subsequent runs only fetch new ones (incremental update).
"""

import sqlite3
import os
import sys
import requests
import time
from datetime import datetime
from typing import List, Dict, Optional, Tuple
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
    """Creates the Song and Scrobble tables."""
    cursor = conn.cursor()

    # Table: Song
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Song (
            id_song     INTEGER PRIMARY KEY AUTOINCREMENT,
            id_artist   INTEGER NOT NULL,
            title       TEXT    NOT NULL,
            duration    INTEGER,
            playcount   INTEGER,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (id_artist, title)
        )
    ''')

    # Table: Scrobble
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Scrobble (
            id_scrobble INTEGER PRIMARY KEY AUTOINCREMENT,
            id_song     INTEGER NOT NULL,
            timestamp   TIMESTAMP NOT NULL,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (id_song) REFERENCES Song(id_song) ON DELETE CASCADE
        )
    ''')

    # Indexes
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_song_artist ON Song (id_artist)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_song_title ON Song (title)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_scrobble_song ON Scrobble (id_song)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_scrobble_timestamp ON Scrobble (timestamp)')

    # Table to track the last update timestamp
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Metadata (
            key     TEXT PRIMARY KEY,
            value   TEXT,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()


def get_last_scrobble_timestamp(conn) -> Optional[str]:
    """Get the most recent scrobble timestamp from the database."""
    cursor = conn.cursor()
    cursor.execute('SELECT MAX(timestamp) FROM Scrobble')
    row = cursor.fetchone()
    return row[0] if row and row[0] else None


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


def get_song_id(conn, id_artist: int, title: str) -> Optional[int]:
    """Get song ID if it already exists."""
    cursor = conn.cursor()
    cursor.execute('SELECT id_song FROM Song WHERE id_artist = ? AND title = ?', (id_artist, title))
    row = cursor.fetchone()
    return row[0] if row else None


def save_song(conn, id_artist: int, title: str, duration: Optional[int] = None):
    """Save a song to the database. Returns the song ID."""
    cursor = conn.cursor()
    
    # Check if song exists
    existing = get_song_id(conn, id_artist, title)
    if existing:
        return existing
    
    # Insert new song
    cursor.execute('''
        INSERT INTO Song (id_artist, title, duration, last_update)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    ''', (id_artist, title, duration))
    conn.commit()
    
    return cursor.lastrowid


def save_scrobble(conn, id_song: int, timestamp: str):
    """Save a scrobble to the database."""
    cursor = conn.cursor()
    
    # Check if scrobble already exists (avoid duplicates)
    cursor.execute('''
        SELECT id_scrobble FROM Scrobble
        WHERE id_song = ? AND timestamp = ?
    ''', (id_song, timestamp))
    
    if cursor.fetchone():
        return  # Scrobble already exists
    
    cursor.execute('''
        INSERT INTO Scrobble (id_song, timestamp, last_update)
        VALUES (?, ?, CURRENT_TIMESTAMP)
    ''', (id_song, timestamp))
    conn.commit()


def update_song_playcount(conn, id_song: int):
    """Update the playcount for a song."""
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE Song
        SET playcount = (
            SELECT COUNT(*) FROM Scrobble WHERE id_song = ?
        ),
        last_update = CURRENT_TIMESTAMP
        WHERE id_song = ?
    ''', (id_song, id_song))
    conn.commit()


# ============================================
# LAST.FM API FUNCTIONS
# ============================================

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
    """Process a single scrobble and save it to the database."""
    # Skip if it's the currently playing track (no timestamp)
    if '@attr' in scrobble_data and scrobble_data['@attr'].get('nowplaying') == 'true':
        return
    
    artist_name = scrobble_data['artist']['#text']
    song_title = scrobble_data['name']
    timestamp = scrobble_data['date']['#text']
    
    # Parse timestamp to ISO format
    try:
        dt = datetime.strptime(timestamp, "%d %b %Y, %H:%M")
        timestamp_iso = dt.isoformat()
    except ValueError:
        try:
            dt = datetime.strptime(timestamp, "%d %b %Y %H:%M")
            timestamp_iso = dt.isoformat()
        except ValueError:
            print(f"    ⚠️ Could not parse timestamp: {timestamp}")
            return
    
    # Get artist ID from artist database
    artist_conn = sqlite3.connect(ARTIST_DB_PATH)
    id_artist = get_artist_id(artist_conn, artist_name)
    artist_conn.close()
    
    if not id_artist:
        print(f"    ⚠️ Artist not found: {artist_name}")
        return
    
    # Save song
    id_song = save_song(conn, id_artist, song_title)
    
    # Save scrobble
    save_scrobble(conn, id_song, timestamp_iso)


def fetch_all_scrobbles(conn, limit: int = 200):
    """
    Fetch all scrobbles from Last.fm.
    If there are existing scrobbles, only fetch new ones.
    """
    # Get the last scrobble timestamp from the database
    last_timestamp = get_last_scrobble_timestamp(conn)
    
    # Convert to UNIX timestamp if exists
    from_timestamp = None
    if last_timestamp:
        try:
            dt = datetime.fromisoformat(last_timestamp)
            from_timestamp = int(dt.timestamp())
            print(f"📌 Last scrobble timestamp: {last_timestamp} (UNIX: {from_timestamp})")
            print("   Fetching only new scrobbles...")
        except ValueError:
            print(f"   ⚠️ Could not parse timestamp: {last_timestamp}. Fetching all scrobbles...")
    
    page = 1
    total_processed = 0
    total_new = 0
    
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
    
    # Update playcounts for all songs
    print("\n🔄 Updating playcounts...")
    cursor = conn.cursor()
    cursor.execute('SELECT id_song FROM Song')
    songs = cursor.fetchall()
    for (id_song,) in songs:
        update_song_playcount(conn, id_song)
    
    # Update metadata
    now = datetime.now().isoformat()
    set_last_update_time(conn, now)
    
    return total_processed


def get_stats(conn):
    """Get database statistics."""
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM Song')
    total_songs = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM Scrobble')
    total_scrobbles = cursor.fetchone()[0]
    
    cursor.execute('SELECT MAX(timestamp) FROM Scrobble')
    latest = cursor.fetchone()[0]
    
    return {
        'total_songs': total_songs,
        'total_scrobbles': total_scrobbles,
        'latest_scrobble': latest
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
        print("   Fetching only new scrobbles...")
    else:
        print("📂 New database. Fetching ALL scrobbles...")
    
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
    
    print("-" * 60)
    print(f"\n✅ Database updated successfully")
    print(f"📁 Location: {SONG_DB_PATH}")
    print(f"📋 Tables: Song, Scrobble")
    print(f"🎵 Total songs: {stats['total_songs']}")
    print(f"🎧 Total scrobbles: {stats['total_scrobbles']}")
    print(f"📅 Latest scrobble: {stats['latest_scrobble']}")
    print("=" * 60)
    
    conn.close()


if __name__ == "__main__":
    create_database()
