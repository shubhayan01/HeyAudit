# bd-auto — automated technical SEO audit + client growth deck.
#
# Playwright drives a headless Chromium (for the checks AND for rendering the
# PDF via page.pdf()), which needs a browser binary plus a long list of system
# libraries. Render's native Python runtime can't apt-install those, so we ship
# a Docker image: we install the Python deps, then let Playwright pull the
# matching Chromium build together with its OS dependencies.

FROM python:3.12-slim

# Keep Python output unbuffered so logs stream straight to Render, and give
# Playwright a stable place to cache the browser.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Install Python dependencies first so Docker can cache this layer.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Install Chromium and all the OS libraries it needs. --with-deps runs apt-get,
# which works here because the build runs as root.
RUN python -m playwright install --with-deps chromium

# Copy the application code. Secrets (.groq_key / .seoptimer_key) are excluded
# via .dockerignore — supply them at runtime as GROQ_API_KEY / SEOPTIMER_API_KEY.
COPY . .

# Render injects $PORT; the server reads it and binds 0.0.0.0. 8000 is only the
# local default.
ENV PORT=8000
EXPOSE 8000

CMD ["python", "server.py"]
