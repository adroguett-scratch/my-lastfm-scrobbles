import sqlite3
import os
import sys
import requests
import time
from datetime import datetime
from dotenv import load_dotenv

# Carga variables desde un archivo .env
load_dotenv()

# Ruta de la base de datos
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', '1_artist.db')

# --- Credenciales ---
LASTFM_API_KEY = os.environ.get('LASTFM_API_KEY')
LASTFM_USER = os.environ.get('LASTFM_USER')
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')

LASTFM_API_URL = 'https://ws.audioscrobbler.com/2.0/'
DEEPSEEK_API_URL = 'https://api.deepseek.com/v1/chat/completions'

# Cache de nacionalidades (para evitar consultas repetidas)
nacionalidad_cache = {}


def crear_esquema(conn):
    """Crea la tabla Artist con columnas de género y nacionalidad."""
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

    # Migración: agregar columna nationality si no existe
    cursor.execute("PRAGMA table_info(Artist)")
    columnas = [fila[1] for fila in cursor.fetchall()]

    if 'nationality' not in columnas:
        cursor.execute('ALTER TABLE Artist ADD COLUMN nationality TEXT')

    # Renombrar 'genre' -> 'genre_1' si viene de una versión muy antigua
    if 'genre' in columnas and 'genre_1' not in columnas:
        cursor.execute('ALTER TABLE Artist RENAME COLUMN genre TO genre_1')
        cursor.execute("PRAGMA table_info(Artist)")
        columnas = [fila[1] for fila in cursor.fetchall()]

    for n in range(1, 16):
        col = f'genre_{n}'
        if col not in columnas:
            cursor.execute(f'ALTER TABLE Artist ADD COLUMN {col} TEXT')

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
    """Obtiene hasta 15 tags (géneros) crudos de un artista desde Last.fm."""
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
        nombres = [t['name'] for t in tags[:15]]
    except Exception as e:
        print(f"  ⚠️ No se pudieron obtener géneros para '{nombre_artista}': {e}")
        nombres = []

    while len(nombres) < 15:
        nombres.append(None)

    return nombres


def obtener_nacionalidad_deepseek(nombre_artista):
    """Usa DeepSeek API para obtener la nacionalidad de un artista."""
    if not DEEPSEEK_API_KEY:
        print("    ⚠️ DeepSeek API Key no configurada. Nacionalidad: Desconocido")
        return 'Desconocido'
    
    # Verificar caché
    if nombre_artista in nacionalidad_cache:
        return nacionalidad_cache[nombre_artista]
    
    try:
        prompt = f"¿De qué país es originario el artista musical '{nombre_artista}'? Responde ÚNICAMENTE con el nombre del país en español, sin explicación adicional. Si no estás seguro, responde 'Desconocido'."
        
        headers = {
            'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        data = {
            'model': 'deepseek-chat',
            'messages': [
                {'role': 'system', 'content': 'Eres un asistente que responde preguntas sobre música. Responde de manera concisa y precisa.'},
                {'role': 'user', 'content': prompt}
            ],
            'temperature': 0.1,
            'max_tokens': 50
        }
        
        resp = requests.post(DEEPSEEK_API_URL, headers=headers, json=data, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        
        nacionalidad = result['choices'][0]['message']['content'].strip()
        
        # Limpiar respuesta
        if len(nacionalidad) > 50 or 'no estoy seguro' in nacionalidad.lower():
            nacionalidad = 'Desconocido'
        
        # Guardar en caché
        nacionalidad_cache[nombre_artista] = nacionalidad
        return nacionalidad
        
    except Exception as e:
        print(f"    ⚠️ Error con DeepSeek API para '{nombre_artista}': {e}")
        return 'Desconocido'


def guardar_artista(conn, nombre, generos, nacionalidad):
    """Inserta o actualiza un artista en la tabla."""
    columnas_genero = [f'genre_{n}' for n in range(1, 16)]
    placeholders = ', '.join(['?'] * len(columnas_genero))
    set_clause = ', '.join([f'{c} = excluded.{c}' for c in columnas_genero])

    cursor = conn.cursor()
    cursor.execute(f'''
        INSERT INTO Artist (name, nationality, {', '.join(columnas_genero)}, last_update)
        VALUES (?, ?, {placeholders}, CURRENT_TIMESTAMP)
        ON CONFLICT(name) DO UPDATE SET
            nationality = excluded.nationality,
            {set_clause},
            last_update = CURRENT_TIMESTAMP
    ''', (nombre, nacionalidad, *generos))
    conn.commit()


def crear_base_datos():
    """Crea la base de datos y la puebla con datos reales de Last.fm."""
    
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
        
        # Obtener géneros
        generos = obtener_generos_lastfm(nombre)
        
        # Obtener nacionalidad con DeepSeek
        print(f"    🏳️ Obteniendo nacionalidad...")
        nacionalidad = obtener_nacionalidad_deepseek(nombre)
        print(f"    📍 Nacionalidad: {nacionalidad}")
        
        guardar_artista(conn, nombre, generos, nacionalidad)
        
        # Pequeña pausa para no saturar APIs
        time.sleep(0.3)

    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM Artist')
    total = cursor.fetchone()[0]

    print(f"\n✅ Base de datos actualizada con éxito")
    print(f"📁 Ubicación: {DB_PATH}")
    print(f"📋 Tabla 'Artist' con géneros y nacionalidad")
    print(f"🎵 Total artistas en BD: {total}")

    conn.close()


if __name__ == "__main__":
    crear_base_datos()
