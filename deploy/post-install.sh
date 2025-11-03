#!/usr/bin/env bash
set -euo pipefail

APP_USER=integrasys
APP_DIR=/srv/integrasys
REPO_URL=https://github.com/CarmiIsGod/Integrasys.git
GIT_REF=main  # ajusta al tag o rama que quieras desplegar

# 1) usuario y carpetas
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  sudo useradd -m -s /bin/bash "$APP_USER"
fi
sudo mkdir -p "$APP_DIR"/{app,run,static,media}
sudo chown -R "$APP_USER":www-data "$APP_DIR"

# 2) dependencias del sistema
sudo apt update
sudo apt install -y python3-venv python3-pip git nginx postgresql postgresql-contrib

# 3) base de datos (solo si usarás Postgres)
if ! sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='integrasys'" | grep -q 1; then
  sudo -u postgres psql -c "CREATE USER integrasys WITH PASSWORD 'CAMBIA_PASS';"
fi
if ! sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='integrasys'" | grep -q 1; then
  sudo -u postgres createdb integrasys
  sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE integrasys TO integrasys;"
fi

# 4) clonar app y entorno virtual
sudo -u "$APP_USER" bash -lc "
  set -e
  cd $APP_DIR/app
  if [ ! -d .git ]; then
    git clone $REPO_URL .
  fi
  git fetch --all
  git checkout $GIT_REF
  python3 -m venv $APP_DIR/venv
  $APP_DIR/venv/bin/pip install --upgrade pip
  $APP_DIR/venv/bin/pip install -r requirements.txt
"

# 5) validar .env
if [ ! -f "$APP_DIR/.env" ]; then
  echo "Falta $APP_DIR/.env — crea el archivo a partir de deploy/.env.example.prod antes de continuar."
  exit 1
fi

# 6) migraciones y estáticos
sudo -u "$APP_USER" bash -lc "
  set -e
  cd $APP_DIR/app
  $APP_DIR/venv/bin/python manage.py migrate --noinput
  $APP_DIR/venv/bin/python manage.py collectstatic --noinput
"

# 7) systemd y nginx
sudo cp "$APP_DIR"/app/deploy/gunicorn.integrasys.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gunicorn.integrasys

sudo cp "$APP_DIR"/app/deploy/nginx.integrasys.conf /etc/nginx/sites-available/integrasys.conf
sudo ln -sf /etc/nginx/sites-available/integrasys.conf /etc/nginx/sites-enabled/integrasys.conf
sudo nginx -t
sudo systemctl reload nginx

echo "Deploy inicial listo."
