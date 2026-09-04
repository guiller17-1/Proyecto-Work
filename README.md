# Dashboard Truck Visits

Dashboard web en Flask que descarga el CSV desde OneDrive, replica las principales transformaciones de Power Query y muestra un gráfico combinado de columnas y línea.

## Funciones

- Eje X: `Truck Visit Truck License`.
- Columnas: promedio de `TIEMPO`.
- Línea: promedio de `TARGET`.
- Filtros: `SEDE` (usa `Stow` si no existe), `Status`, `CONSIDERAR` y `TURNO`.
- Actualización automática cada 10 minutos.
- KPIs y tabla de detalle.
- Lectura del CSV desde un vínculo OneDrive con descarga directa.

## Ejecutar localmente

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

En Windows PowerShell, activa el entorno con:

```powershell
.venv\Scripts\Activate.ps1
```

Abre `http://localhost:5000`.

## Subir a GitHub

1. Descomprime el ZIP.
2. Crea un repositorio vacío en GitHub.
3. Sube todos los archivos manteniendo las carpetas `templates` y `static`.

## Publicar en Render

1. En Render selecciona **New > Web Service**.
2. Conecta el repositorio de GitHub.
3. Runtime: **Python 3**.
4. Build Command: `pip install -r requirements.txt`.
5. Start Command: `gunicorn app:app`.
6. Agrega la variable de entorno `CSV_URL` con el enlace directo de OneDrive.

El proyecto ya contiene un `render.yaml`, así que también puedes usar **New > Blueprint**.

## Fuente de datos

La URL de prueba ya está configurada en `app.py`. Para evitar dejarla escrita en el repositorio, configura en Render:

```text
CSV_URL=https://...&download=1
```

## Notas sobre los cálculos

- `Handled limpio`: usa la hora actual cuando `Handled` está vacío.
- `TRANSACCION`: convierte Empty según las reglas entregadas.
- Duplicidad: se calcula con Status + placa + Start Date, siguiendo `CONCA 20`.
- `CONTAINER TYPE`: si la columna no existe, se infiere desde `ISO Group (order)` e `ISO Type`.
- `SEDE`: si no existe en el CSV, se usa `Stow` provisionalmente.
- Se aplica la lógica de TARGET, TIEMPO, ACEPTABLE, CUMPLIMIENTO y CONSIDERAR.

## Seguridad

El vínculo de prueba permite descargar el archivo sin autenticación. No uses esta modalidad para datos sensibles. En una versión corporativa conviene Microsoft Graph o Power Automate con autenticación.
