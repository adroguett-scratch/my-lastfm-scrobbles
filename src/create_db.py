import sqlite3
import os
from datetime import datetime

# Ruta de la base de datos
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'my_scrobbles.db')

def crear_base_datos():
    """Crea la base de datos y la tabla Artist con un artista de ejemplo"""
    
    # Crear la carpeta data si no existe
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    # Conectar a la base de datos (se crea automáticamente)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Crear tabla Artist (con 5 géneros fijos)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS Artist (
            id_artist   INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            genre       TEXT,
            genre_2     TEXT,
            genre_3     TEXT,
            genre_4     TEXT,
            genre_5     TEXT,
            last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 2. Crear índice para búsquedas rápidas
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_artist_name ON Artist (name)')
    
    # 3. Insertar artista de ejemplo (Änglagård)
    cursor.execute('SELECT COUNT(*) FROM Artist WHERE name = ?', ('Änglagård',))
    existe = cursor.fetchone()[0]
    
    if existe == 0:
        cursor.execute('''
            INSERT INTO Artist (name, genre, genre_2, genre_3, genre_4, genre_5)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            'Änglagård',
            'Progressive rock',
            'Symphonic prog',
            'Swedish',
            'Instrumental',
            'Progressive'
        ))
        print("🎵 Artista de ejemplo insertado: Änglagård")
    else:
        print("ℹ️ Änglagård ya existe en la base de datos")
    
    # 4. Verificar que el artista se insertó
    cursor.execute('SELECT * FROM Artist')
    artistas = cursor.fetchall()
    
    # 5. Guardar cambios
    conn.commit()
    
    print(f"\n✅ Base de datos creada con éxito")
    print(f"📁 Ubicación: {DB_PATH}")
    print(f"📋 Tabla 'Artist' creada con 5 campos de género")
    print(f"🎵 Total artistas en BD: {len(artistas)}")
    
    # Mostrar los artistas
    if artistas:
        print("\n📊 ARTISTAS GUARDADOS:")
        for artista in artistas:
            print(f"  - ID: {artista[0]}, Nombre: {artista[1]}")
            print(f"    Géneros: {artista[2]}, {artista[3]}, {artista[4]}, {artista[5]}, {artista[6]}")
    
    conn.close()

if __name__ == "__main__":
    crear_base_datos()
