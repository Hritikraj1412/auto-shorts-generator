# Use lightweight Python image
FROM python:3.11-slim

# Install system tools like FFmpeg for video processing
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all your project files to the server
COPY . .

# Create folders for uploads and clips
RUN mkdir -p uploads static/clips

# Command to run your app dynamically on Render's Port
CMD ["sh", "-c", "flask run --host=0.0.0.0 --port=${PORT:-5000}"]