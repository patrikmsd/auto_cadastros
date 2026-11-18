"""OCR do Mistral (API, cobrado por página) com registro do custo. Chave: API_MISTRAL no .env.

A imagem vai como está: quem chama já manda na rotação certa (o Mistral não desvira foto de ponta-cabeça).
"""
import base64
import json
import urllib.error
import urllib.request

import cv2

from . import config, custo_ia
from .ocr_teste import ler_imagem

MODELO = config.ENV.get("mistral_modelo", "mistral-ocr-latest")


def ocr_mistral(tarefa, img, esquema=None, instrucao=None, modelo=None):
    """img (array do OpenCV) → (texto em markdown, dict extraído ou None). Com esquema, o Mistral também preenche os
    campos (extração estruturada, preço maior). Mostra e registra o custo."""
    if isinstance(img, (str, bytes)) or hasattr(img, "suffix"):
        img = ler_imagem(img)
    b64 = base64.b64encode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()).decode()
    corpo = {"model": modelo or MODELO, "document": {"type": "image_url", "image_url": f"data:image/jpeg;base64,{b64}"}}
    if esquema:
        corpo["document_annotation_format"] = {"type": "json_schema",
                                               "json_schema": {"name": "campos", "schema": esquema, "strict": False}}
        if instrucao:
            corpo["document_annotation_prompt"] = instrucao
    req = urllib.request.Request("https://api.mistral.ai/v1/ocr", data=json.dumps(corpo).encode(),
                                 headers={"Authorization": f"Bearer {config.ENV.get('api_mistral', '')}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            resposta = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Mistral respondeu {e.code}: {e.read().decode()[:400]}")
    paginas = (resposta.get("usage_info") or {}).get("pages_processed", 1)
    custo_ia.registrar_paginas(tarefa, resposta.get("model", corpo["model"]), paginas, estruturado=bool(esquema))
    texto = "\n".join(p.get("markdown", "") for p in resposta.get("pages", []))
    anotacao = resposta.get("document_annotation")
    return texto, (json.loads(anotacao) if isinstance(anotacao, str) and anotacao else anotacao)
