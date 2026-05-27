#!/bin/bash
set -e

echo ""
echo "====================================="
echo "  CV Job Matcher – Setup (MEDWING)"
echo "====================================="
echo ""

# Python check
if ! command -v python3 &>/dev/null; then
  echo "❌  Python3 nicht gefunden. Bitte installieren: https://www.python.org"
  exit 1
fi

echo "✅  Python3 gefunden: $(python3 --version)"

# pip install
echo ""
echo "📦  Installiere Abhängigkeiten …"
python3 -m pip install --upgrade pip -q
python3 -m pip install -r requirements.txt -q

echo ""
echo "✅  Alle Pakete installiert."
echo ""
echo "🚀  Starten mit:"
echo "     python3 main.py"
echo ""
