"""
Artist Genre Filter Module

This module provides functionality to filter and normalize genre tags for artists
using external dictionaries located in the genre_filter_dictionaries directory.
"""

import sqlite3
import os
import sys
import re
from datetime import datetime
from typing import List, Optional, Tuple

# Add the genre_filter_dictionaries directory to path
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'genre_filter_dictionaries'))

from genre_dict import GENRE_DICT, GENERIC_TAGS
from nationality_dict import NATIONALITY_TAGS

# Database paths
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', '0_artist_raw.db')
OUTPUT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), '1_artist_genres.db')


# ============================================
# DECADE TAGS TO DISCARD
# ============================================

DECADE_TAGS = {
    '60s', '70s', '80s', '90s', '00s', '2000s', '2010s', '2020s',
    '1960s', '1970s', '1980s', '1990s', '2000s',
    '60', '70', '80', '90', '00',
    'sixties', 'seventies', 'eighties', 'nineties'
}


# ============================================
# ARTIST KEYWORDS TO DISCARD
# ============================================

ARTIST_KEYWORDS = {
    'band', 'group', 'project', 'ensemble', 'orchestra',
    'orchestral', 'symphony', 'philharmonic', 'chamber',
    'music', 'songs', 'album', 'records', 'live', 'concert',
    'tour', 'festival', 'rock', 'pop', 'metal', 'punk',
    'alternative', 'indie', 'electronic', 'hip hop'
}


# ============================================
# FILTER FUNCTIONS
# ============================================

def is_nationality_tag(tag: str) -> bool:
    """Check if a tag represents a nationality."""
    tag_lower = tag.lower()
    return tag_lower in NATIONALITY_TAGS


def is_decade_tag(tag: str) -> bool:
    """Check if a tag represents a decade."""
    tag_lower = tag.lower()
    return tag_lower in DECADE_TAGS


def is_generic_tag(tag: str) -> bool:
    """Check if a tag is too generic (e.g., 'rock', 'pop', 'metal')."""
    tag_lower = tag.lower()
    return tag_lower in GENERIC_TAGS


def is_artist_keyword(tag: str) -> bool:
    """Check if a tag contains artist keywords (e.g., 'band', 'group')."""
    tag_lower = tag.lower()
    return any(keyword in tag_lower for keyword in ARTIST_KEYWORDS)


def is_artist_name_tag(tag: str, artist_name: str) -> bool:
    """
    Check if a tag is the artist's name (e.g., 'pink floyd' as a tag for Pink Floyd).
    
    Args:
        tag: The tag to check
        artist_name: The artist's name to compare against
        
    Returns:
        True if the tag matches the artist name, False otherwise
    """
    if not tag or not artist_name:
        return False
    
    tag_clean = tag.lower().strip()
    artist_clean = artist_name.lower().strip()
    
    # Direct match
    if tag_clean == artist_clean:
        return True
    
    # Check if tag is a substring of artist name (e.g., 'pink' for 'Pink Floyd')
    if len(tag_clean) >= 3 and tag_clean in artist_clean:
        return True
    
    # Check if artist name is a substring of tag (e.g., 'floyd' for 'Pink Floyd')
    if len(artist_clean) >= 3 and artist_clean in tag_clean:
        return True
    
    # Check for common variations
    common_words = {'the', 'and', 'of', 'for', 'with', 'on', 'at', 'from', 'by'}
    
    tag_words = set(tag_clean.split())
    artist_words = set(artist_clean.split())
    
    # Remove common words
    tag_words = tag_words - common_words
    artist_words = artist_words - common_words
    
    # Check if any significant word matches
    if tag_words and artist_words:
        for tw in tag_words:
            for aw in artist_words:
                if len(tw) >= 3 and len(aw) >= 3 and (tw in aw or aw in tw):
                    return True
    
    return False


def normalize_genre(tag: str) -> str:
    """
    Normalize a genre tag using the GENRE_DICT.
    Returns the normalized genre in lowercase.
    """
    tag_lower = tag.lower()
    if tag_lower in GENRE_DICT:
        return GENRE_DICT[tag_lower].lower()
    return tag.lower()


def should_keep_tag(tag: str, artist_name: str = None) -> bool:
    """
    Determine if a tag should be kept after filtering.
    
    Args:
        tag: The tag to evaluate
        artist_name: The artist's name (to discard artist-name tags)
        
    Returns:
        True if the tag should be kept, False otherwise
    """
    if not tag or not tag.strip():
        return False
    
    tag_lower = tag.lower()
    
    # Discard nationalities
    if is_nationality_tag(tag_lower):
        return False
    
    # Discard decades
    if is_decade_tag(tag_lower):
        return False
    
    # Discard generic tags
    if is_generic_tag(tag_lower):
        return False
    
    # Discard artist keywords
    if is_artist_keyword(tag_lower):
        return False
    
    # Discard tags that are the artist's name
    if artist_name and is_artist_name_tag(tag, artist_name):
        return False
    
    return True


def filter_and_normalize_genres(raw_tags: List[str], artist_name: str = None) -> List[str]:
    """
    Filter and normalize a list of genre tags.
    
    Steps:
    1. Normalize each tag using GENRE_DICT (returns lowercase)
    2. Filter out unwanted tags (nationality, decade, generic, artist keywords, artist name)
    3. Remove duplicates (case-insensitive)
    4. Return unique, clean genres (all lowercase)
    
    Args:
        raw_tags: List of raw genre tags from Last.fm
        artist_name: The artist's name (to discard artist-name tags)
        
    Returns:
        List of filtered and normalized genres (all lowercase)
    """
    if not raw_tags:
        return []
    
    # Step 1: Normalize all tags (returns lowercase)
    normalized = []
    for tag in raw_tags:
        if tag and isinstance(tag, str):
            normalized_tag = normalize_genre(tag)
            if normalized_tag:
                normalized.append(normalized_tag)
    
    # Step 2: Filter unwanted tags
    filtered = []
    for tag in normalized:
        if should_keep_tag(tag, artist_name):
            filtered.append(tag)
    
    # Step 3: Remove duplicates (case-insensitive)
    seen = set()
    unique_genres = []
    for tag in filtered:
        tag_lower = tag.lower()
        if tag_lower not in seen:
            seen.add(tag_lower)
            unique_genres.append(tag_lower)
    
    return unique_genres


def get_top_n_genres(raw_tags: List[str], artist_name: str = None, n: int = 5) -> List[str]:
    """Get the top N genres from a list of raw tags (all lowercase)."""
    filtered = filter_and_normalize_genres(raw_tags, artist_name)
    return filtered[:n]


# ============================================
# DATABASE FUNCTIONS
# ============================================

def get_raw_artists(conn):
    """Get all artists with their raw genres from the raw database."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id_artist, name, nationality,
               genre_1, genre_2, genre_3, genre_4, genre_5,
               genre_6, genre_7, genre_8, genre_9, genre_10,
               genre_11, genre_12, genre_13, genre_14, genre_15
        FROM Artist
    ''')
    
    artists = []
    for row in cursor.fetchall():
        artist_id = row[0]
        name = row[1]
        nationality = row[2]
        # Collect non-None genres
        raw_genres = []
        for i in range(3, 18):  # genre_1 to genre_15
            if row[i] is not None:
                raw_genres.append(row[i])
        artists.append({
            'id': artist_id,
            'name': name,
            'nationality': nationality,
            'raw_genres': raw_genres
        })
    
    return artists


def create_filtered_schema(conn):
    """Create the filtered Artist table."""
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Artist (
            id_artist   INTEGER PRIMARY KEY,
            name        TEXT    NOT NULL UNIQUE,
            nationality TEXT,
            genre_1     TEXT,
            genre_2     TEXT,
            genre_3     TEXT,
            genre_4     TEXT,
            genre_5     TEXT,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_artist_name_filtered ON Artist (name)')
    conn.commit()


def save_filtered_artist(conn, artist_id, name, nationality, genres):
    """Save a filtered artist to the database (genres already in lowercase)."""
    # Pad genres to exactly 5
    while len(genres) < 5:
        genres.append(None)
    
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO Artist (id_artist, name, nationality, genre_1, genre_2, genre_3, genre_4, genre_5, last_update)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (artist_id, name, nationality, genres[0], genres[1], genres[2], genres[3], genres[4]))
    conn.commit()


def get_filtered_stats(conn):
    """Get statistics from the filtered database."""
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM Artist')
    total = cursor.fetchone()[0]
    return total


# ============================================
# MAIN PROCESSING FUNCTION
# ============================================

def process_and_filter_artists():
    """Process raw artists and generate filtered database."""
    
    print("=" * 60)
    print("ARTIST GENRE FILTER")
    print("=" * 60)
    
    # Check if raw database exists
    if not os.path.exists(DB_PATH):
        print(f"❌ Raw database not found: {DB_PATH}")
        print("   Please run 0_artist_db_raw.py first.")
        return
    
    # Connect to raw database
    print(f"📂 Reading from: {DB_PATH}")
    raw_conn = sqlite3.connect(DB_PATH)
    raw_conn.row_factory = sqlite3.Row
    
    # Connect to output database (in current directory)
    print(f"📂 Writing to: {OUTPUT_DB_PATH}")
    out_conn = sqlite3.connect(OUTPUT_DB_PATH)
    
    # Create filtered schema
    create_filtered_schema(out_conn)
    
    # Get all raw artists
    artists = get_raw_artists(raw_conn)
    print(f"🎵 Found {len(artists)} artists to process")
    print("-" * 60)
    
    processed = 0
    skipped = 0
    
    for artist in artists:
        name = artist['name']
        raw_genres = artist['raw_genres']
        nationality = artist['nationality'] or 'Unknown'
        
        print(f"\n🎵 Processing: {name}")
        print(f"   Raw genres: {raw_genres}")
        
        # Filter and normalize genres (all lowercase)
        clean_genres = filter_and_normalize_genres(raw_genres, artist_name=name)
        
        # Get top 5 genres
        top_genres = clean_genres[:5]
        
        # Skip if no genres remain after filtering
        if not top_genres:
            print(f"   ⚠️ No genres after filtering")
            skipped += 1
            continue
        
        # Save filtered artist
        save_filtered_artist(out_conn, artist['id'], name, nationality, top_genres)
        processed += 1
        
        print(f"   ✅ Filtered genres: {top_genres}")
    
    # Commit and close
    out_conn.commit()
    
    # Get statistics
    total = get_filtered_stats(out_conn)
    
    print("-" * 60)
    print(f"\n✅ Filtered database created successfully")
    print(f"📁 Location: {OUTPUT_DB_PATH}")
    print(f"📋 Table 'Artist' with filtered genres (top 5, all lowercase)")
    print(f"🎵 Total artists in filtered DB: {total}")
    print(f"   Processed: {processed}")
    print(f"   Skipped (no genres): {skipped}")
    print("=" * 60)
    
    raw_conn.close()
    out_conn.close()


if __name__ == "__main__":
    process_and_filter_artists()
