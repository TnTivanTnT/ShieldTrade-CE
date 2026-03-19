FROM python:3.12-slim

# Evitar archivos basura de python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Instalar Supervisor
RUN apt-get update && apt-get install -y supervisor && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar librerías
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código
COPY . .

# Exponer el puerto para la API
EXPOSE 8000

# Comando de arranque (Supervisor toma el control)
CMD ["supervisord", "-c", "/app/supervisord.conf"]