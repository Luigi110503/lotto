import json
from models import Jugada, Lista, LimiteNumero

class ApuestaCalculator:

    @staticmethod
    def calcular_premio_individual(tipo, monto, numeros_ganadores, get_multiplier):
        multiplier = get_multiplier(tipo)
        return monto * multiplier

    @staticmethod
    def validar_numeros(tipo, numeros_str):
        try:
            if tipo == 'fijo':
                num = int(numeros_str)
                return 0 <= num <= 99, [num]
            elif tipo == 'corrido':
                num = int(numeros_str)
                return 0 <= num <= 99, [num]
            elif tipo == 'parlet':
                numeros = [int(n.strip()) for n in numeros_str.split(',')]
                if len(numeros) >= 2 and all(0 <= n <= 99 for n in numeros):
                    return True, numeros
                return False, []
            elif tipo == 'centena':
                num = int(numeros_str)
                return 0 <= num <= 9, [num]
        except:
            return False, []
        return False, []

    @staticmethod
    def verificar_limites(lista, numeros, monto):
        total_apostado = sum(j.monto_apostado for j in lista.jugadas)
        if lista.limite_total > 0 and total_apostado + monto > lista.limite_total:
            return False, f"Límite total de la lista excedido. Máximo: ${lista.limite_total}"

        for num in numeros:
            limite = LimiteNumero.query.filter_by(
                lista_id=lista.id, numero=num
            ).first()
            if limite and limite.limite_maximo > 0:
                if limite.monto_actual + monto > limite.limite_maximo:
                    return False, f"Número {num} excedió su límite. Máximo: ${limite.limite_maximo}"
        return True, "OK"

    @staticmethod
    def parsear_resultado(resultado_str):
        partes = resultado_str.strip().split()
        if len(partes) == 3 and len(partes[0]) == 3:
            centena = int(partes[0][0])
            fijo = int(partes[0][1:])
            corrido1 = int(partes[1])
            corrido2 = int(partes[2])
        elif len(partes) == 4:
            centena = int(partes[0])
            fijo = int(partes[1])
            corrido1 = int(partes[2])
            corrido2 = int(partes[3])
        else:
            return None, "Formato inválido. Use: 234 45 56 o 2 34 45 56"
        return {
            'centena': centena,
            'fijo': fijo,
            'corridos': [corrido1, corrido2],
            'todos_numeros': [fijo, corrido1, corrido2]
        }, None

    @staticmethod
    def calcular_premios_por_resultado(resultado_str, todas_jugadas, get_multiplier):
        numeros_ganadores, error = ApuestaCalculator.parsear_resultado(resultado_str)
        if error:
            return {'error': error}

        premios = {'centena': [], 'fijo': [], 'corrido': [], 'parlet': []}
        total_premios = 0

        for jugada in todas_jugadas:
            numeros_apostados = json.loads(jugada.numeros)
            monto = jugada.monto_apostado
            premio = 0
            tipo_premio = None

            if jugada.tipo_apuesta == 'centena' and numeros_apostados[0] == numeros_ganadores['centena']:
                premio = monto * get_multiplier('centena')
                tipo_premio = 'centena'
            elif jugada.tipo_apuesta == 'fijo' and numeros_apostados[0] == numeros_ganadores['fijo']:
                premio = monto * get_multiplier('fijo')
                tipo_premio = 'fijo'
            elif jugada.tipo_apuesta == 'corrido' and numeros_apostados[0] in numeros_ganadores['corridos']:
                premio = monto * get_multiplier('corrido')
                tipo_premio = 'corrido'
            elif jugada.tipo_apuesta == 'parlet':
                aciertos = [n for n in numeros_apostados if n in numeros_ganadores['todos_numeros']]
                if len(aciertos) >= 2:
                    premio = monto * get_multiplier('parlet')
                    tipo_premio = 'parlet'

            if premio > 0:
                premios[tipo_premio].append({
                    'jugada_id': jugada.id,
                    'nombre_jugador': jugada.nombre_jugador,
                    'numeros': jugada.numeros,
                    'monto': monto,
                    'premio': premio,
                    'lista': jugada.lista.nombre,
                    'listero': jugada.lista.listero.nombre_completo,
                    'lista_id': jugada.lista_id
                })
                total_premios += premio

        return {
            'total_premios': total_premios,
            'detalle': premios,
            'numeros_ganadores': numeros_ganadores
        }

    @staticmethod
    def calcular_premios_por_lista(resultado_str, lista_id, get_multiplier):
        from models import Jugada
        jugadas = Jugada.query.filter_by(lista_id=lista_id).all()
        return ApuestaCalculator.calcular_premios_por_resultado(resultado_str, jugadas, get_multiplier)