FROM python:3.12-slim

WORKDIR /opt/presidio

# Install full Presidio stack + Flask
RUN pip install --upgrade pip --quiet && \
    pip install --no-cache-dir \
    flask==3.1.3 \
    flask-cors==5.0.1 \
    presidio-analyzer==2.2.364 \
    presidio-anonymizer==2.2.364 \
    spacy==3.8.14

# Download spaCy model for Presidio NLP
RUN python3 -m spacy download en_core_web_sm --quiet

# Copy the service
COPY presidio_service.py .

EXPOSE 8090

CMD ["python3", "presidio_service.py", "--daemon"]
