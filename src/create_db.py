import sqlite3
import os
import sys
import time
import requests
from datetime import datetime

# Ruta de la base de datos
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'my_scrobbles.db')

# --- Credenciales (deben venir de variables de entorno / GitHub Secrets) ---
LASTFM_API_KEY = os.environ.get('LASTFM_API_KEY')
LASTFM_USER = os.environ.get('LASTFM_USER')

LASTFM_API_URL = 'https://ws.audioscrobbler.com/2.0/'
MUSICBRAINZ_API_URL = 'https://musicbrainz.org/ws/2/artist/'

# MusicBrainz exige un User-Agent identificable y máx. ~1 request/segundo
HEADERS_MB = {
    'User-Agent': 'MyScrobblesDBBot/1.0 (contacto@example.com)'
}


def crear_esquema(conn):
    """Crea la tabla Artist (con columna de nacionalidad) y su índice."""
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Artist (
            id_artist   INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            genre       TEXT,
            genre_2     TEXT,
            genre_3     TEXT,
            genre_4     TEXT,
            genre_5     TEXT,
            nationality TEXT,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    cursor.execute('CREATE INDEX IF NOT EXISTS idx_artist_name ON Artist (name)')

    # Si la tabla ya existía de una versión anterior sin la columna, la añadimos
    cursor.execute("PRAGMA table_info(Artist)")
    columnas = [fila[1] for fila in cursor.fetchall()]
    if 'nationality' not in columnas:
        cursor.execute('ALTER TABLE Artist ADD COLUMN nationality TEXT')

    conn.commit()


def obtener_top_artistas(limit=50):
    """Trae los artistas más escuchados del usuario desde Last.fm."""
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
        raise RuntimeError(f"Error de Last.fm API: {data.get('message', data)}")

    artistas = data.get('topartists', {}).get('artist', [])
    return [a['name'] for a in artistas]


def obtener_generos_lastfm(nombre_artista):
    """Obtiene hasta 5 tags (géneros) de un artista desde Last.fm."""
    params = {
        'method': 'artist.gettoptags',
        'artist': nombre_artista,
        'api_key': LASTFM_API_KEY,
        'format': 'json'
    }

    try:
        resp = requests.get(LASTFM_API_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        tags = data.get('toptags', {}).get('tag', [])
        nombres = [t['name'] for t in tags[:5]]
    except Exception as e:
        print(f"  ⚠️ No se pudieron obtener géneros para '{nombre_artista}': {e}")
        nombres = []

    # Rellenar hasta 5 posiciones con None
    while len(nombres) < 5:
        nombres.append(None)

    return nombres


def obtener_nacionalidad_musicbrainz(nombre_artista):
    """Busca el artista en MusicBrainz y devuelve su país/área de origen, si existe."""
    params = {
        'query': f'artist:{nombre_artista}',
        'fmt': 'json',
        'limit': 1
    }

    try:
        resp = requests.get(MUSICBRAINZ_API_URL, params=params, headers=HEADERS_MB, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        artistas = data.get('artists', [])

        if not artistas:
            return None

        artista = artistas[0]
        # 'area' o 'begin-area' suelen contener el país/ciudad de origen
        area = artista.get('area') or artista.get('begin-area')
        if area:
            return area.get('name')
        return None
    except Exception as e:
        print(f"  ⚠️ No se pudo obtener nacionalidad para '{nombre_artista}': {e}")
        return None
    finally:
        # Respeta el límite de MusicBrainz (~1 request/seg)
        time.sleep(1)


def guardar_artista(conn, nombre, generos, nacionalidad):
    """Inserta o actualiza un artista en la tabla."""
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO Artist (name, genre, genre_2, genre_3, genre_4, genre_5, nationality, last_update)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(name) DO UPDATE SET
            genre = excluded.genre,
            genre_2 = excluded.genre_2,
            genre_3 = excluded.genre_3,
            genre_4 = excluded.genre_4,
            genre_5 = excluded.genre_5,
            nationality = COALESCE(excluded.nationality, Artist.nationality),
            last_update = CURRENT_TIMESTAMP
    ''', (nombre, *generos, nacionalidad))
    conn.commit()


def crear_base_datos():
    """Crea la base de datos, la tabla Artist, y la puebla con datos reales de Last.fm."""

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    crear_esquema(conn)

    if not LASTFM_API_KEY or not LASTFM_USER:
        print("⚠️ No se encontraron LASTFM_API_KEY / LASTFM_USER en las variables de entorno.")
        print("   La tabla se creó, pero no se importaron artistas desde Last.fm.")
        conn.close()
        return

    print(f"🔎 Consultando artistas de '{LASTFM_USER}' en Last.fm...")
    try:
        artistas = obtener_top_artistas(limit=50)
    except Exception as e:
        print(f"❌ Error al consultar Last.fm: {e}")
        conn.close()
        sys.exit(1)

    print(f"🎧 {len(artistas)} artistas encontrados. Procesando...")

    for i, nombre in enumerate(artistas, start=1):
        print(f"  [{i}/{len(artistas)}] {nombre}")
        generos = obtener_generos_lastfm(nombre)
        nacionalidad = obtener_nacionalidad_musicbrainz(nombre)
        guardar_artista(conn, nombre, generos, nacionalidad)

    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM Artist')
    total = cursor.fetchone()[0]

    print(f"\n✅ Base de datos actualizada con éxito")
    print(f"📁 Ubicación: {DB_PATH}")
    print(f"📋 Tabla 'Artist' con géneros + nacionalidad")
    print(f"🎵 Total artistas en BD: {total}")

    conn.close()


if __name__ == "__main__":
    crear_base_datos()
