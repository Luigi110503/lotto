from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user, login_user
from models import db, User, Lista, Jugada, LimiteNumero, ResultadoSorteo, HistorialRecaudacion
from utils import ApuestaCalculator
import json
from datetime import datetime, timezone

api_bp = Blueprint('api', __name__, url_prefix='/api')

# Endpoint de prueba (para verificar que la API funciona)
@api_bp.route('/ping', methods=['GET'])
def ping():
    return jsonify({'message': 'pong', 'status': 'ok'})

# Endpoint de login (devuelve token o datos de usuario)
@api_bp.route('/login', methods=['POST'])
def api_login():
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password) and user.activo:
        # Aquí más adelante usaremos tokens JWT, por ahora devolvemos datos básicos
        return jsonify({
            'success': True,
            'user': {
                'id': user.id,
                'username': user.username,
                'nombre_completo': user.nombre_completo,
                'role': user.role,
                'autorizado': user.autorizado
            }
        })
    else:
        return jsonify({'success': False, 'message': 'Credenciales inválidas'}), 401

# Endpoint para obtener listas de un listero (por su ID)
@api_bp.route('/listero/<int:listero_id>/listas', methods=['GET'])
@login_required
def api_listas_listero(listero_id):
    if current_user.id != listero_id and current_user.role != 'admin':
        return jsonify({'error': 'No autorizado'}), 403
    listas = Lista.query.filter_by(listero_id=listero_id, activa=True).all()
    return jsonify([{
        'id': l.id,
        'nombre': l.nombre,
        'turno': l.turno,
        'hora_cierre': l.hora_cierre,
        'limite_total': l.limite_total,
        'total_apostado': sum(j.monto_apostado for j in l.jugadas)
    } for l in listas])

# Endpoint para agregar una jugada (vía API)
@api_bp.route('/agregar_jugada', methods=['POST'])
@login_required
def api_agregar_jugada():
    data = request.get_json()
    lista_id = data.get('lista_id')
    nombre_jugador = data.get('nombre_jugador')
    tipo = data.get('tipo')
    numeros_str = data.get('numeros')
    monto = data.get('monto')
    
    lista = Lista.query.get_or_404(lista_id)
    if lista.listero_id != current_user.id and current_user.role != 'admin':
        return jsonify({'error': 'No autorizado'}), 403
    
    if not lista.activa:
        return jsonify({'error': 'Lista cerrada'}), 400
    
    valido, numeros_lista = ApuestaCalculator.validar_numeros(tipo, numeros_str)
    if not valido:
        return jsonify({'error': 'Números inválidos'}), 400
    
    limite_ok, mensaje = ApuestaCalculator.verificar_limites(lista, numeros_lista, monto)
    if not limite_ok:
        return jsonify({'error': mensaje}), 400
    
    jugada = Jugada(
        lista_id=lista_id,
        nombre_jugador=nombre_jugador,
        tipo_apuesta=tipo,
        numeros=json.dumps(numeros_lista),
        monto_apostado=monto,
        monto_premio=0
    )
    db.session.add(jugada)
    
    for num in numeros_lista:
        limite = LimiteNumero.query.filter_by(lista_id=lista_id, numero=num).first()
        if limite:
            limite.monto_actual += monto
    
    db.session.commit()
    return jsonify({'success': True, 'jugada_id': jugada.id})