# Base image
FROM python:3.11-slim

# Répertoire de travail
WORKDIR /app

# Copie des fichiers de dépendances
COPY requirements.txt .

# Installation des dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Copie du code source
COPY . .

# Variable d'environnement pour le port
ENV PORT=8000

# Commande pour lancer l'application (à adapter)
CMD ["python", "app.py"]