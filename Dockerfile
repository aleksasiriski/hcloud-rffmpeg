FROM python:latest

WORKDIR /usr/src/app

COPY . .

RUN pip install --no-cache-dir -r requirements.txt

VOLUME /config

ENTRYPOINT [ "python", "./hcloud-rffmpeg.py" ]