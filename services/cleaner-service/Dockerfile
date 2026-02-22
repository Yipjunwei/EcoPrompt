FROM python:3.12-slim

# Set working directory inside the container
WORKDIR /app

# Copy and install dependencies first (Docker caches this layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the service code
COPY . .

# Tell Docker which port this service listens on
EXPOSE 5001

# Command to run when the container starts
CMD ["python", "cleaner.py"]
