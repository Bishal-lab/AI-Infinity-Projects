# Campaign Communication Effectiveness Dashboard
#
# Build on a connected machine, then transfer the image to the air-gapped host:
#
#   docker build -f docker/Dockerfile -t comms-dashboard:1.0 .
#   docker save comms-dashboard:1.0 | gzip > comms-dashboard-1.0.tar.gz
#   # …copy across, then on the target host:
#   docker load < comms-dashboard-1.0.tar.gz
#
# If a wheelhouse/ directory is present it is used instead of PyPI, so the
# image can also be built on a machine with no internet access.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

COPY requirements.txt ./
# The wildcard makes the wheelhouse optional: present -> offline install,
# absent -> normal install from PyPI at build time.
COPY requirements.txt wheelhous[e] ./wheelhouse/
RUN if [ -n "$(ls -A ./wheelhouse 2>/dev/null | grep -v requirements.txt)" ]; then \
        pip install --no-index --find-links=./wheelhouse -r requirements.txt; \
    else \
        pip install -r requirements.txt; \
    fi && rm -rf ./wheelhouse

COPY comms_dashboard/ ./comms_dashboard/
COPY app/ ./app/
COPY sql/ ./sql/
COPY scripts/ ./scripts/
COPY config/ ./config/
COPY .streamlit/ ./.streamlit/
COPY data/samples/ ./data/samples/

# The pseudonymisation salt must never travel inside the image: it is shipped to
# the air-gapped host as a `docker save` tarball, and leaking the salt makes
# every hashed identifier reversible. .dockerignore already keeps it out of the
# build context; this fails the build loudly if it ever gets in anyway — through
# a context assembled by hand, an older .dockerignore, or a build tool that does
# not honour it.
RUN test ! -e config/secret_salt \
    || (echo "BUILD REFUSED: config/secret_salt is in the build context." \
     && echo "Check .dockerignore — the salt belongs on the host, not in the image." \
     && exit 1)

# Run unprivileged. The data directory is a mount point, so it must be writable
# by this user — see the note in docker-compose.yml about matching ownership.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p data/inbox data/archive data/rejected data/warehouse data/exports \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health').status==200 else 1)"

ENTRYPOINT ["streamlit", "run", "app/main.py", \
            "--server.address=0.0.0.0", "--server.port=8501", \
            "--server.headless=true"]
