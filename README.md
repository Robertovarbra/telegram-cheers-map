# Telegram Cheers Map

Bot de Telegram que permite a usuarios de un grupo enviar video notes y ubicaciones para mostrarlos en un mapa interactivo.

## Stack

- Python 3.9+, `python-telegram-bot` v21+, `aiohttp` v3.11+
- SQLite (`videos.db`)
- Frontend: Leaflet.js + OpenStreetMap
- CD (Raspberry Pi): systemd + Cloudflare Tunnel

## Recomendaciones pendientes

### 🔴 Críticas (antes de desplegar)

1. **Backup automático de la DB** — ✅ Script listo (`scripts/backup.sh`). rclone configurado + cron en el Pi.
2. **BOT_USERNAME sin validación** — ✅ `config.py:24`: warning si no está seteado. Sin este valor, los Mini App deep links (`t.me/{bot}/map?startapp=...`) se rompen.
3. **`aiohttp.ClientSession` no reutilizado** — `web_handlers.py:41`: se crea un `ClientSession` nuevo por cada proxy request. Debería reusarse una sesión guardada en `web_app` (como se hace con `bot`).
4. **Timeout en proxy de video** — ✅ `web_handlers.py:42`: `ClientTimeout(sock_read=10)`.
5. **XSS en `topEmoji` del grupo marker** — ✅ Todos los valores dinámicos en `web/index.html` usan `esc()`.
6. **Caché para Nominatim (OpenStreetMap)** — ✅ Caché LRU en memoria (max 500 entradas) + `asyncio.Lock` + `sleep(1)` entre requests en `telegram_handlers.py:226-255`.
7. **Graceful shutdown incompleto** — ✅ `main.py:80`: `await application.stop()` agregado antes de `runner.cleanup()`.
8. **SQLite sin WAL mode** — ✅ `PRAGMA journal_mode=WAL` agregado en `database.py:8`.
9. **`context.user_data` sin limpieza** — ✅ `bot_reply_message_id` se limpia al inicio de `handle_video` en `telegram_handlers.py:204`.
10. **DB connections leak en excepciones** — ✅ `database.py`: todas las funciones ahora envuelven `sqlite3.connect()` en `try/finally` garantizando `conn.close()`.
11. **`BOT_TOKEN` en texto plano** — ✅ Se agregó advertencia si el token viene de `.env`. En producción debe ir en `EnvironmentFile` de systemd (ver sección Despliegue).

### 🟡 Importantes

12. **Rate limiting** — Sin protección contra spam de videos o ubicaciones. Añadir throttle por usuario.
13. **`get_pins` sin límite** — ✅ `database.py:188`: `LIMIT 500` por defecto con `OFFSET` para paginación. El frontend siempre pide 500 por página.
14. **Error de API expuesto al cliente** — ✅ `web_handlers.py:74-79`: `chat_id.lstrip("-").isdigit()` antes de `int()`; errores internos devuelven `"internal error"` genérico.
15. **Streaming loop sin manejo de desconexión** — ✅ `web_handlers.py:62-65`: `ConnectionResetError` y `asyncio.CancelledError` capturados con `try/except pass` para cierre graceful.
16. **Health check endpoint** — ✅ `GET /api/health` + watchdog systemd (`Type=notify`, `WatchdogSec=30`) en `main.py:40-44,97`. Documentado en sección 8 del README.
17. **`uv sync --frozen` en deploy** — `deploy.sh:6` debería usar `--frozen` para garantizar que se usa el lockfile exacto.
18. **Reconexión automática** — Si Telegram falla, `updater.start_polling()` muere sin reintentar.
19. **Mapa salta al renderizar** — ✅ `web/index.html`: `map.fitBounds()` ya no se ejecuta cuando el usuario navega el mapa. El flag `skipBoundsFit` evita el re-centrado. Se mantiene en el render inicial y cambios de filtro sin bounds activo.
20. **Procesar `video` normal además de `video_note`** — `telegram_handlers.py:195`: solo maneja `message.video_note`. Los videos normales se ignoran sin feedback.
21. **Auto-deletes silencian errores** — Si el bot pierde permisos "Delete messages", los `try/except pass` ocultan el problema. Mejor loguear el error.
22. **Validación de `BOT_TOKEN` al arrancar** — ✅ `config.py:14`: `if not TOKEN: raise SystemExit(...)` con mensaje claro.
23. **`handle_emoji_text` acepta cualquier texto ≤10 caracteres** — No valida que sea un emoji real. Cualquier texto se guarda como "emoji".
24. **Eliminar pin propio** — Los usuarios no pueden borrar sus cheers. Un comando `/delete` o un botón en el popup del mapa lo resolvería.
25. **Nominatim rate limit** — ✅ Resuelto por el lock + sleep(1) en `reverse_geocode` (recomendación #4).
26. **Indicador de carga en el mapa** — ✅ `web/index.html:270`: spinner CSS animado + "Loading..." mientras se cargan filtros server-side.
27. **Network watchdog** — Script que verifique conectividad (ping al gateway) cada minuto y reinicie la interfaz de red si no hay respuesta. Evita quedar inaccesible como ocurrió con el WiFi.
28. **Watchdog hardware** — Configurar `/dev/watchdog` del Pi para que el sistema se reinicie automáticamente si se cuelga por completo (kernel panic, out-of-memory, etc.).
29. **`_watchdog_loop` sin cancelación** — `main.py:95`: el `asyncio.create_task(_watchdog_loop())` nunca se cancela en shutdown, lo que puede generar `Task was destroyed but it is pending!`.
30. **`web_app["bot"]` seteado después de arrancar el web server** — `main.py:85-89`: el `site.start()` se llama antes de `web_app["bot"] = application.bot`. Hay una ventana donde el server acepta requests pero `bot` no está disponible.
31. **Race condition en geocode cache** — `telegram_handlers.py:233-242`: el doble-check pattern tiene un hueco entre el check sin lock y `_nominatim_lock`, donde otra corrutina puede insertar la misma clave.
32. **Geocode cache LRU naive** — `telegram_handlers.py:258-259`: cuando el caché llega a 500 entradas, elimina una clave arbitraria (`next(iter(...))`) en vez de la más vieja. Mejor usar `OrderedDict` o `@lru_cache`.
33. **`populateFilters` acumula event listeners** — `web/index.html`: cada vez que se llama, registra nuevos listeners (`change`, `click`) sin remover los anteriores. Si se llama dos veces, los eventos se duplican.

### 🟢 Buenas prácticas

34. **Test básico** — No hay ningún test. Uno que verifique `add_pin` + `get_pins` ahorraría problemas.
35. **SRI en CDN de Leaflet** — Los `<link>` y `<script>` de unpkg deberían incluir el atributo `integrity` para evitar supply chain attacks.
36. **`.env.example`** — No existe, los nuevos desarrolladores no saben qué variables necesitan. Crear uno con placeholders.
37. **`GROUP_RADIUS` hardcodeado** — `web/index.html:99`: 10 metros fijo para agrupar pines. Podría ser configurable o dinámico según el nivel de zoom.
38. **Caché de Nominatim persistente** — La caché en memoria se pierde al reiniciar el bot. Una caché en disco/DB la conservaría entre reinicios.
39. **Paginación de pines** — ✅ API soporta `limit`/`offset` + filtros `user_ids`, `date_from`, `date_to`, `q` en SQL. Frontend con navegación Prev/Next (`PAGE_SIZE=500`) en `web/index.html`.
40. **`deploy.sh` sin verificación de `git pull`** — Si hay conflictos locales, `git pull` falla y el script se detiene sin mensaje claro. Un `git diff --quiet` previo sería más robusto.
41. **Logging estructurado** — `logging.basicConfig` plano dificulta filtrar y debuggear en producción.
42. **Log rotation** — Los logs del bot crecen sin límite y pueden llenar la SD. Configurar `logrotate` para rotar diariamente, comprimir y eliminar logs viejos (>7 días).
43. **Monitor de salud de la SD** — `dmesg | grep -i "mmc\|sdhci\|i/o error"` chequeado periódicamente, más `df -h` para espacio libre. Una SD llena o muriendo causa caídas silenciosas.
44. **Separar `.env` de producción y desarrollo** — Usar `.env.production` / `.env.development` para evitar confusiones o commits accidentales.
45. **Comando `/mypins`** — En chat privado con el bot para que el usuario vea/elimine sus propios pins.
46. **`Pasar cabecera `Range`** ya implementado en el proxy de video, pero documentar que es esencial para el seeking.
47. **Ruff integrado** — Ya configurado como dependencia dev. Ejecutar `uv run ruff check .` y `uv run ruff format .` periódicamente.
48. **`index_handler` anidado en `main()`** — `main.py:72`: está definido como función interna sin necesidad. Podría ser función top-level para claridad.
49. **Alias `AiohttpClient` sin usar** — `web_handlers.py:3`: se importa `ClientSession as AiohttpClient` pero nunca se referencia.
50. **Validación de `chat_id` duplicada** — `web_handlers.py:10-13, 81-84, 106-109`: el mismo patrón de validación se repite en 3 handlers. Extraer a helper.
51. **Columnas accedidas por índice numérico** — `database.py:180, 225-241`: usar `sqlite3.Row` haría el código más legible y menos frágil ante cambios de esquema.

## Despliegue en Raspberry Pi

### 1. Prerrequisitos

```bash
# Instalar dependencias del sistema
sudo apt update && sudo apt install -y python3 python3-venv git rclone

# Clonar el repositorio
git clone https://github.com/Robertovarbra/telegram-cheers-map.git
cd telegram-cheers-map

# Crear y activar el venv (o usar uv sync — ver deploy_example.sh)
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. EnvironmentFile seguro para el token

El token de Telegram no debe vivir en `.env` en producción. Se guarda en `/etc/cheers-bot/env` con permisos restringidos:

```bash
sudo mkdir -p /etc/cheers-bot
sudo tee /etc/cheers-bot/env <<'EOF'
BOT_TOKEN=tu_token_aqui
BOT_USERNAME=nombre_de_tu_bot
WEB_PORT=8080
EOF
sudo chmod 600 /etc/cheers-bot/env  # solo root puede leerlo
```

### 3. Servicio systemd — cheers-bot

Crear `/etc/systemd/system/cheers-bot.service`:

```ini
[Unit]
Description=Cheers Map Bot
After=network.target

[Service]
Type=notify
WatchdogSec=30
User=pi
WorkingDirectory=/home/pi/telegram-cheers-map
EnvironmentFile=/etc/cheers-bot/env
ExecStart=/home/pi/telegram-cheers-map/.venv/bin/python src/main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable cheers-bot   # arranque automático al bootear
sudo systemctl start cheers-bot    # iniciar ahora
```

### 4. Servicio systemd — Cloudflare Tunnel

Si usas Cloudflare Tunnel para exponer el mapa sin abrir puertos, crea `/etc/systemd/system/cloudflared-tunnel.service`:

> **Nota:** Debes autenticar y configurar el tunnel con `cloudflared tunnel login` y `cloudflared tunnel create cheersmap`. La configuración queda en `~/.cloudflared/`.

```ini
[Unit]
Description=Cloudflare Tunnel — Cheers Map
After=network.target

[Service]
Type=simple
User=pi
ExecStart=/usr/local/bin/cloudflared tunnel run cheersmap
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable cloudflared-tunnel    # arranque automático al bootear
sudo systemctl start cloudflared-tunnel     # iniciar ahora
```

### 5. Verificar que ambos arrancan automáticamente

```bash
sudo systemctl is-enabled cheers-bot            # debe decir "enabled"
sudo systemctl is-enabled cloudflared-tunnel    # debe decir "enabled"
```

En cada reinicio del Pi, ambos servicios se inician solos.

### 6. Actualizar el bot (deploy)

Utilizar scripts/deploy_example.sh para crear deploy_cheers_map.sh considerando la máquina local.

```bash
# En el Raspberry Pi
ssh pi@raspberry ./deploy_cheers_map.sh

# (el script hace: git pull origin main && uv sync && sudo systemctl restart cheers-bot)
```

### 7. Backup automático a Cloudflare R2

Configurar rclone una sola vez y programar el backup diario vía cron:

```bash
rclone config  # Configurar remote "r2" de tipo s3 con tus credenciales R2
cp .env.backup.example .env.backup  # Editar con tu bucket name
crontab -e  # Agregar: 0 6 * * * /home/robertovarbra/telegram-cheers-map/scripts/backup.sh
```

### 8. Monitoreo (watchdog + health check)

El bot incluye dos capas de defensa:

1. **Watchdog de systemd** — el bot envía un latido cada 15s via `$NOTIFY_SOCKET`; si deja de hacerlo por 30s, systemd mata y reinicia el proceso automáticamente. Se activa con `Type=notify` y `WatchdogSec=30` en el servicio (sección 3). No requiere dependencias extra.
2. **Health check HTTP** — endpoint `GET /api/health` que verifica que la API responde. Un cron local puede reiniciar el servicio si falla.

#### Health check + reinicio automático (cron)

Agregar al crontab del usuario `pi`:

```bash
crontab -e
```

Y pegar:

```bash
* * * * * curl -sf http://localhost:8080/api/health || sudo systemctl restart cheers-bot
```

Esto verifica cada minuto que el endpoint responda. Si falla (proceso vivo pero DB corrupta, SD llena, etc.), reinicia el servicio.

> **Nota:** Para que `sudo systemctl` funcione sin contraseña desde cron, agregá en `/etc/sudoers.d/cheers-bot`:
> ```
> pi ALL=(root) NOPASSWD: /usr/bin/systemctl restart cheers-bot
> ```
