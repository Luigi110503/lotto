import os
import pytz
import json
from datetime import datetime, timezone
from collections import defaultdict
from functools import wraps

from flask import Flask, render_template, request, jsonify, flash, redirect, url_for
from flask_login import LoginManager, login_required, current_user, logout_user
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity, get_jwt
from dotenv import load_dotenv

from models import db, User, Lista, Jugada, LimiteNumero, ResultadoSorteo, HistorialRecaudacion, PremioConfig
from auth import auth_bp, admin_required, listero_required, listero_autorizado_required
from utils import ApuestaCalculator

# Cargar variables de entorno desde .env
load_dotenv()

app = Flask(__name__)

# ========== CONFIGURACIÓN ==========
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'clave-muy-segura-cambiala-en-produccion')
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'jwt-secreto-cambiala')

# Base de datos
database_url = os.environ.get('DATABASE_URL')
if database_url:
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///apuestas.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['WTF_CSRF_ENABLED'] = False

# Zona horaria de Cuba
CUBA_TZ = pytz.timezone('America/Havana')

# ========== EXTENSIONES ==========
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
app.register_blueprint(auth_bp, url_prefix='/auth')

# CORS para permitir conexiones desde app móvil
CORS(app)

# JWT para autenticación de API móvil
jwt = JWTManager(app)

# ========== UTILIDADES DE PLANTILLAS ==========
@app.context_processor
def utility_processor():
    now_utc = datetime.now(timezone.utc)
    now_cuba = now_utc.astimezone(CUBA_TZ)
    return {'now': now_cuba, 'cuba_tz': CUBA_TZ}

@app.template_filter('cuba_time')
def cuba_time_filter(dt):
    if dt is None:
        return ''
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CUBA_TZ).strftime('%d/%m/%Y %H:%M')

# ========== FUNCIONES AUXILIARES ==========
def get_premio_multiplier(tipo):
    config = PremioConfig.query.filter_by(tipo=tipo).first()
    if config:
        return config.multiplicador
    defaults = {'fijo': 70, 'corrido': 70, 'centena': 300, 'parlet': 700}
    return defaults.get(tipo, 0)

def cerrar_lista_individual(lista):
    if not lista.activa:
        return
    jugadas = Jugada.query.filter_by(lista_id=lista.id).all()
    total_apostado = sum(j.monto_apostado for j in jugadas)
    total_premios = sum(j.monto_premio for j in jugadas)
    ganancia_neta = total_apostado - total_premios
    historial = HistorialRecaudacion(
        lista_id=lista.id,
        fecha_cierre=datetime.now(timezone.utc),
        turno=lista.turno,
        total_apostado=total_apostado,
        total_premios_pagados=total_premios,
        ganancia_neta=ganancia_neta
    )
    db.session.add(historial)
    lista.activa = False
    lista.fecha_cierre = datetime.now(timezone.utc)
    limites = LimiteNumero.query.filter_by(lista_id=lista.id).all()
    for lim in limites:
        lim.monto_actual = 0
    db.session.commit()

def cerrar_listas_vencidas():
    now_cuba = datetime.now(CUBA_TZ)
    listas_vencidas = Lista.query.filter(
        Lista.activa == True,
        Lista.hora_cierre != None,
        Lista.hora_cierre < now_cuba.strftime('%H:%M')
    ).all()
    for lista in listas_vencidas:
        cerrar_lista_individual(lista)
    return len(listas_vencidas)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ========== RUTAS DEL ADMIN (TU CÓDIGO ORIGINAL) ==========
@app.route('/')
def index():
    return redirect(url_for('auth.login'))

@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    cerrar_listas_vencidas()
    listas = Lista.query.filter_by(activa=True).all()
    listeros = User.query.filter_by(role='listero').all()
    total_apostado = sum(sum(j.monto_apostado for j in lista.jugadas) for lista in listas)

    listeros_data = []
    for listero in listeros:
        listas_listero = Lista.query.filter_by(listero_id=listero.id, activa=True).all()
        total_listero = sum(sum(j.monto_apostado for j in l.jugadas) for l in listas_listero)
        listeros_data.append({
            'listero': listero,
            'total_recaudado': total_listero,
            'listas_activas': len(listas_listero)
        })
    return render_template('dashboard_admin.html', listas=listas, listeros_data=listeros_data, total_apostado=total_apostado)

@app.route('/admin/crear_listero', methods=['POST'])
@login_required
@admin_required
def crear_listero():
    nombre_completo = request.form.get('nombre_completo')
    username = request.form.get('username')
    password = request.form.get('password')
    if not nombre_completo or not username or not password:
        flash('Todos los campos son obligatorios', 'danger')
        return redirect(url_for('admin_dashboard'))
    if User.query.filter_by(username=username).first():
        flash('El nombre de usuario ya existe', 'danger')
        return redirect(url_for('admin_dashboard'))
    nuevo = User(username=username, nombre_completo=nombre_completo, role='listero', activo=True, autorizado=False)
    nuevo.set_password(password)
    db.session.add(nuevo)
    db.session.commit()
    flash(f'✅ Listero {nombre_completo} creado exitosamente.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/autorizar_listeros')
@login_required
@admin_required
def autorizar_listeros():
    listeros = User.query.filter_by(role='listero').all()
    return render_template('autorizar_listeros.html', listeros=listeros)

@app.route('/admin/cambiar_autorizacion', methods=['POST'])
@login_required
@admin_required
def cambiar_autorizacion():
    listero_id = request.form.get('listero_id')
    accion = request.form.get('accion')
    listero = User.query.get_or_404(listero_id)
    if accion == 'autorizar':
        listero.autorizado = True
        listero.fecha_autorizacion = datetime.now(timezone.utc)
        flash(f'✅ Listero {listero.nombre_completo} autorizado', 'success')
    else:
        listero.autorizado = False
        listero.fecha_autorizacion = None
        flash(f'⚠️ Listero {listero.nombre_completo} desautorizado', 'warning')
    db.session.commit()
    return redirect(url_for('autorizar_listeros'))

@app.route('/admin/cambiar_password/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_cambiar_password(user_id):
    listero = User.query.get_or_404(user_id)
    if listero.role != 'listero':
        flash('Solo se pueden cambiar contraseñas de listeros.', 'danger')
        return redirect(url_for('autorizar_listeros'))
    if request.method == 'POST':
        nueva = request.form.get('nueva_password')
        confirmar = request.form.get('confirmar_password')
        if not nueva or len(nueva) < 6:
            flash('La contraseña debe tener al menos 6 caracteres.', 'danger')
            return redirect(request.url)
        if nueva != confirmar:
            flash('Las contraseñas no coinciden.', 'danger')
            return redirect(request.url)
        listero.set_password(nueva)
        db.session.commit()
        flash(f'Contraseña de {listero.nombre_completo} actualizada.', 'success')
        return redirect(url_for('autorizar_listeros'))
    return render_template('cambiar_password_admin.html', listero=listero)

@app.route('/admin/ver_listas')
@login_required
@admin_required
def ver_listas():
    listas = Lista.query.all()
    return render_template('ver_listas.html', listas=listas)

@app.route('/admin/ver_jugadas/<int:lista_id>')
@login_required
@admin_required
def ver_jugadas(lista_id):
    lista = Lista.query.get_or_404(lista_id)
    jugadas = Jugada.query.filter_by(lista_id=lista_id).all()
    total_apostado = sum(j.monto_apostado for j in jugadas)
    return render_template('ver_jugadas_admin.html', lista=lista, jugadas=jugadas, total_apostado=total_apostado)

@app.route('/admin/limites/<int:lista_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def configurar_limites(lista_id):
    lista = Lista.query.get_or_404(lista_id)
    if request.method == 'POST':
        for i in range(100):
            limite = request.form.get(f'limite_{i}')
            if limite:
                limite_numero = LimiteNumero.query.filter_by(lista_id=lista_id, numero=i).first()
                if not limite_numero:
                    limite_numero = LimiteNumero(lista_id=lista_id, numero=i)
                    db.session.add(limite_numero)
                limite_numero.limite_maximo = float(limite)
        db.session.commit()
        flash('Límites por número configurados.', 'success')
        return redirect(url_for('admin_dashboard'))
    limites = {lim.numero: lim.limite_maximo for lim in LimiteNumero.query.filter_by(lista_id=lista_id).all()}
    return render_template('configurar_limites.html', lista=lista, limites=limites)

@app.route('/admin/editar_limite_total/<int:lista_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_limite_total(lista_id):
    lista = Lista.query.get_or_404(lista_id)
    if request.method == 'POST':
        nuevo = request.form.get('limite_total')
        if nuevo:
            lista.limite_total = float(nuevo)
            db.session.commit()
            flash(f'Límite total de {lista.nombre} actualizado a ${lista.limite_total:,.2f}', 'success')
        else:
            flash('El límite no puede estar vacío', 'danger')
        return redirect(url_for('ver_listas'))
    return render_template('editar_limite_total.html', lista=lista)

@app.route('/admin/configurar_horario/<int:lista_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def configurar_horario(lista_id):
    lista = Lista.query.get_or_404(lista_id)
    if request.method == 'POST':
        hora = request.form.get('hora_cierre')
        lista.hora_cierre = hora
        db.session.commit()
        flash(f'Horario de cierre configurado: {hora}', 'success')
        return redirect(url_for('admin_dashboard'))
    return render_template('configurar_horario.html', lista=lista)

@app.route('/admin/cerrar_lista/<int:lista_id>')
@login_required
@admin_required
def cerrar_lista(lista_id):
    lista = Lista.query.get_or_404(lista_id)
    cerrar_lista_individual(lista)
    flash(f'Lista "{lista.nombre}" cerrada.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/cerrar_vencidas')
@login_required
@admin_required
def cerrar_vencidas():
    cantidad = cerrar_listas_vencidas()
    flash(f'Se cerraron {cantidad} listas vencidas.', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/clear_database', methods=['GET', 'POST'])
@login_required
@admin_required
def clear_database():
    if request.method == 'POST':
        with app.app_context():
            db.drop_all()
            db.create_all()
            crear_usuarios_iniciales()
        flash('✅ Base de datos limpiada y reiniciada correctamente.', 'success')
        return redirect(url_for('admin_dashboard'))
    return render_template('confirmar_clear.html')

@app.route('/admin/cambiar_credenciales', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_cambiar_credenciales():
    if request.method == 'POST':
        nuevo_username = request.form.get('nuevo_username', '').strip()
        password_actual = request.form.get('password_actual')
        nueva_password = request.form.get('nueva_password')
        confirmar = request.form.get('confirmar_password')

        if not current_user.check_password(password_actual):
            flash('Contraseña actual incorrecta.', 'danger')
            return redirect(url_for('admin_cambiar_credenciales'))

        if nuevo_username and nuevo_username != current_user.username:
            if User.query.filter_by(username=nuevo_username).first():
                flash('El nombre de usuario ya existe.', 'danger')
                return redirect(url_for('admin_cambiar_credenciales'))
            current_user.username = nuevo_username
            flash('Nombre de usuario actualizado. Debes volver a iniciar sesión.', 'info')

        if nueva_password:
            if len(nueva_password) < 6:
                flash('La nueva contraseña debe tener al menos 6 caracteres.', 'danger')
                return redirect(url_for('admin_cambiar_credenciales'))
            if nueva_password != confirmar:
                flash('Las contraseñas no coinciden.', 'danger')
                return redirect(url_for('admin_cambiar_credenciales'))
            current_user.set_password(nueva_password)
            flash('Contraseña actualizada.', 'success')

        db.session.commit()

        if nuevo_username and nuevo_username != current_user.username:
            logout_user()
            flash('Credenciales actualizadas. Por favor inicia sesión nuevamente.', 'info')
            return redirect(url_for('auth.login'))

        return redirect(url_for('admin_dashboard'))

    return render_template('admin_cambiar_credenciales.html')

@app.route('/admin/premios', methods=['GET', 'POST'])
@login_required
@admin_required
def configurar_premios():
    if request.method == 'POST':
        for tipo in ['fijo', 'corrido', 'centena', 'parlet']:
            valor = request.form.get(tipo)
            if valor:
                config = PremioConfig.query.filter_by(tipo=tipo).first()
                if not config:
                    config = PremioConfig(tipo=tipo)
                    db.session.add(config)
                config.multiplicador = int(valor)
                config.actualizado_por = current_user.id
                config.fecha_actualizacion = datetime.now(timezone.utc)
        db.session.commit()
        flash('Multiplicadores de premios actualizados.', 'success')
        return redirect(url_for('configurar_premios'))
    configs = {c.tipo: c.multiplicador for c in PremioConfig.query.all()}
    return render_template('configurar_premios.html', configs=configs)

@app.route('/admin/resultados', methods=['GET', 'POST'])
@login_required
@admin_required
def administrar_resultados():
    if request.method == 'POST':
        resultado_str = request.form.get('resultado')
        turno = request.form.get('turno')
        fecha_sorteo = request.form.get('fecha_sorteo')
        fecha_obj = datetime.strptime(fecha_sorteo, '%Y-%m-%d').date()
        numeros, error = ApuestaCalculator.parsear_resultado(resultado_str)
        if error:
            flash(error, 'danger')
            return redirect(url_for('administrar_resultados'))
        resultado_formateado = f"{numeros['centena']}{numeros['fijo']:02d} {numeros['corridos'][0]} {numeros['corridos'][1]}"
        nuevo = ResultadoSorteo(
            fecha=fecha_obj,
            turno=turno,
            centena=numeros['centena'],
            fijo=numeros['fijo'],
            corrido1=numeros['corridos'][0],
            corrido2=numeros['corridos'][1],
            resultado_formateado=resultado_formateado
        )
        db.session.add(nuevo)
        db.session.commit()

        listas_abiertas = Lista.query.filter_by(turno=turno, activa=True).all()
        for lista in listas_abiertas:
            cerrar_lista_individual(lista)

        flash(f'Resultado {turno} guardado. Se cerraron {len(listas_abiertas)} listas.', 'success')
        return redirect(url_for('calcular_premios_generales', resultado=resultado_str.replace(' ', '_'), turno=turno))
    return render_template('resultados.html')

@app.route('/admin/historial_resultados')
@login_required
@admin_required
def historial_resultados():
    resultados = ResultadoSorteo.query.order_by(ResultadoSorteo.fecha.desc(), ResultadoSorteo.turno.desc()).all()
    return render_template('historial_resultados.html', resultados=resultados)

@app.route('/admin/historial_recaudacion')
@login_required
@admin_required
def historial_recaudacion():
    historial = HistorialRecaudacion.query.order_by(HistorialRecaudacion.fecha_cierre.desc()).all()
    total_apostado_acum = sum(h.total_apostado for h in historial)
    total_premios_acum = sum(h.total_premios_pagados for h in historial)
    ganancia_neta_acum = total_apostado_acum - total_premios_acum
    return render_template('historial_recaudacion.html',
                           historial=historial,
                           total_apostado_acum=total_apostado_acum,
                           total_premios_acum=total_premios_acum,
                           ganancia_neta_acum=ganancia_neta_acum)

@app.route('/admin/reportes')
@login_required
@admin_required
def reportes():
    tipo = request.args.get('tipo', 'semana')
    historial = HistorialRecaudacion.query.order_by(HistorialRecaudacion.fecha_cierre).all()
    grupos = defaultdict(lambda: {'total_apostado': 0, 'total_premios': 0})
    for h in historial:
        fecha = h.fecha_cierre.astimezone(CUBA_TZ)
        if tipo == 'semana':
            clave = fecha.strftime('%Y-%W')
            nombre = f"Semana {clave}"
        elif tipo == 'mes':
            clave = fecha.strftime('%Y-%m')
            nombre = fecha.strftime('%B %Y')
        else:
            clave = fecha.strftime('%Y')
            nombre = f"Año {clave}"
        grupos[clave]['nombre'] = nombre
        grupos[clave]['total_apostado'] += h.total_apostado
        grupos[clave]['total_premios'] += h.total_premios_pagados
        grupos[clave]['ganancia_neta'] = grupos[clave]['total_apostado'] - grupos[clave]['total_premios']

    reportes_data = [{'periodo': v['nombre'], 'total_apostado': v['total_apostado'],
                      'total_premios': v['total_premios'], 'ganancia_neta': v['ganancia_neta']}
                     for k, v in sorted(grupos.items(), reverse=True)]
    return render_template('reportes.html', reportes=reportes_data, tipo=tipo)

@app.route('/admin/calcular_premios/<resultado>/<turno>')
@login_required
@admin_required
def calcular_premios_generales(resultado, turno):
    resultado_str = resultado.replace('_', ' ')
    jugadas = Jugada.query.all()
    calculo = ApuestaCalculator.calcular_premios_por_resultado(resultado_str, jugadas, get_premio_multiplier)
    if 'error' in calculo:
        flash(calculo['error'], 'danger')
        return redirect(url_for('administrar_resultados'))

    for tipo in ['centena', 'fijo', 'corrido', 'parlet']:
        for p in calculo['detalle'][tipo]:
            jugada = Jugada.query.get(p['jugada_id'])
            if jugada:
                jugada.monto_premio = p['premio']
    db.session.commit()

    numeros = calculo['numeros_ganadores']
    resultado_formateado = f"{numeros['centena']}{numeros['fijo']:02d} {numeros['corridos'][0]} {numeros['corridos'][1]}"
    return render_template('resultado_premios.html', resultado=resultado_formateado, turno=turno,
                           numeros_ganadores=numeros, calculo=calculo)

# ========== RUTAS DEL LISTERO ==========
@app.route('/listero/dashboard')
@login_required
@listero_required
def listero_dashboard():
    listas = Lista.query.filter_by(listero_id=current_user.id, activa=True).all()
    autorizado = current_user.autorizado
    return render_template('dashboard_listero.html', listas=listas, autorizado=autorizado)

@app.route('/listero/crear_lista', methods=['GET', 'POST'])
@login_required
@listero_autorizado_required
def crear_lista():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        turno = request.form.get('turno')
        limite_total = float(request.form.get('limite_total', 0))
        nueva = Lista(
            nombre=nombre,
            listero_id=current_user.id,
            limite_total=limite_total,
            turno=turno
        )
        db.session.add(nueva)
        db.session.commit()
        flash(f'Lista {nombre} creada para turno {turno}', 'success')
        return redirect(url_for('listero_dashboard'))
    return render_template('crear_lista.html')

@app.route('/listero/agregar_jugada/<int:lista_id>', methods=['GET', 'POST'])
@login_required
@listero_required
def agregar_jugada(lista_id):
    lista = Lista.query.get_or_404(lista_id)
    if lista.listero_id != current_user.id:
        flash('No tienes permiso', 'danger')
        return redirect(url_for('listero_dashboard'))
    if not lista.activa:
        flash('Lista cerrada', 'warning')
        return redirect(url_for('listero_dashboard'))
    if lista.hora_cierre:
        hora_actual = datetime.now(CUBA_TZ).strftime('%H:%M')
        if hora_actual > lista.hora_cierre:
            flash(f'Lista cerró a las {lista.hora_cierre}', 'warning')
            return redirect(url_for('listero_dashboard'))
    if request.method == 'POST':
        nombre_jugador = request.form.get('nombre_jugador')
        tipo = request.form.get('tipo')
        numeros_str = request.form.get('numeros')
        monto = float(request.form.get('monto'))
        valido, numeros_lista = ApuestaCalculator.validar_numeros(tipo, numeros_str)
        if not valido:
            flash('Números inválidos', 'danger')
            return redirect(request.url)
        limite_ok, mensaje = ApuestaCalculator.verificar_limites(lista, numeros_lista, monto)
        if not limite_ok:
            flash(mensaje, 'danger')
            return redirect(request.url)
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
        flash('✅ Jugada agregada', 'success')
        return redirect(url_for('listero_dashboard'))
    return render_template('agregar_jugada.html', lista=lista)

@app.route('/listero/mis_jugadas/<int:lista_id>')
@login_required
@listero_required
def mis_jugadas(lista_id):
    lista = Lista.query.get_or_404(lista_id)
    if lista.listero_id != current_user.id:
        flash('No tienes permiso', 'danger')
        return redirect(url_for('listero_dashboard'))
    jugadas = Jugada.query.filter_by(lista_id=lista_id).all()
    total_apostado = sum(j.monto_apostado for j in jugadas)
    return render_template('ver_jugadas.html', lista=lista, jugadas=jugadas, total_apostado=total_apostado)

@app.route('/listero/cambiar_password', methods=['GET', 'POST'])
@login_required
@listero_required
def listero_cambiar_password():
    if request.method == 'POST':
        actual = request.form.get('password_actual')
        nueva = request.form.get('nueva_password')
        confirmar = request.form.get('confirmar_password')
        if not current_user.check_password(actual):
            flash('Contraseña actual incorrecta.', 'danger')
            return redirect(request.url)
        if not nueva or len(nueva) < 6:
            flash('La nueva contraseña debe tener al menos 6 caracteres.', 'danger')
            return redirect(request.url)
        if nueva != confirmar:
            flash('Las contraseñas no coinciden.', 'danger')
            return redirect(request.url)
        current_user.set_password(nueva)
        db.session.commit()
        flash('Contraseña actualizada.', 'success')
        return redirect(url_for('listero_dashboard'))
    return render_template('cambiar_password_listero.html')

# ========== API DE SINCRONIZACIÓN OFFLINE ==========
@app.route('/api/sincronizar', methods=['POST'])
@login_required
def sincronizar():
    try:
        data = request.get_json()
        jugadas_offline = data.get('jugadas', [])
        for jugada_data in jugadas_offline:
            jugada = Jugada(
                lista_id=jugada_data['lista_id'],
                nombre_jugador=jugada_data['nombre_jugador'],
                tipo_apuesta=jugada_data['tipo'],
                numeros=json.dumps(jugada_data['numeros']),
                monto_apostado=jugada_data['monto'],
                monto_premio=0,
                sincronizada=True
            )
            db.session.add(jugada)
        db.session.commit()
        return jsonify({'status': 'success', 'sincronizadas': len(jugadas_offline)})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# =============================================================================
# ========== 📱 NUEVOS ENDPOINTS PARA APP MÓVIL (API REST + JWT) ==========
# =============================================================================

@app.route('/api/mobile/login', methods=['POST'])
def mobile_login():
    """
    Login para app móvil: devuelve token JWT.
    JSON esperado: {"username": "...", "password": "..."}
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON requerido'}), 400

    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'error': 'Username y password son requeridos'}), 400

    user = User.query.filter_by(username=username).first()

    if user and user.check_password(password) and user.activo:
        additional_claims = {
            "role": user.role,
            "listero_id": user.id if user.role == 'listero' else None,
            "nombre": user.nombre_completo,
            "autorizado": user.autorizado if user.role == 'listero' else None
        }
        access_token = create_access_token(identity=user.id, additional_claims=additional_claims)
        return jsonify({
            'access_token': access_token,
            'token_type': 'Bearer',
            'role': user.role,
            'nombre': user.nombre_completo,
            'autorizado': user.autorizado if user.role == 'listero' else None
        }), 200

    return jsonify({'error': 'Credenciales inválidas o usuario inactivo'}), 401


@app.route('/api/mobile/listas', methods=['GET'])
@jwt_required()
def mobile_get_listas():
    """
    Obtiene listas activas según el rol del usuario.
    Headers: Authorization: Bearer <token>
    """
    current_user_id = get_jwt_identity()
    claims = get_jwt()
    role = claims.get('role')

    if role == 'admin':
        listas = Lista.query.filter_by(activa=True).all()
    elif role == 'listero':
        listero_id = claims.get('listero_id')
        if not listero_id:
            return jsonify({'error': 'No se pudo identificar al listero'}), 400
        listas = Lista.query.filter_by(listero_id=listero_id, activa=True).all()
    else:
        return jsonify({'error': 'Rol no autorizado'}), 403

    resultado = []
    for lista in listas:
        total_apostado = sum(j.monto_apostado for j in lista.jugadas)
        resultado.append({
            'id': lista.id,
            'nombre': lista.nombre,
            'turno': lista.turno,
            'hora_cierre': lista.hora_cierre,
            'limite_total': lista.limite_total,
            'total_apostado': total_apostado,
            'activa': lista.activa
        })

    return jsonify(resultado), 200


@app.route('/api/mobile/jugada', methods=['POST'])
@jwt_required()
def mobile_agregar_jugada():
    """
    Agrega una jugada desde la app móvil.
    JSON esperado: {
        "lista_id": 1,
        "nombre_jugador": "Juan",
        "tipo": "fijo",
        "numeros": "12",
        "monto": 10.00
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'JSON requerido'}), 400

    claims = get_jwt()

    # Solo listeros pueden agregar jugadas
    if claims.get('role') != 'listero':
        return jsonify({'error': 'Solo listeros pueden agregar jugadas'}), 403

    lista_id = data.get('lista_id')
    lista = Lista.query.get(lista_id)

    if not lista:
        return jsonify({'error': 'Lista no encontrada'}), 404

    if lista.listero_id != claims.get('listero_id'):
        return jsonify({'error': 'No tienes permiso para esta lista'}), 403

    if not lista.activa:
        return jsonify({'error': 'Esta lista está cerrada'}), 400

    # Validar hora de cierre
    if lista.hora_cierre:
        hora_actual = datetime.now(CUBA_TZ).strftime('%H:%M')
        if hora_actual > lista.hora_cierre:
            return jsonify({'error': f'Lista cerró a las {lista.hora_cierre}'}), 400

    tipo = data.get('tipo')
    numeros_str = data.get('numeros')
    monto = data.get('monto')
    nombre_jugador = data.get('nombre_jugador')

    if not all([tipo, numeros_str, monto, nombre_jugador]):
        return jsonify({'error': 'Todos los campos son requeridos'}), 400

    try:
        monto = float(monto)
    except (ValueError, TypeError):
        return jsonify({'error': 'Monto debe ser un número válido'}), 400

    # Validar números con tu lógica existente
    valido, numeros_lista = ApuestaCalculator.validar_numeros(tipo, numeros_str)
    if not valido:
        return jsonify({'error': 'Números inválidos para este tipo de apuesta'}), 400

    # Verificar límites
    limite_ok, mensaje = ApuestaCalculator.verificar_limites(lista, numeros_lista, monto)
    if not limite_ok:
        return jsonify({'error': mensaje}), 400

    # Crear jugada
    jugada = Jugada(
        lista_id=lista_id,
        nombre_jugador=nombre_jugador,
        tipo_apuesta=tipo,
        numeros=json.dumps(numeros_lista),
        monto_apostado=monto,
        monto_premio=0
        # No especificamos fecha_apuesta, se asignará automáticamente con default=datetime.utcnow
    )
    db.session.add(jugada)

    # Actualizar límites por número
    for num in numeros_lista:
        limite = LimiteNumero.query.filter_by(lista_id=lista_id, numero=num).first()
        if limite:
            limite.monto_actual += monto

    db.session.commit()

    return jsonify({
        'status': 'success',
        'jugada_id': jugada.id,
        'mensaje': 'Jugada registrada correctamente'
    }), 201


@app.route('/api/mobile/jugadas/<int:lista_id>', methods=['GET'])
@jwt_required()
def mobile_get_jugadas(lista_id):
    """
    Obtiene todas las jugadas de una lista.
    Headers: Authorization: Bearer <token>
    """
    claims = get_jwt()
    lista = Lista.query.get_or_404(lista_id)

    # Validar permisos
    if claims.get('role') == 'admin':
        pass  # Admin ve todo
    elif claims.get('role') == 'listero' and lista.listero_id == claims.get('listero_id'):
        pass  # Listero ve sus listas
    else:
        return jsonify({'error': 'No autorizado'}), 403

    jugadas = Jugada.query.filter_by(lista_id=lista_id).all()
    resultado = []

    for j in jugadas:
        try:
            numeros = json.loads(j.numeros) if j.numeros else []
        except:
            numeros = []

        resultado.append({
            'id': j.id,
            'jugador': j.nombre_jugador,
            'tipo': j.tipo_apuesta,
            'numeros': numeros,
            'monto': j.monto_apostado,
            'premio': j.monto_premio,
            'fecha': j.fecha_apuesta.isoformat() if hasattr(j, 'fecha_apuesta') and j.fecha_apuesta else None
        })

    return jsonify(resultado), 200


@app.route('/api/mobile/health', methods=['GET'])
def health_check():
    """Endpoint simple para verificar que la API está funcionando."""
    return jsonify({'status': 'ok', 'message': 'API móvil activa'}), 200


# =============================================================================
# ========== INICIALIZACIÓN DE LA BASE DE DATOS ==========
# =============================================================================

def crear_usuarios_iniciales():
    """Crea usuario administrador y listeros de prueba si no existen."""
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(username='admin', nombre_completo='Administrador', role='admin', activo=True, autorizado=True)
        admin.set_password('admin123')
        db.session.add(admin)

    for u in [('listero1', 'Juan Pérez', True), ('listero2', 'María García', True), ('listero3', 'Carlos López', False)]:
        if not User.query.filter_by(username=u[0]).first():
            nuevo = User(username=u[0], nombre_completo=u[1], role='listero', activo=True, autorizado=u[2])
            if u[2]:
                nuevo.fecha_autorizacion = datetime.now(timezone.utc)
            nuevo.set_password('listero123')
            db.session.add(nuevo)

    db.session.commit()

    defaults = {'fijo': 70, 'corrido': 70, 'centena': 300, 'parlet': 700}
    for tipo, mult in defaults.items():
        if not PremioConfig.query.filter_by(tipo=tipo).first():
            config = PremioConfig(tipo=tipo, multiplicador=mult, actualizado_por=1 if admin else None)
            db.session.add(config)
    db.session.commit()


# Crear tablas y usuarios iniciales al iniciar la app
with app.app_context():
    db.create_all()
    crear_usuarios_iniciales()


if __name__ == '__main__':
    # En desarrollo local
    app.run(debug=True, host='0.0.0.0', port=5000)