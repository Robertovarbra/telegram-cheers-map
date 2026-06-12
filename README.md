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
echo "pi ALL=(root) NOPASSWD: /usr/bin/systemctl restart cheers-bot" | sudo tee /etc/sudoers.d/cheers-bot
```

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

> **Nota:** Para que `sudo systemctl` funcione sin contraseña desde cron, agregá en `/etc/sudoers.d/cheers-bot`:
> ```
> pi ALL=(root) NOPASSWD: /usr/bin/systemctl restart cheers-bot
> ```
