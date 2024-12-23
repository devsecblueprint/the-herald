FROM cgr.dev/chainguard/python:latest-dev

RUN pip install awslambdaric

COPY ./src .
COPY requirements.txt .
RUN pip install -r requirements.txt

CMD ["handler.main"]