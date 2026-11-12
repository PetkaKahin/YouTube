.DEFAULT_GOAL := help
.NOTPARALLEL:

DOCKER_RUN := ./docker/run

.PHONY: help init up down build start stop restart logs ps test check audit context-check lock shell shell-front

help: ## Показать команды dev
	@awk 'BEGIN {FS = ":.*## "; print "Команды dev:"} /^[a-zA-Z][a-zA-Z0-9_-]*:.*## / {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

init: ## Подготовить .env, secrets и недостающие lock-файлы
	$(DOCKER_RUN) init

up: ## Собрать и запустить dev, дождаться готовности
	$(DOCKER_RUN) dev up --build -d --wait

down: ## Остановить и удалить контейнеры dev, сохранив тома
	$(DOCKER_RUN) dev down

build: ## Собрать образы dev
	$(DOCKER_RUN) dev build

start: ## Поднять dev без принудительной пересборки
	$(DOCKER_RUN) dev up -d --wait

stop: ## Остановить dev, сохранив контейнеры
	$(DOCKER_RUN) dev stop

restart: ## Перезапустить dev и дождаться готовности
	$(DOCKER_RUN) dev restart
	$(DOCKER_RUN) dev up -d --wait

logs: ## Смотреть последние 100 строк и новые логи
	$(DOCKER_RUN) dev logs --follow --tail 100

ps: ## Показать состояние всех контейнеров dev
	$(DOCKER_RUN) dev ps --all

test: ## Запустить pytest в работающем dev
	$(DOCKER_RUN) dev exec -T python pytest

check: ## Выполнить проверки кода, зависимостей и HTTP
	$(DOCKER_RUN) check

audit: ## Проверить dev на утечки секретов
	$(DOCKER_RUN) audit dev

context-check: ## Проверить контексты сборки через BuildKit
	$(DOCKER_RUN) context-check

lock: ## Обновить lock-файлы внутри Docker
	$(DOCKER_RUN) lock

shell: ## Открыть shell контейнера Python
	$(DOCKER_RUN) dev exec python sh

shell-front: ## Открыть shell контейнера Node
	$(DOCKER_RUN) dev exec node sh
