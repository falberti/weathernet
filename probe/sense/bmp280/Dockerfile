FROM python:3.9.20-alpine3.20

LABEL org.opencontainers.image.authors="francesco.albe@gmail.com"

# Requirements for python script
COPY requirements.txt /opt/app/requirements.txt
WORKDIR /opt/app
RUN pip install -r requirements.txt

# Copy script
COPY bmp280.py /opt/app/bmp280.py

