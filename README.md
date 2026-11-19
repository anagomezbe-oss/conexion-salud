# CONEXIÓN SALUD

Demostrador académico de coordinación operativa entre Punto Salud, EPS y proveedores. El modo local usa Python 3.10+ y SQLite. El modo nube usa Supabase Auth, PostgreSQL, Storage y Realtime, con archivos estáticos y funciones Python en Vercel.

## Iniciar

1. Instala Python 3.10 o superior.
2. Ejecuta `python app.py` desde esta carpeta (en Windows también puedes abrir `iniciar.bat`).
3. Abre [http://127.0.0.1:8000](http://127.0.0.1:8000).

La base `conexion_salud.db` se crea automáticamente al primer inicio con datos ficticios. No contiene información de pacientes. Para volver al conjunto inicial, detén el servidor, elimina `conexion_salud.db` y reinícialo. El servidor escucha únicamente en localhost.

## Cuentas de demostración

Contraseña común: `demo1234`.

| Perfil | Correo | Entidad |
|---|---|---|
| Punto Salud | `salud@conexionsalud.demo` | Punto Salud |
| Proveedor | `proveedor@conexionsalud.demo` | MediSupply |
| EPS | `eps@conexionsalud.demo` | EPS Bienestar |
| Administrador | `admin@conexionsalud.demo` | Conexión Salud |

Los usuarios que crea el administrador reciben contraseña temporal `demo1234`; cámbiala en una implementación real. Las cuentas semilla son exclusivamente para demostración local.

## Recorrido de demostración

1. Ingresa como Punto Salud. Revisa inventarios y pedidos. Crea un pedido con proveedor, medicamento, cantidad y fecha.
2. Cierra sesión e ingresa como proveedor. Confirma el pedido y avanza a preparación. Puedes actualizar existencias y registrar una novedad de retraso, que genera una alerta.
3. Cierra sesión e ingresa otra vez como Punto Salud. Abre el Centro de alertas y consulta la novedad.
4. Como proveedor, actualiza el pedido a Despachado y luego En tránsito.
5. Como Punto Salud, abre el pedido y confirma Entregado. El cambio registra actor y fecha en el historial.
6. Consulta pagos, envía un mensaje institucional e inspecciona los indicadores o exporta los pedidos a CSV.
7. Abre Mensajería en las sesiones de Punto Salud, EPS y proveedor. La sala general es compartida; desde “Chat privado” puedes iniciar una conversación entre dos entidades. Envía un mensaje desde una sesión y comprueba su recepción inmediata, presencia y lectura en las otras sesiones.

Los datos sembrados incluyen stock normal, bajo y agotado; pedidos entregados, pendientes, en tránsito y retrasados; facturas pendientes, vencidas y pagadas.

## Qué incluye

- Inicio/cierre de sesión con cuentas locales y contraseñas derivadas mediante PBKDF2.
- Panel con indicadores calculados desde SQLite, pedidos recientes, alertas y resumen gráfico.
- Inventarios, búsqueda/filtros, actualización de stock y registro de movimientos.
- Creación y seguimiento de pedidos con historial y validación de transiciones.
- Seguimiento de entregas y confirmación de recepción.
- Alertas persistentes, lectura y filtros.
- Seguimiento de facturas y estados administrativos (sin pasarela de pagos).
- Chat grupal común y mensajes privados entre entidades, servidos en vivo por WebSocket y persistidos en SQLite.
- Presencia por sala, indicador de escritura, confirmaciones de lectura, búsqueda, vinculación con pedidos/inventario/alertas y adjuntos de hasta 2 MB.
- Informes operativos calculados al consultar y descarga de CSV.
- Administración de cuentas y activación/desactivación por administrador.
- Interfaz adaptable a escritorio, tablet y móvil.

## Acceso y límites de seguridad

Punto Salud crea pedidos y confirma recepción; proveedores administran sus existencias y actualizan pedidos de su entidad; EPS consulta y conversa; administración tiene acceso global. En modo local, la sesión usa una cookie HttpOnly y los cambios se guardan en SQLite. En modo nube, Supabase Auth identifica al usuario y las políticas RLS controlan el acceso a filas y salas privadas.

El modo local es un prototipo académico: las sesiones viven en memoria y expiran al reiniciar el proceso. El modo nube usa proveedores gestionados para Auth, PostgreSQL y Realtime, pero sigue siendo una demostración con datos ficticios; no ingreses información personal, clínica o financiera real. Antes de un uso clínico o productivo se requiere revisión de seguridad, copias de respaldo y políticas de retención, recuperación de acceso, auditoría y análisis antimalware de adjuntos.

El chat se ejecuta en el mismo proceso local y usa WebSocket nativo, sin sondeos ni proveedor externo. Al reconectar, la interfaz vuelve a conectarse y el historial se recupera de SQLite; los identificadores de cliente únicos evitan duplicar mensajes al reintentar. Archivos pequeños se guardan como datos adjuntos en la base local.

Para ejecutar las pruebas automatizadas: `python -m unittest discover -s tests -v`. Incluyen permisos, pedidos, alertas, pagos, mensajes y una prueba de tres conexiones WebSocket simultáneas (Punto Salud, EPS y proveedor) en la sala común, con presencia, escritura, persistencia, referencia a pedido y deduplicación.

Los datos son demostrativos. El alcance no integra EPS reales, correo, transportistas, facturación electrónica ni bancos; no hay credenciales para esos servicios. Para producción se requeriría una política de retención y análisis antimalware de archivos adjuntos, además del endurecimiento de sesiones y despliegue ya indicado.

## Despliegue en Vercel y Supabase

El proyecto Supabase debe tener aplicado `supabase/schema.sql` desde SQL Editor antes de que los perfiles puedan iniciar sesión. Ese esquema crea las tablas, datos ficticios, políticas RLS, funciones transaccionales y el bucket privado de adjuntos.

En Vercel importa `anagomezbe-oss/conexion-salud` desde la rama `main`. Usa el preset **Other**, sin comando de build y con `.` como directorio de salida. Configura estas variables de entorno en Production y Preview:

| Variable | Uso |
|---|---|
| `SUPABASE_URL` | URL del proyecto Supabase |
| `SUPABASE_PUBLISHABLE_KEY` | Clave pública usada por el cliente web |
| `SUPABASE_SECRET_KEY` | Clave secreta, solo funciones servidor para crear cuentas; nunca se expone al navegador |
| `BOOTSTRAP_ADMIN_SECRET` | Secreto temporal para el primer administrador |

Después del primer despliegue, abre `/bootstrap.html` para crear la primera cuenta administradora. Luego elimina `BOOTSTRAP_ADMIN_SECRET` de Vercel y usa **Gestión de usuarios** para crear las cuentas Punto Salud, EPS y proveedor. No subas contraseñas, claves ni archivos `.env` a GitHub.

La rama pública de GitHub contiene únicamente código y datos ficticios; `conexion_salud.db` está excluida. Antes de usar información real, revisa privacidad, retención, copias de seguridad, autorización de cuentas, claves y el plan de Supabase/Vercel.
