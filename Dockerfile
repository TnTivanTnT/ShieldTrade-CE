FROM python:3.9-slim

WORKDIR /app

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código
COPY . .

# Arrancar Uvicorn directamente (FastAPI levantará el hilo de trading automáticamente)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]