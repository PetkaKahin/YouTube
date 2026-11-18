#!/usr/bin/env bash
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd -- "$project_dir"
install_stage='проверка окружения'
trap 'printf "Установка остановлена: %s. Исправьте ошибку и повторите bash install.sh.\n" "$install_stage" >&2' ERR

if [[ $# != 0 ]]; then
    printf 'Использование: bash install.sh\n' >&2
    exit 2
fi
if [[ ! -r /etc/os-release ]]; then
    printf 'Нужна Ubuntu 24.04 LTS или 26.04 LTS.\n' >&2
    exit 1
fi
# This system-owned file contains the OS identity; project .env is never sourced.
# shellcheck source=/dev/null
source /etc/os-release
case "${ID:-}:${VERSION_ID:-}" in
    ubuntu:24.04) ubuntu_suite=noble ;;
    ubuntu:26.04) ubuntu_suite=resolute ;;
    *) printf 'Поддерживаются Ubuntu 24.04 LTS и 26.04 LTS.\n' >&2; exit 1 ;;
esac
if [[ ! -f docker/run || ! -f .env.example || ! -f docker-compose.dev.yml ]]; then
    printf 'Распакуйте весь архив проекта перед установкой.\n' >&2
    exit 1
fi

if [[ $EUID != 0 ]]; then
    command -v sudo >/dev/null || {
        printf 'Для установки Docker нужны root или sudo. Запустите sudo bash install.sh.\n' >&2
        exit 1
    }
    exec sudo -- bash "$project_dir/install.sh"
fi
install_user=${SUDO_USER:-root}
if ! id "$install_user" >/dev/null 2>&1; then
    printf 'Не удалось определить пользователя проекта.\n' >&2
    exit 1
fi
as_owner() {
    if [[ $install_user == root ]]; then
        "$@"
    else
        runuser -u "$install_user" -- "$@"
    fi
}
if ! as_owner test -w "$project_dir"; then
    printf 'Пользователь %s должен иметь право записи в %s.\n' "$install_user" "$project_dir" >&2
    exit 1
fi

while true; do
    printf 'Использовать сидеры для заполнения тестовыми данными? (Y/n) '
    if ! IFS= read -r seed_answer; then
        printf '\nОтвет не получен, установка отменена.\n' >&2
        exit 1
    fi
    case "$seed_answer" in
        ''|[Yy]|[Yy][Ee][Ss]) use_seed=yes; break ;;
        [Nn]|[Nn][Oo]) use_seed=no; break ;;
        *) printf 'Введите Y или n.\n' ;;
    esac
done

install_stage='установка системных зависимостей'
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl make util-linux

docker_ready() {
    as_owner docker info >/dev/null 2>&1 \
        && as_owner docker compose version >/dev/null 2>&1 \
        && as_owner docker buildx version >/dev/null 2>&1
}

group_added=no
if ! docker_ready; then
    install_stage='установка Docker Engine, Compose и Buildx'
    # Keep an existing complete Engine; missing group membership needs no reinstall.
    if ! { command -v docker >/dev/null && docker compose version >/dev/null 2>&1 \
        && docker buildx version >/dev/null 2>&1; }; then
        conflicts=()
        for package in docker.io docker-compose docker-compose-v2 docker-doc docker-buildx \
            podman-docker containerd runc; do
            if [[ $(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || :) == 'install ok installed' ]]; then
                conflicts+=("$package")
            fi
        done
        if [[ ${#conflicts[@]} != 0 ]]; then
            printf 'Обнаружены конфликтующие пакеты: %s. Удалите их перед установкой Docker CE.\n' \
                "${conflicts[*]}" >&2
            exit 1
        fi
        install -m 0755 -d /etc/apt/keyrings
        curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
            --connect-timeout 10 --max-time 60 --retry 3 \
            https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
        chmod a+r /etc/apt/keyrings/docker.asc
        architecture=$(dpkg --print-architecture)
        cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $ubuntu_suite
Components: stable
Architectures: $architecture
Signed-By: /etc/apt/keyrings/docker.asc
EOF
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
            docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
    systemctl enable --now docker
    if [[ $install_user != root ]] && ! as_owner docker info >/dev/null 2>&1; then
        usermod -aG docker "$install_user"
        group_added=yes
    fi
fi

install_stage='проверка Docker'
docker_ready || { printf 'Docker недоступен пользователю %s.\n' "$install_user" >&2; exit 1; }
compose_version=$(as_owner docker compose version --short)
if [[ ! $compose_version =~ ^v?([0-9]+)\.([0-9]+) ]] \
    || (( BASH_REMATCH[1] < 2 || (BASH_REMATCH[1] == 2 && BASH_REMATCH[2] < 24) )); then
    printf 'Требуется Docker Compose 2.24 или новее.\n' >&2
    exit 1
fi

install_stage='подготовка конфигурации, сборка и миграции'
chmod +x docker/run
as_owner bash ./docker/run setup
if [[ $use_seed == yes ]]; then
    install_stage='заполнение тестовыми данными'
    as_owner make seed SEED_MODE=dev
fi
install_stage='проверка HTTP'
binding=$(as_owner bash ./docker/run dev port nginx 8080)
binding=${binding%%$'\n'*}
http_port=${binding##*:}
[[ $http_port =~ ^[0-9]+$ ]] || { printf 'Не удалось определить HTTP-порт.\n' >&2; exit 1; }
curl --fail --silent --show-error --max-time 15 --retry 3 --retry-connrefused \
    "http://localhost:$http_port/api/v1/health/ready" >/dev/null
printf '\nПриложение готово: http://localhost:%s\n' "$http_port"
if [[ $use_seed == yes ]]; then
    printf 'Демоаккаунты и пароль: README.md, раздел «Тестовые и демонстрационные данные».\n'
fi
if [[ $group_added == yes ]]; then
    printf 'Для команд make без sudo откройте новый сеанс пользователя %s.\n' "$install_user"
fi
