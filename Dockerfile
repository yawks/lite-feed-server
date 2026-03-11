# Utilisation d'une image python légère
FROM python:3.11-slim

# Répertoire de travail
WORKDIR /app

# Copie des dépendances et installation
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du code source
COPY main.py .

# Variable d'environnement pour l'API key (à définir au lancement)
ENV API_KEY=""

# Exposition du port
EXPOSE 8000

# Commande de lancement
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]