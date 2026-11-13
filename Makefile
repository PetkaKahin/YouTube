.DEFAULT_GOAL := help
.NOTPARALLEL:

DOCKER_RUN := ./docker/run
override EMAIL := $(value EMAIL)
override ROLE := $(value ROLE)
export EMAIL ROLE ROLE_ACTION

.PHONY: help init up down build start stop restart logs ps test check audit context-check lock shell shell-front role

ifneq ($(filter role,$(MAKECMDGOALS)),)
ifneq ($(firstword $(MAKECMDGOALS)),role)
$(error Используйте make role get или make role set отдельно от других целей)
endif
ifneq ($(words $(MAKECMDGOALS)),2)
$(error Используйте make role get EMAIL=адрес или make role set EMAIL=адрес ROLE=роль)
endif
ifeq ($(filter $(word 2,$(MAKECMDGOALS)),get set),)
$(error Неизвестная подкоманда role: допустимы get и set)
endif
.PHONY: get set
get set: ; @:
endif

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

test: ## Запустить все тесты бэкенда в работающем dev
	$(DOCKER_RUN) dev exec -T python pytest

role: override ROLE_ACTION := $(word 2,$(MAKECMDGOALS))
role: ## Роль аккаунта: make role get EMAIL=адрес / make role set EMAIL=адрес ROLE=роль
	@if [ -z "$$EMAIL" ]; then \
		printf '%s\n' 'Укажите EMAIL.'; exit 2; \
	fi
	@if [ "$$ROLE_ACTION" = get ]; then \
		$(DOCKER_RUN) dev exec -T python python -m app.cli role get -- "$$EMAIL"; \
	else \
		if [ -z "$$ROLE" ]; then printf '%s\n' 'Укажите ROLE.'; exit 2; fi; \
		$(DOCKER_RUN) dev exec -T python python -m app.cli role set -- "$$EMAIL" "$$ROLE"; \
	fi

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
