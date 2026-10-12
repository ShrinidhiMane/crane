# One image for the API, the workers and the janitor; the command picks the role.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./
COPY crane ./crane
RUN pip install . && useradd --uid 10001 --no-create-home crane

USER 10001
EXPOSE 8080
ENTRYPOINT ["python", "-m", "crane"]
CMD ["api"]
