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
2. **Timeout en proxy de video** — ✅ `web_handlers.py:42`: `ClientTimeout(sock_read=10)`.
3. **XSS en `topEmoji` del grupo marker** — ✅ Todos los valores dinámicos en `web/index.html` usan `esc()`.
4. **Caché para Nominatim (OpenStreetMap)** — ✅ Caché LRU en memoria (max 500 entradas) + `asyncio.Lock` + `sleep(1)` entre requests en `telegram_handlers.py:226-255`.
5. **Graceful shutdown incompleto** — ✅ `main.py:80`: `await application.stop()` agregado antes de `runner.cleanup()`.
6. **SQLite sin WAL mode** — ✅ `PRAGMA journal_mode=WAL` agregado en `database.py:8`.
7. **`context.user_data` sin limpieza** — ✅ `bot_reply_message_id` se limpia al inicio de `handle_video` en `telegram_handlers.py:204`.
8. **DB connections leak en excepciones** — ✅ `database.py`: todas las funciones ahora envuelven `sqlite3.connect()` en `try/finally` garantizando `conn.close()`.
9. **`BOT_TOKEN` en texto plano** — `.env` contiene el token en texto plano. Aunque está gitignored, cualquiera con acceso al servidor o máquina de desarrollo puede leerlo. Un backup o swap file podría exponerlo accidentalmente.

### 🟡 Importantes

10. **Rate limiting** — Sin protección contra spam de videos o ubicaciones. Añadir throttle por usuario.
11. **`get_pins` sin límite** — `database.py:173`: sin `LIMIT`, un chat con 10K+ pines explota memoria y cuelga el frontend. Relacionado con #32 pero con riesgo de crash real.
12. **Error de API expuesto al cliente** — `web_handlers.py:73-80`: `int(chat_id)` sin validar; el mensaje de error (ValueError, DB errors) se devuelve como JSON al cliente. Riesgo de fuga de información interna.
13. **Streaming loop sin manejo de desconexión** — `web_handlers.py:58-62`: si el cliente se desconecta, `ConnectionResetError` en el `async for chunk` no se captura y propaga como 500.
14. **Health check endpoint** — Falta un `/api/health` o `/ping` para monitorear el bot (esencial en Raspberry Pi).
15. **`uv sync --frozen` en deploy** — `deploy.sh:6` debería usar `--frozen` para garantizar que se usa el lockfile exacto.
16. **Reconexión automática** — Si Telegram falla, `updater.start_polling()` muere sin reintentar.
17. **Mapa salta al renderizar** — `web/index.html:267`: `map.fitBounds()` se ejecuta en cada cambio de filtro, frustrando al usuario que explora el mapa. Debería solo ejecutarse en el render inicial.
18. **Procesar `video` normal además de `video_note`** — `telegram_handlers.py:195`: solo maneja `message.video_note`. Los videos normales se ignoran sin feedback.
19. **Auto-deletes silencian errores** — Si el bot pierde permisos "Delete messages", los `try/except pass` ocultan el problema. Mejor loguear el error.
20. **Validación de `BOT_TOKEN` al arrancar** — Si falta `.env`, `TOKEN = None` y la app explota con un error poco claro. Añadir `if not TOKEN: raise SystemExit(...)`.
21. **`handle_emoji_text` acepta cualquier texto ≤10 caracteres** — No valida que sea un emoji real. Cualquier texto se guarda como "emoji".
22. **Eliminar pin propio** — Los usuarios no pueden borrar sus cheers. Un comando `/delete` o un botón en el popup del mapa lo resolvería.
23. **Nominatim rate limit** — ✅ Resuelto por el lock + sleep(1) en `reverse_geocode` (recomendación #4).
24. **Indicador de carga en el mapa** — Al cambiar filtros no hay feedback visual. Un spinner o "Loading..." mínimo ayudaría.
25. **Network watchdog** — Script que verifique conectividad (ping al gateway) cada minuto y reinicie la interfaz de red si no hay respuesta. Evita quedar inaccesible como ocurrió con el WiFi.
26. **Watchdog hardware** — Configurar `/dev/watchdog` del Pi para que el sistema se reinicie automáticamente si se cuelga por completo (kernel panic, out-of-memory, etc.).

### 🟢 Buenas prácticas

27. **Test básico** — No hay ningún test. Uno que verifique `add_pin` + `get_pins` ahorraría problemas.
28. **SRI en CDN de Leaflet** — Los `<link>` y `<script>` de unpkg deberían incluir el atributo `integrity` para evitar supply chain attacks.
29. **`.env.example`** — No existe, los nuevos desarrolladores no saben qué variables necesitan. Crear uno con placeholders.
30. **`GROUP_RADIUS` hardcodeado** — `web/index.html:99`: 10 metros fijo para agrupar pines. Podría ser configurable o dinámico según el nivel de zoom.
31. **Caché de Nominatim persistente** — La caché en memoria se pierde al reiniciar el bot. Una caché en disco/DB la conservaría entre reinicios.
32. **Paginación de pines** — `get_pins` devuelve todos los pines del chat sin límite. Un chat con 10K+ pines enviaría demasiados datos al frontend.
33. **`deploy.sh` sin verificación de `git pull`** — Si hay conflictos locales, `git pull` falla y el script se detiene sin mensaje claro. Un `git diff --quiet` previo sería más robusto.
34. **Logging estructurado** — `logging.basicConfig` plano dificulta filtrar y debuggear en producción.
35. **Log rotation** — Los logs del bot crecen sin límite y pueden llenar la SD. Configurar `logrotate` para rotar diariamente, comprimir y eliminar logs viejos (>7 días).
36. **Monitor de salud de la SD** — `dmesg | grep -i "mmc\|sdhci\|i/o error"` chequeado periódicamente, más `df -h` para espacio libre. Una SD llena o muriendo causa caídas silenciosas.
37. **Separar `.env` de producción y desarrollo** — Usar `.env.production` / `.env.development` para evitar confusiones o commits accidentales.
38. **Comando `/mypins`** — En chat privado con el bot para que el usuario vea/elimine sus propios pins.
39. **`Pasar cabecera `Range`** ya implementado en el proxy de video, pero documentar que es esencial para el seeking.
40. **Ruff integrado** — Ya configurado como dependencia dev. Ejecutar `uv run ruff check .` y `uv run ruff format .` periódicamente.

## Despliegue

```bash
# En el Raspberry Pi
ssh pi ./deploy_cheers_map.sh
# (el script interno hace: git pull origin main && uv sync && sudo systemctl restart cheers-bot)
```

## Backup a Cloudflare R2

```bash
# En el Raspberry Pi (una sola vez)
sudo apt install rclone
rclone config  # Configurar remote "r2" de tipo s3 con tus credenciales R2
cp .env.backup.example .env.backup  # Editar con tu bucket name
crontab -e  # Agregar: 0 6 * * * /home/robertovarbra/telegram-cheers-map/scripts/backup.sh
```

## Túnel de desarrollo

```bash
cloudflared tunnel --config ~/.cloudflared/config-dev.yml run cheersmap-dev
```
