# Swing bot en Vercel (Alpaca paper)

Bot de swing trading que opera **solo en la cuenta PAPER de Alpaca** (dinero ficticio) para
recoger datos de prueba. Corre gratis en el plan Hobby de Vercel, una vez al día, sin servidor.

Está bloqueado a paper en dos sitios: el cliente de Alpaca se crea con `paper=True` fijo y el bot
no funciona si `config.json` dice `"paper": false`.

## Qué hace cada día de mercado (entre las 11:00 y las 12:00 de Madrid)

1. **Lee su historial** de órdenes en Alpaca (no necesita disco: cada orden suya lleva un id `swb-…`).
2. **Gestiona** lo abierto: vende si hay resultados en ≤ 2 días o si lleva 15 días de mercado 
abierta; cancela compras limitadas que llevan 2 días sin ejecutarse.
3. **Busca operaciones nuevas** (máx. 2 posiciones):

   * Acciones de EE. UU. negociables en Alpaca (sin ETF, warrants, preferentes…).
   * Setups en gráfico diario: ruptura de máximo de cierre de 20 días, bandera alcista,
recuperación de la media de 20 días.
   * Filtros: precio 5–45 $, liquidez media ≥ 10 M$/día, sin subidas de > 50 % en un día,
sin resultados en los próximos 15 días (calendario público de Nasdaq).
   * Orden **bracket GTC**: compra limitada al último cierre, stop = entrada − 1,5 × ATR(14),
objetivo = entrada + 2 × riesgo. Tamaño: lo que permita perder como máximo 2 €.
4. **Te avisa** al móvil con un resumen.

Frenos: si las pérdidas cerradas llegan a 15 €, deja de abrir operaciones; si no puede leer el
calendario de resultados, ese día no abre nada; `SWINGBOT\_KILL=1` lo para.

## Qué necesitas

* Cuenta de **GitHub** (gratis).
* Cuenta de **Vercel** en plan **Hobby** (gratis), entrando con GitHub.
* Las **claves API de la cuenta paper** de Alpaca.
* Opcional: la app **ntfy** en el iPhone para los avisos.

## Paso a paso

### 1\. Sube el código a un repositorio privado de GitHub

1. github.com → **New repository** → nombre `swing-bot` → **Private** → *Create repository*.
2. En el repositorio vacío: **uploading an existing file** → arrastra **el contenido** de la
carpeta `swing-bot-vercel` (las carpetas `api`, `swingbot`, `tests` y los ficheros sueltos)
→ *Commit changes*.
3. Comprueba que en la raíz del repositorio se ven `vercel.json`, `config.json`, `requirements.txt`
y las carpetas `api` y `swingbot`.

### 2\. Prepara los secretos

Inventa dos contraseñas largas (20+ caracteres, solo letras y números):

* `CRON\_SECRET`: para lanzar la pasada diaria.
* `REPORT\_KEY`: para ver el informe de operaciones.

Para los avisos con ntfy: instala **ntfy** en el iPhone, pulsa **+** y suscríbete a un tema largo e
imposible de adivinar (por ejemplo `swingbot-angel-8f3k2q9x`).

### 3\. Crea el proyecto en Vercel

1. vercel.com → **Sign up** → plan **Hobby** → *Continue with GitHub*.
2. **Add New… → Project** → importa el repositorio `swing-bot` (si no aparece, *Adjust GitHub App
Permissions* y dale acceso).
3. **Framework Preset: Other**. No toques los comandos de build.
4. Despliega **Environment Variables** y añade:

|Nombre|Valor|
|-|-|
|`ALPACA\_API\_KEY`|tu key de **paper**|
|`ALPACA\_SECRET\_KEY`|tu secret de **paper**|
|`CRON\_SECRET`|la primera contraseña|
|`REPORT\_KEY`|la segunda contraseña|
|`NTFY\_TOPIC`|el tema de ntfy (opcional)|

5. **Deploy**. Tarda un par de minutos.

### 4\. Prueba

Abre en el navegador (cambia `TU-PROYECTO` y la clave):

```
https://TU-PROYECTO.vercel.app/api/run?key=TU\_CRON\_SECRET
```

Tarda 1–3 minutos y muestra el resumen del día (y te llega el aviso). Las órdenes que ponga las
verás en Alpaca, en la cuenta paper, en **Orders**.

El informe de todas las operaciones:

```
https://TU-PROYECTO.vercel.app/api/report?key=TU\_REPORT\_KEY
https://TU-PROYECTO.vercel.app/api/report?key=TU\_REPORT\_KEY\&format=csv   (para Excel)
```

### 5\. Comprueba la tarea diaria

En Vercel: proyecto → **Settings → Cron Jobs**. Debe aparecer `/api/run` con `0 10 \* \* 1-5`
(10:00 UTC de lunes a viernes = entre 11:00 y 12:00 en Madrid en invierno, una hora antes en verano).
Desde ahí también puedes lanzarlo a mano con **Run**.

## Cambiar cosas

* **Reglas** (presupuesto, pérdida máxima, precios…): edita `config.json` en GitHub (icono del
lápiz) → *Commit*. Vercel vuelve a publicar solo en un minuto.
* **Parar las entradas nuevas**: en Vercel, *Settings → Environment Variables* → añade
`SWINGBOT\_KILL` = `1` → *Deployments → … → Redeploy*. Lo abierto se sigue gestionando.
* **Ver errores**: proyecto → **Logs** (el plan gratuito guarda 1 hora).

## Problemas conocidos

* **"subscription does not permit querying recent SIP data"**: pon `"data\_feed": "iex"` en
`config.json`. Con IEX el volumen es solo una parte del mercado: baja también
`min\_dollar\_volume\_usd` (por ejemplo a 500000).
* **Timeout (la pasada supera los 5 minutos del plan gratuito)**: en los logs verás
"Task timed out". Si pasa, se puede partir el escaneo en dos llamadas.
* No uses la misma cuenta paper para operar a mano: el bot solo mira sus órdenes (`swb-…`), pero
el efectivo y las posiciones de un mismo valor se mezclarían.

## Pruebas locales

```bash
pip install alpaca-py pytest \&\& python -m pytest -q
```

## Pasar a IBKR en el futuro

Todo lo que habla con el bróker está en `swingbot/broker.py`. Para IBKR se escribe otra clase con
los mismos métodos y el resto del bot no cambia (IBKR necesita IB Gateway siempre encendido, así que
para entonces haría falta un servidor, no Vercel).

\---

Herramienta de pruebas en cuenta paper. No es asesoramiento financiero.

