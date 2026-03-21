// static/offline.js
const DB_NAME = 'app_apuestas_db';
const DB_VERSION = 1;
const STORE_NAME = 'jugadas_offline';

let dbPromise;

function openDB() {
  if (dbPromise) return dbPromise;
  dbPromise = idb.openDB(DB_NAME, DB_VERSION, {
    upgrade(db) {
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { autoIncrement: true });
      }
    }
  });
  return dbPromise;
}

async function guardarJugadaOffline(jugada) {
  const db = await openDB();
  await db.add(STORE_NAME, jugada);
  mostrarEstadoOffline();
}

async function obtenerJugadasOffline() {
  const db = await openDB();
  return await db.getAll(STORE_NAME);
}

async function eliminarTodasJugadasOffline() {
  const db = await openDB();
  const tx = db.transaction(STORE_NAME, 'readwrite');
  await tx.objectStore(STORE_NAME).clear();
  await tx.done;
  mostrarEstadoOffline();
}

async function sincronizarConServidor() {
  const jugadas = await obtenerJugadasOffline();
  if (jugadas.length === 0) return;
  try {
    const response = await fetch('/api/sincronizar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jugadas: jugadas })
    });
    if (response.ok) {
      await eliminarTodasJugadasOffline();
      console.log('Sincronización completada');
    } else {
      console.error('Error en sincronización:', response.status);
    }
  } catch (err) {
    console.error('Sin conexión, se reintentará más tarde', err);
  }
}

function mostrarEstadoOffline() {
  const badge = document.getElementById('offline-badge');
  if (!badge) return;
  obtenerJugadasOffline().then(pendientes => {
    if (pendientes.length > 0) {
      badge.style.display = 'inline-block';
      badge.textContent = `📱 ${pendientes.length} pendiente(s)`;
    } else {
      badge.style.display = 'none';
    }
  });
}

// Eventos de conexión
window.addEventListener('online', () => {
  console.log('Conexión recuperada, sincronizando...');
  sincronizarConServidor();
});
window.addEventListener('offline', () => {
  console.log('App offline');
});

// Sincronizar al cargar si hay conexión
if (navigator.onLine) {
  sincronizarConServidor();
}