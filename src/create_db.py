import sqlite3
import os
import requests
import time
from datetime import datetime

# ====================
# CONFIGURACIÓN
# ====================
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'my_scrobbles.db')

# Tus credenciales de Last.fm
API_KEY = "a725e74db931ed577bd86effc664cxxx"
USERNAME = "ActsOfNoise"

# ====================
# FUNCIONES DE BASE DE DATOS
# ====================
def get_connection():
    """Obtiene una conexión a la base de datos"""
    return sqlite3.connect(DB_PATH)

def artista_ya_existe(nombre):
    """Verifica si un artista ya está en la BD"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM Artist WHERE name = ?', (nombre,))
    existe = cursor.fetchone()[0] > 0
    conn.close()
    return existe

def insertar_artista(nombre, generos):
    """Inserta un artista con sus 5 géneros"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Asegurar que tenemos 5 géneros (rellenar con None)
    while len(generos) < 5:
        generos.append(None)
    
    cursor.execute('''
        INSERT OR IGNORE INTO Artist (name, genre, genre_2, genre_3, genre_4, genre_5)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (nombre, generos[0], generos[1], generos[2], generos[3], generos[4]))
    
    conn.commit()
    conn.close()

def contar_artistas():
    """Cuenta cuántos artistas hay en la BD"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM Artist')
    total = cursor.fetchone()[0]
    conn.close()
    return total

# ====================
# FUNCIONES DE API DE LAST.FM
# ====================
def obtener_top_artistas(usuario, api_key, limite=50):
    """Obtiene los top artistas de un usuario desde Last.fm"""
    url = "http://ws.audioscrobbler.com/2.0/"
    params = {
        "method": "user.gettopartists",
        "user": usuario,
        "api_key": api_key,
        "format": "json",
        "limit": limite,
        "period": "overall"  # Todos los tiempos
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if "topartists" in data and "artist" in data["topartists"]:
            return data["topartists"]["artist"]
        else:
            print("❌ Error al obtener artistas")
            return []
    except Exception as e:
        print(f"❌ Error en la API: {e}")
        return []

def obtener_generos_artista(artista, api_key):
    """Obtiene los 5 géneros principales de un artista desde Last.fm"""
    try:
        url = "http://ws.audioscrobbler.com/2.0/"
        params = {
            "method": "artist.gettoptags",
            "artist": artista,
            "api_key": api_key,
            "format": "json"
        }
        
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        
        if 'toptags' in data and 'tag' in data['toptags']:
            tags = data['toptags']['tag']
            generos = []
            for tag in tags[:5]:
                nombre = tag['name'].strip()
                if nombre and len(nombre) > 1:
                    generos.append(nombre)
            
            while len(generos) < 5:
                generos.append(None)
            
            return generos
        return [None] * 5
    except Exception as e:
        print(f"⚠️ Error obteniendo géneros para {artista}: {e}")
        return [None] * 5

# ====================
# SCRIPT PRINCIPAL
# ====================
def descargar_artistas():
    """Descarga los top artistas de Last.fm y los guarda en la BD"""
    
    print("🚀 Iniciando descarga de artistas...")
    print(f"👤 Usuario: {USERNAME}")
    print("=" * 50)
    
    # 1. Verificar que la base de datos existe
    if not os.path.exists(DB_PATH):
        print("❌ La base de datos no existe. Ejecuta primero create_db.py")
        return
    
    # 2. Obtener top artistas
    print("📡 Obteniendo top artistas de Last.fm...")
    artistas = obtener_top_artistas(USERNAME, API_KEY, limite=20)
    
    if not artistas:
        print("❌ No se obtuvieron artistas")
        return
    
    print(f"📊 Encontrados {len(artistas)} artistas en tu top")
    print("=" * 50)
    
    # 3. Procesar cada artista
    nuevos_artistas = 0
    ya_existentes = 0
    
    for idx, artista in enumerate(artistas, 1):
        nombre = artista["name"]
        scrobbles = artista["playcount"]
        
        print(f"{idx:2d}. {nombre} ({scrobbles} scrobbles)")
        
        # Verificar si ya existe
        if artista_ya_existe(nombre):
            print(f"    ⏭️  Ya existe en la BD")
            ya_existentes += 1
            continue
        
        # Obtener géneros
        print(f"    🏷️  Obteniendo géneros...")
        generos = obtener_generos_artista(nombre, API_KEY)
        
        # Mostrar géneros
        generos_str = ", ".join([g for g in generos if g])
        print(f"    📋 Géneros: {generos_str if generos_str else 'Sin géneros'}")
        
        # Guardar en la BD
        insertar_artista(nombre, generos)
        nuevos_artistas += 1
        print(f"    ✅ Guardado en la BD")
        
        # Pequeña pausa para respetar límites de API
        time.sleep(0.3)
        print()
    
    # 4. Resumen final
    total = contar_artistas()
    print("=" * 50)
    print("📊 RESUMEN FINAL")
    print(f"  Artistas nuevos guardados: {nuevos_artistas}")
    print(f"  Artistas ya existentes: {ya_existentes}")
    print(f"  Total artistas en BD: {total}")
    print("=" * 50)

if __name__ == "__main__":
    descargar_artistas()
