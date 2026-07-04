import sqlite3
import os

# Ruta de la base de datos
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'my_scrobbles.db')

def crear_base_datos():
    """Crea la base de datos y la tabla Artist"""
    
    # Crear la carpeta data si no existe
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    # Conectar a la base de datos (se crea automáticamente)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Crear tabla Artist (con 5 géneros fijos)
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
    
    # Crear índice para búsquedas rápidas
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_artist_name ON Artist (name)')
    
    # Guardar cambios
    conn.commit()
    conn.close()
    
    print("✅ Base de datos creada con éxito")
    print(f"📁 Ubicación: {DB_PATH}")
    print("📋 Tabla 'Artist' creada con 5 campos de género")

if __name__ == "__main__":
    crear_base_datos()
