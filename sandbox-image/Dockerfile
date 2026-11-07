ARG PYTHON_BASE_IMAGE
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.lock /tmp/researchos-requirements.lock
RUN if grep -Eq '^[[:space:]]*[^#[:space:]]' /tmp/researchos-requirements.lock; then \
      python -m pip install --require-hashes -r /tmp/researchos-requirements.lock; \
    fi \
    && rm -f /tmp/researchos-requirements.lock

WORKDIR /workspace
