FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY serveur.py acces.py donnees.py garmin.py strava.py vap.py ./
RUN useradd --create-home appli
USER appli
ENV PORT=8000
EXPOSE 8000
CMD ["python", "serveur.py"]
