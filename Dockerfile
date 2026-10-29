FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Port configuration (Hugging Face Spaces uses 7860, Render uses 10000/8080)
ENV PORT=7860
ENV HOST=0.0.0.0
EXPOSE 7860

CMD ["python", "-m", "jiosaavn"]
