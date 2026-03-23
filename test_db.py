from app import app, db
from models import User
import sys

print("Probando conexión a Neon...")

with app.app_context():
    try:
        db.create_all()
        print("✅ Conexión exitosa. Las tablas se han creado (o ya existían).")
    except Exception as e:
        print("❌ Error de conexión:")
        print(e)
        sys.exit(1)
