FROM cgr.dev/chainguard/python:latest-dev as build

WORKDIR /app
USER root
RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install Lambda RIC and dependencies
COPY requirements.txt .
RUN pip install \
    --no-cache-dir \
    awslambdaric \
    -r requirements.txt

# Create entry script in build stage
RUN echo '#!/bin/sh\nexec python -m awslambdaric "$@"' > /app/entry.sh && \
    chmod +x /app/entry.sh
RUN ls -la /app/.venv/bin

FROM cgr.dev/chainguard/python:latest

WORKDIR /var/task
COPY --from=build /app/.venv /var/task/.venv
COPY --from=build /app/entry.sh /var/task/entry.sh
COPY ./src/* .
ENV PATH="/var/task/.venv/bin:$PATH"
ENV PYTHONPATH="/var/task"

ENTRYPOINT [ "/var/task/.venv/bin/python", "-m", "awslambdaric" ]
CMD [ "handler.main" ]
