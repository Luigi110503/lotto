from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    nombre_completo = db.Column(db.String(100), nullable=False)
    fecha_registro = db.Column(db.DateTime, default=datetime.utcnow)
    activo = db.Column(db.Boolean, default=True)
    autorizado = db.Column(db.Boolean, default=False)
    fecha_autorizacion = db.Column(db.DateTime, nullable=True)
    listas = db.relationship('Lista', backref='listero', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Lista(db.Model):
    __tablename__ = 'listas'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    listero_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    fecha_cierre = db.Column(db.DateTime, nullable=True)
    hora_cierre = db.Column(db.String(10), nullable=True)
    turno = db.Column(db.String(10), nullable=True)          # 'mediodia' o 'noche'
    activa = db.Column(db.Boolean, default=True)
    limite_total = db.Column(db.Float, default=0)
    jugadas = db.relationship('Jugada', backref='lista', lazy=True)

class Jugada(db.Model):
    __tablename__ = 'jugadas'
    id = db.Column(db.Integer, primary_key=True)
    lista_id = db.Column(db.Integer, db.ForeignKey('listas.id'), nullable=False)
    nombre_jugador = db.Column(db.String(100), nullable=False)
    tipo_apuesta = db.Column(db.String(20), nullable=False)
    numeros = db.Column(db.String(200), nullable=False)
    monto_apostado = db.Column(db.Float, nullable=False)
    monto_premio = db.Column(db.Float, default=0)
    fecha_apuesta = db.Column(db.DateTime, default=datetime.utcnow)
    sincronizada = db.Column(db.Boolean, default=False)

class LimiteNumero(db.Model):
    __tablename__ = 'limites_numeros'
    id = db.Column(db.Integer, primary_key=True)
    lista_id = db.Column(db.Integer, db.ForeignKey('listas.id'), nullable=False)
    numero = db.Column(db.Integer, nullable=False)
    limite_maximo = db.Column(db.Float, default=0)
    monto_actual = db.Column(db.Float, default=0)

class ResultadoSorteo(db.Model):
    __tablename__ = 'resultados_sorteo'
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, nullable=False)
    turno = db.Column(db.String(10), nullable=False)
    centena = db.Column(db.Integer, nullable=False)
    fijo = db.Column(db.Integer, nullable=False)
    corrido1 = db.Column(db.Integer, nullable=False)
    corrido2 = db.Column(db.Integer, nullable=False)
    resultado_formateado = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class HistorialRecaudacion(db.Model):
    __tablename__ = 'historial_recaudacion'
    id = db.Column(db.Integer, primary_key=True)
    lista_id = db.Column(db.Integer, db.ForeignKey('listas.id'), nullable=False)
    fecha_cierre = db.Column(db.DateTime, nullable=False)
    turno = db.Column(db.String(10), nullable=False)
    total_apostado = db.Column(db.Float, nullable=False)
    total_premios_pagados = db.Column(db.Float, nullable=False)
    ganancia_neta = db.Column(db.Float, nullable=False)
    lista = db.relationship('Lista', backref='historial', lazy=True)

class PremioConfig(db.Model):
    """Configuración de premios (multiplicadores)"""
    __tablename__ = 'premios_config'
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(20), unique=True, nullable=False)  # 'fijo', 'corrido', 'centena', 'parlet'
    multiplicador = db.Column(db.Integer, nullable=False)
    actualizado_por = db.Column(db.Integer, db.ForeignKey('users.id'))
    fecha_actualizacion = db.Column(db.DateTime, default=datetime.utcnow)