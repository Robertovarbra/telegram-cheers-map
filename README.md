# Telegram Cheers Map

Bot de Telegram que permite a usuarios de un grupo enviar video notes y ubicaciones para mostrarlos en un mapa interactivo.

## Stack

- Python 3.9+, `python-telegram-bot` v21+, `aiohttp` v3.11+
- SQLite (`videos.db`)
- Frontend: Leaflet.js + OpenStreetMap
- CD: GitHub Actions + Tailscale → Raspberry Pi (systemd + Cloudflare Tunnel)

## Despliegue en Raspberry Pi

### 1. Prerrequisitos

```bash
# Instalar dependencias del sistema
sudo apt update && sudo apt install -y python3 python3-venv git rclone

# Instalar uv (gestor de paquetes)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Instalar Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Clonar el repositorio
git clone https://github.com/Robertovarbra/telegram-cheers-map.git
cd telegram-cheers-map

# Instalar dependencias
uv sync
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
Wants=network-online.target
After=network-online.target

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

> `Wants=`/`After=network-online.target` hacen que el servicio espere a que haya **ruta y DNS reales**, no sólo a que la interfaz esté levantada: `network.target` no espera a DHCP ni a la resolución. En hardware lento (Pi 1/Zero/B+) evita que el bot y el tunnel arranquen antes de tener red y se pasen el primer par de minutos reintentando. La misma directiva va en la unit del tunnel (sección 4), y depende de que exista un proveedor de *wait-online* habilitado (`NetworkManager-wait-online` o `systemd-networkd-wait-online`); sin él el target se alcanza al instante y no aporta nada.

```bash
systemctl list-unit-files | grep wait-online   # debe haber uno "enabled"
```

### 4. Servicio systemd — Cloudflare Tunnel

Si usas Cloudflare Tunnel para exponer el mapa sin abrir puertos, crea `/etc/systemd/system/cloudflared.service`:

> **Nota:** Debes autenticar y configurar el tunnel con `cloudflared tunnel login` y `cloudflared tunnel create cheersmap`. La configuración queda en `~/.cloudflared/`. El `User=` del servicio debe ser el mismo que hizo el `login`: las credenciales se leen de su `$HOME` (o de la ruta absoluta de `credentials-file` en `config.yml`).

```ini
[Unit]
Description=Cloudflare Tunnel
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=pi
ExecStart=/usr/local/bin/cloudflared tunnel run cheersmap
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> `After=network-online.target` no es adorno: `network.target` sólo ordena contra el arranque de la red, **no espera a que haya ruta/DNS**. Sin él, cloudflared arranca antes de tener conectividad y se pasa un par de minutos reintentando antes del `precheck`; mientras no registra conexiones, la URL pública devuelve **530**. Si la unit ya existe, añade `Wants=`/`After=network-online.target` en `[Unit]` y luego `sudo systemctl daemon-reload && sudo systemctl restart cloudflared`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable cloudflared    # arranque automático al bootear
sudo systemctl start cloudflared     # iniciar ahora
```

> **El nombre del servicio es el nombre del fichero**, no el `Description`. Si el fichero es `cloudflared.service`, los comandos son `systemctl start cloudflared`. `cloudflared-tunnel` sólo existiría si crearas `/etc/systemd/system/cloudflared-tunnel.service`; si no coincide, systemd responde `Unit ... not found`.

#### 4.1 Instalar el binario — `armhf` no funciona en ARMv6 (Pi 1 / Zero / B+)

El `.deb` oficial instala la variante **`armhf`, compilada para ARMv7**. En un Pi con `uname -m` = `armv6l` el proceso muere al instante con `Illegal instruction` (`status=4/ILL`) y el servicio entra en bucle de reinicio. Hay que usar el binario **`arm`**:

```bash
uname -m    # armv6l -> usa cloudflared-linux-arm (NO dpkg --print-architecture, que da armhf)
curl -L -o /tmp/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm
sudo install -m 0755 /tmp/cloudflared /usr/local/bin/cloudflared
cloudflared --version
```

> **Ojo con el orden:** el `.deb` crea en su `postinst` el symlink `/usr/local/bin/cloudflared -> /usr/bin/cloudflared`, y su `postrm` hace `rm -f /usr/local/bin/cloudflared`. Si purgas el paquete **después** de copiar el binario manual, te borra el binario. Purga primero y copia después:

```bash
sudo dpkg --purge cloudflared    # elimina el .deb armhf y sus restos
# ...y ahora sí, copia el binario arm a /usr/local/bin/cloudflared
```

#### 4.2 Actualizar cloudflared

`ExecStart` apunta a `/usr/local/bin/cloudflared`, así que instalar el `.deb` (que va a `/usr/bin`) **no actualiza nada** de lo que corre el servicio. Se actualiza el binario a mano y se reinicia:

```bash
curl -L -o /tmp/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm
sudo install -m 0755 /tmp/cloudflared /usr/local/bin/cloudflared
sudo systemctl restart cloudflared    # "restart", no "start": si ya corre, "start" no hace nada
```

Verifica que el proceso vivo es el binario nuevo. `cloudflared --version` no basta (resuelve por `PATH` y puede mostrar otro binario); hay que mirar el ejecutable real del proceso:

```bash
PID=$(systemctl show -p MainPID --value cloudflared)
readlink -f /proc/$PID/exe     # debe ser /usr/local/bin/cloudflared
journalctl -u cloudflared -n 20 --no-pager | grep "Registered tunnel connection"
```

### 5. Verificar que ambos arrancan automáticamente

`is-enabled` sólo dice que hay un symlink en `multi-user.target.wants`; no dice que el servicio arranque bien. Para comprobarlo de verdad hay que reiniciar y mirar:

```bash
sudo systemctl is-enabled cheers-bot cloudflared   # enabled / enabled
sudo systemctl is-active cheers-bot cloudflared    # active / active

# ¿el tunnel está realmente conectado? deben salir 4 líneas (conexiones por defecto)
journalctl -u cloudflared -b --no-pager | grep -c "Registered tunnel connection"

# y la URL pública responde
curl -s -o /dev/null -w "%{http_code}\n" https://cheersmap.robertoapproved.com/api/health
```

> **En un Pi lento (1/Zero/B+) hay una ventana muerta de varios minutos tras el reinicio.** En un Pi B+ el arranque completo (boot → URL 200) ronda los 4–5 minutos: cloudflared tarda un par de minutos en arrancar y el bot manda `READY=1` poco antes de que la URL empiece a responder. Durante esa ventana la URL devuelve **530** y es normal: ni el tunnel está conectado ni el servidor web está escuchando. No lo confundas con un fallo; confirma con `journalctl -u cheers-bot -b` que el bot está inicializando.

En cada reinicio del Pi, ambos servicios se inician solos.

### 6. Actualizar el bot (deploy)

El deploy es automático vía GitHub Actions + Tailscale. No es necesario conectarse al Pi.

#### 6.1 Cómo funciona

1. **Trigger**: al hacer merge a `main`, o manualmente desde GitHub Actions (botón "Run workflow")
2. **Tailscale**: el runner de GitHub se conecta a tu tailnet mediante un OAuth client
3. **SSH**: se conecta al Pi por su IP de Tailscale usando una key SSH dedicada
4. **`scripts/deploy.sh`**: actualiza el código, instala dependencias, ejecuta lint y reinicia el servicio

```
[merge a main] → [GitHub Actions] → [Tailscale] → [SSH al Pi] → scripts/deploy.sh
```

#### 6.2 Configuración inicial (una sola vez)

**a. SSH key para GitHub Actions**

En tu máquina local, generar una key dedicada:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/gh-actions-deploy -N ""
ssh-copy-id -i ~/.ssh/gh-actions-deploy.pub pi@<IP_LOCAL_DEL_PI>
```

**b. Passwordless sudo para systemctl**

En el Pi:

```bash
sudo tee /etc/sudoers.d/cheers-bot >/dev/null <<'EOF'
pi ALL=(root) NOPASSWD: /usr/bin/systemctl restart cheers-bot, /usr/bin/systemctl restart cloudflared, /usr/bin/systemctl daemon-reload
EOF
sudo chmod 0440 /etc/sudoers.d/cheers-bot
sudo chown root:root /etc/sudoers.d/cheers-bot
sudo visudo -c
```

> El fichero debe quedar `root:root` y modo **0440**. Con `0644` sudo lo **ignora** (avisa y sigue) y `visudo -c` termina en error; el síntoma en runtime es `sudo: a password is required` en mitad de un deploy. Valida con `sudo visudo -c` (el `sudoers` completo, incluidos los ficheros de `/etc/sudoers.d/` que de verdad se cargan), no con `visudo -cf fichero`, que sólo mira ese fichero.

> El deploy sólo necesita `restart cheers-bot`; `restart cloudflared` y `daemon-reload` se añaden para poder tocar las units a mano sin contraseña. No uses `ALL` ni `systemctl *`: si el usuario de SSH se compromete, la regla es lo único que separa al atacante de root.

**c. OAuth client en Tailscale**

En `https://login.tailscale.com/admin/settings/oauth`, crear un OAuth client con:
- Descripción: `github-actions`
- Permisos: `Devices → Core` (Read + Write), `Keys → Auth Keys` (Read + Write)
- Tags: `tag:ci`

**d. Secrets en GitHub**

En `Settings → Secrets and variables → Actions`, agregar:

| Secret | Valor |
|--------|-------|
| `TAILSCALE_IP` | IP del Pi en Tailscale (ej: `100.x.x.x`) |
| `PI_USER` | `pi` |
| `SSH_PRIVATE_KEY` | Contenido de `~/.ssh/gh-actions-deploy` (key privada) |
| `TS_OAUTH_CLIENT_ID` | Client ID del OAuth client |
| `TS_OAUTH_SECRET` | Client Secret del OAuth client |

### 7. Backup automático a Cloudflare R2

Configurar rclone una sola vez y programar el backup diario vía cron:

```bash
rclone config  # Configurar remote "r2" de tipo s3 con tus credenciales R2
cp .env.backup.example .env.backup  # Editar con tu bucket name
crontab -e  # Agregar: 0 6 * * * /home/pi/telegram-cheers-map/scripts/backup.sh
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

> **Nota:** Esto depende de la regla `NOPASSWD` de `/etc/sudoers.d/cheers-bot` (sección 6.2, punto *b*). Si el fichero falta o tiene permisos incorrectos, el cron falla **en silencio**: verás el health check fallar cada minuto en `journalctl` pero el servicio no se reiniciará. Es la misma regla, no la dupliques aquí.
