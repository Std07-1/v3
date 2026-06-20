#!/usr/bin/env bash
# Конвертація NLLB-200 distilled-600M у формат CTranslate2 з int8-квантизацією.
# Запуск один раз на машині перекладу. Потребує requirements-nllb.txt.
#
#   bash scripts/convert_nllb.sh [OUTPUT_DIR]
#
set -euo pipefail

MODEL_ID="facebook/nllb-200-distilled-600M"
OUT_DIR="${1:-models/nllb-200-distilled-600M-int8}"

if [ -d "$OUT_DIR" ]; then
  echo "Модель уже сконвертована: $OUT_DIR"
  exit 0
fi

echo "Конвертую $MODEL_ID -> $OUT_DIR (int8)..."
ct2-transformers-converter \
  --model "$MODEL_ID" \
  --quantization int8 \
  --output_dir "$OUT_DIR"

echo "Готово. Встанови TRANSLATE_ENGINE=nllb та TRANSLATE_NLLB_MODEL_DIR=$OUT_DIR"
