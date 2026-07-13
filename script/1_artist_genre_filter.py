"""
Artist Genre Filter Module

This module provides functionality to filter and normalize genre tags for artists
using external dictionaries located in the genre_filter_dictionaries directory.
"""

import os
import sys
from datetime import datetime
from typing import List, Optional, Tuple

# Add the genre_filter_dictionaries directory to path
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'genre_filter_dictionaries'))

from genre_dict import GENRE_DICT, GENERIC_TAGS
from nationality_dict import NATIONALITY_TAGS


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
    'orchestral', 'symphony', 'philharmonic', 'chamber'
}


# ============================================
# FILTER FUNCTIONS
# ============================================

def is_nationality_tag(tag: str) -> bool:
    """
    Check if a tag represents a nationality.
    
    Args:
        tag: The tag to check
        
    Returns:
        True if the tag is a nationality, False otherwise
    """
    tag_lower = tag.lower()
    return tag_lower in NATIONALITY_TAGS


def is_decade_tag(tag: str) -> bool:
    """
    Check if a tag represents a decade.
    
    Args:
        tag: The tag to check
        
    Returns:
        True if the tag is a decade, False otherwise
    """
    tag_lower = tag.lower()
    return tag_lower in DECADE_TAGS


def is_generic_tag(tag: str) -> bool:
    """
    Check if a tag is too generic (e.g., 'rock', 'pop', 'metal').
    
    Args:
        tag: The tag to check
        
    Returns:
        True if the tag is generic, False otherwise
    """
    tag_lower = tag.lower()
    return tag_lower in GENERIC_TAGS


def is_artist_keyword(tag: str) -> bool:
    """
    Check if a tag contains artist keywords (e.g., 'band', 'group').
    
    Args:
        tag: The tag to check
        
    Returns:
        True if the tag contains artist keywords, False otherwise
    """
    tag_lower = tag.lower()
    return any(keyword in tag_lower for keyword in ARTIST_KEYWORDS)


def normalize_genre(tag: str) -> str:
    """
    Normalize a genre tag using the GENRE_DICT.
    
    Args:
        tag: The raw genre tag
        
    Returns:
        The normalized genre, or the original if not found
    """
    tag_lower = tag.lower()
    
    if tag_lower in GENRE_DICT:
        return GENRE_DICT[tag_lower]
    
    return tag


def should_keep_tag(tag: str) -> bool:
    """
    Determine if a tag should be kept after filtering.
    
    Args:
        tag: The tag to evaluate
        
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
    
    return True


def filter_and_normalize_genres(raw_tags: List[str]) -> List[str]:
    """
    Filter and normalize a list of genre tags.
    
    Steps:
    1. Normalize each tag using GENRE_DICT
    2. Filter out unwanted tags (nationality, decade, generic, artist keywords)
    3. Remove duplicates (case-insensitive)
    4. Return unique, clean genres
    
    Args:
        raw_tags: List of raw genre tags from Last.fm
        
    Returns:
        List of filtered and normalized genres
    """
    if not raw_tags:
        return []
    
    # Step 1: Normalize all tags
    normalized = []
    for tag in raw_tags:
        if tag and isinstance(tag, str):
            normalized_tag = normalize_genre(tag)
            if normalized_tag:
                normalized.append(normalized_tag)
    
    # Step 2: Filter unwanted tags
    filtered = [tag for tag in normalized if should_keep_tag(tag)]
    
    # Step 3: Remove duplicates (case-insensitive)
    seen = set()
    unique_genres = []
    for tag in filtered:
        tag_lower = tag.lower()
        if tag_lower not in seen:
            seen.add(tag_lower)
            unique_genres.append(tag)
    
    return unique_genres


def get_top_n_genres(raw_tags: List[str], n: int = 5) -> List[str]:
    """
    Get the top N genres from a list of raw tags.
    
    Args:
        raw_tags: List of raw genre tags from Last.fm
        n: Number of genres to return (default: 5)
        
    Returns:
        List of top N filtered and normalized genres
    """
    filtered = filter_and_normalize_genres(raw_tags)
    return filtered[:n]


# ============================================
# ARTIST RECORD FUNCTIONS
# ============================================

def create_artist_record(
    artist_id: int,
    name: str,
    nationality: str,
    genres: List[str],
    entry_date: Optional[datetime] = None
) -> Tuple[int, str, str, List[str], str]:
    """
    Create a standardized artist record.
    
    Args:
        artist_id: Unique identifier for the artist
        name: Artist name
        nationality: Artist nationality
        genres: List of genres (already filtered and normalized)
        entry_date: Date the artist was added (defaults to current time)
        
    Returns:
        Tuple containing (id, name, nationality, genres, entry_date)
    """
    if entry_date is None:
        entry_date = datetime.now()
    
    # Ensure we have exactly 5 genres (pad with empty strings if needed)
    while len(genres) < 5:
        genres.append('')
    
    return (artist_id, name, nationality, genres[:5], entry_date.isoformat())


def format_artist_record(record: Tuple[int, str, str, List[str], str]) -> str:
    """
    Format an artist record for display.
    
    Args:
        record: Tuple containing (id, name, nationality, genres, entry_date)
        
    Returns:
        Formatted string representation of the artist record
    """
    artist_id, name, nationality, genres, entry_date = record
    
    genres_str = ', '.join([g for g in genres if g])
    if not genres_str:
        genres_str = 'No genres'
    
    return (
        f"ID: {artist_id}\n"
        f"Name: {name}\n"
        f"Nationality: {nationality}\n"
        f"Genres: {genres_str}\n"
        f"Entry Date: {entry_date}\n"
    )


# ============================================
# BATCH PROCESSING FUNCTIONS
# ============================================

def process_artist_genres(
    artist_name: str,
    raw_genres: List[str],
    nationality: str = 'Unknown',
    artist_id: Optional[int] = None
) -> Tuple[int, str, str, List[str], str]:
    """
    Process an artist's genres and create a standardized record.
    
    Args:
        artist_name: Name of the artist
        raw_genres: Raw genre tags from Last.fm
        nationality: Artist nationality
        artist_id: Optional artist ID (will be auto-generated if not provided)
        
    Returns:
        Artist record tuple (id, name, nationality, genres, entry_date)
    """
    # Filter and normalize genres
    clean_genres = filter_and_normalize_genres(raw_genres)
    
    # Generate ID if not provided
    if artist_id is None:
        artist_id = hash(artist_name) % 1000000
    
    # Create and return record
    return create_artist_record(
        artist_id=artist_id,
        name=artist_name,
        nationality=nationality,
        genres=clean_genres
    )


def process_artist_batch(
    artists_data: List[Tuple[str, List[str], str]]
) -> List[Tuple[int, str, str, List[str], str]]:
    """
    Process a batch of artists and return their records.
    
    Args:
        artists_data: List of tuples (artist_name, raw_genres, nationality)
        
    Returns:
        List of artist records
    """
    records = []
    
    for idx, (name, raw_genres, nationality) in enumerate(artists_data, start=1):
        record = process_artist_genres(
            artist_name=name,
            raw_genres=raw_genres,
            nationality=nationality,
            artist_id=idx
        )
        records.append(record)
    
    return records


# ============================================
# MAIN FUNCTION (FOR TESTING)
# ============================================

def main():
    """
    Test the genre filtering functionality.
    """
    # Example raw genres from Last.fm
    test_artists = [
        (
            "Pink Floyd",
            [
                "progressive rock", "psychedelic rock", "classic rock",
                "rock", "psychedelic", "70s", "british", "art rock"
            ],
            "United Kingdom"
        ),
        (
            "Tool",
            [
                "progressive metal", "progressive rock", "metal",
                "alternative", "rock", "american", "90s"
            ],
            "United States"
        ),
        (
            "Änglagård",
            [
                "progressive rock", "symphonic prog", "swedish",
                "instrumental", "progressive", "70s"
            ],
            "Sweden"
        ),
    ]
    
    print("=" * 60)
    print("ARTIST GENRE FILTER TEST")
    print("=" * 60)
    
    for name, raw_genres, nationality in test_artists:
        print(f"\n🎵 Processing: {name}")
        print(f"   Raw genres: {raw_genres}")
        
        clean_genres = filter_and_normalize_genres(raw_genres)
        
        print(f"   Clean genres: {clean_genres}")
        print(f"   Nationality: {nationality}")
        
        # Create a record
        record = process_artist_genres(
            artist_name=name,
            raw_genres=raw_genres,
            nationality=nationality
        )
        
        print("\n   📋 Record:")
        print(f"      {format_artist_record(record)}")


if __name__ == "__main__":
    main()
