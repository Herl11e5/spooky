# Spooky Development Makefile
# Comandi rapidi per setup, build, run, test

.PHONY: help setup setup-mac setup-rpi ollama run debug test diagnose clean

SHELL := /bin/bash
PYTHON := python3
CORE_DIR := robot-core
VENV := $(CORE_DIR)/venv
BIN := $(VENV)/bin

help:
	@echo "🕷️  Spooky Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make setup-mac      → Installa dipendenze su macOS"
	@echo "  make setup-rpi      → Installa su Raspberry Pi"
	@echo "  make ollama         → Setup Ollama + modelli"
	@echo ""
	@echo "Sviluppo:"
	@echo "  make run            → Avvia Spooky normale"
	@echo "  make debug          → Avvia con debug logging"
	@echo "  make stop           → Arresta Spooky"
	@echo ""
	@echo "Diagnostica:"
	@echo "  make diagnose       → Verifica installazione"
	@echo "  make test           → Esegui unit tests"
	@echo ""
	@echo "Maintenance:"
	@echo "  make clean          → Rimuovi .pyc, cache, logs"
	@echo "  make venv-clean     → Ricreo virtual environment"
	@echo ""

# ── Setup ──────────────────────────────────────────────────────────────

setup-mac:
	@echo "🍎 Setup macOS..."
	@bash $(CORE_DIR)/scripts/install_mac.sh

setup-rpi:
	@echo "🦾 Setup Raspberry Pi..."
	@bash $(CORE_DIR)/scripts/install_rpi.sh

ollama:
	@echo "🤖 Setup Ollama..."
	@bash $(CORE_DIR)/scripts/setup_ollama.sh

# ── Run & Debug ────────────────────────────────────────────────────────

run: _ensure-venv
	@echo "🕷️  Avvio Spooky..."
	@cd $(CORE_DIR) && $(BIN)/python main.py

debug: _ensure-venv
	@echo "🐛 Avvio Spooky (DEBUG)..."
	@cd $(CORE_DIR) && $(BIN)/python main.py --debug 2>&1 | head -100

stop:
	@echo "⏹  Arresto Spooky..."
	@pkill -f "python.*main.py" || echo "Nessuna istanza in esecuzione"

# ── Diagnostica ────────────────────────────────────────────────────────

diagnose: _ensure-venv
	@echo "🔍 Diagnostica sistema..."
	@cd $(CORE_DIR) && $(BIN)/python scripts/diagnose_system.py

test: _ensure-venv
	@echo "🧪 Esecuzione test..."
	@cd $(CORE_DIR) && $(BIN)/python -m pytest tests/ -v 2>/dev/null || \
		echo "Installa pytest: pip install pytest"

# ── Manutenzione ───────────────────────────────────────────────────────

clean:
	@echo "🧹 Pulizia cache..."
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@rm -rf $(CORE_DIR)/logs/*.log 2>/dev/null || true
	@echo "✅ Pulito"

venv-clean:
	@echo "♻️  Ricreo virtual environment..."
	@rm -rf $(VENV)
	@$(PYTHON) -m venv $(VENV)
	@$(BIN)/pip install --upgrade pip setuptools wheel -q
	@$(BIN)/pip install -r $(CORE_DIR)/requirements.txt -q
	@echo "✅ venv ricreato"

# ── Internal ───────────────────────────────────────────────────────────

_ensure-venv:
	@if [ ! -f $(BIN)/python ]; then \
		echo "❌ venv non trovato. Esegui: make setup-mac"; \
		exit 1; \
	fi

.DEFAULT_GOAL := help
