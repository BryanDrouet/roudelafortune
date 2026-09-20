# Base image
FROM python:3.11-slim

# Répertoire de travail
WORKDIR /app

# Installation des dépendances système
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copie UNIQUEMENT requirements.txt d'abord (cache pip)
COPY requirements.txt .

# Installation des dépendances Python
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copie TOUT le reste (y compris uwsgi.ini et app.py)
COPY . .

# Variable d'environnement
ENV PORT=8000

# Port exposé
EXPOSE 8000

# Commande de lancement
CMD ["uwsgi", "--ini", "uwsgi.ini"]