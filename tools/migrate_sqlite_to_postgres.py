# -*- coding: utf-8 -*-
"""Copia los datos de un archivo SQLite (gullieth.db) a PostgreSQL (Supabase).

Una sola vez, al pasar de SQLite a Supabase:

    set DATABASE_URL=postgresql://usuario:clave@host:6543/postgres
    python tools/migrate_sqlite_to_postgres.py gullieth.db

Conserva los ids (los documentos y pagos apuntan a ellos) y deja las secuencias
de PostgreSQL en el valor correcto para que los registros nuevos no choquen.
Se niega a correr si la base de destino ya tiene usuarios, para no duplicar ni
mezclar datos; con --force sobrescribe lo que haya (borra antes las tablas).
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Orden que respeta las claves foráneas.
TABLES = ('users', 'documents', 'payments', 'settings')
SERIAL_TABLES = ('users', 'documents', 'payments')


def migrate(source_path, destination, force=False):
    """Copia `source_path` (SQLite) a `destination` (db._Conn). Devuelve {tabla: filas}."""
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    counts = {}
    try:
        existing = destination.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n']
        if existing and not force:
            raise SystemExit(f'La base de destino ya tiene {existing} usuario(s). '
                             'Usa --force solo si quieres reemplazarlos.')
        if existing:
            for table in reversed(TABLES):
                destination.execute(f'DELETE FROM {table}')

        for table in TABLES:
            source_columns = [row[1] for row in source.execute(f'PRAGMA table_info({table})')]
            dest_columns = _destination_columns(destination, table)
            columns = [c for c in source_columns if c in dest_columns]    # solo las que existen en ambos
            rows = source.execute(f'SELECT {", ".join(columns)} FROM {table}').fetchall()
            placeholders = ', '.join('?' for _ in columns)
            # init_db() ya siembra algún ajuste en el destino: el valor del origen manda.
            upsert = ' ON CONFLICT (key) DO UPDATE SET value = excluded.value' if table == 'settings' else ''
            for row in rows:
                destination.execute(
                    f'INSERT INTO {table} ({", ".join(columns)}) VALUES ({placeholders}){upsert}', tuple(row))
            counts[table] = len(rows)

        if destination._pg:
            for table in SERIAL_TABLES:
                destination.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f'GREATEST((SELECT COALESCE(MAX(id), 0) FROM {table}), 1), '
                    f'(SELECT COUNT(*) > 0 FROM {table}))')
        destination.commit()
    finally:
        source.close()
    return counts


def _destination_columns(destination, table):
    import db
    return db._columns(destination, table)


def main(argv):
    force = '--force' in argv
    paths = [a for a in argv if not a.startswith('--')]
    if len(paths) != 1:
        raise SystemExit('Uso: python tools/migrate_sqlite_to_postgres.py ruta/al/gullieth.db [--force]')
    if not os.path.isfile(paths[0]):
        raise SystemExit(f'No existe el archivo {paths[0]}')

    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.getcwd(), '.env'))
    import db
    db.DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
    if not db.is_postgres():
        raise SystemExit('Define DATABASE_URL (postgresql://...) en el entorno o en .env.')

    db.init_db()                                  # crea las tablas si faltan
    with db.standalone() as destination:
        counts = migrate(paths[0], destination, force=force)
    for table, n in counts.items():
        print(f'  {table:<10} {n} fila(s) copiadas')
    print('Listo. Verifica en Supabase y recién entonces archiva el archivo SQLite.')


if __name__ == '__main__':
    main(sys.argv[1:])
