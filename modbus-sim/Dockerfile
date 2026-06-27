FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sim.py .

EXPOSE 5020 5021
CMD ["python", "sim.py"]
