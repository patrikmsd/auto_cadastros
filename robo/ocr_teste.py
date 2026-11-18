"""Teste 2: OCR grátis e local (RapidOCR = modelos do PaddleOCR, sem enviar nada para fora).

Lê as fotos de testes/amostras/<PLACA>, procura QR Code, CPF, placa, chassi e Renavam,
e compara com os campos que o explorar.py salvou do card.

Uso:  python -m robo.ocr_teste --placa ABC1D23
"""
import argparse
import base64
import json
import re
import urllib.request

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

from . import config

PADROES = {
    "placa": r"\b[A-Z]{3}-?\d[A-Z0-9]\d{2}\b",
    # 11 caracteres + 6 dígitos de série; sem \b no fim porque o CRLV pode trazer sufixo ("...103772REM" = remarcado)
    "chassi": r"(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{11}\d{6}",
    "cpf": r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b",
    "renavam": r"\b\d{9,11}\b",
}


def ler_imagem(caminho):
    """Abre a foto (imread falha com acento no caminho). PNG transparente vai para fundo branco: senão o fundo fica
    preto e o OCR só enxerga os rótulos, como no CRLV exportado do app da CDT."""
    img = cv2.imdecode(np.fromfile(str(caminho), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        alfa = img[:, :, 3:4] / 255.0
        return (img[:, :, :3] * alfa + 255 * (1 - alfa)).astype(np.uint8)
    return img


def cpf_valido(cpf):
    d = re.sub(r"\D", "", cpf)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for n in (9, 10):
        soma = sum(int(d[i]) * (n + 1 - i) for i in range(n))
        if (soma * 10 % 11) % 10 != int(d[n]):
            return False
    return True


def renavam_valido(renavam):
    d = re.sub(r"\D", "", renavam).zfill(11)
    soma = sum(int(a) * int(b) for a, b in zip(d[:10], "3298765432"))
    return (soma * 10 % 11) % 10 == int(d[10])


class GoogleVision:
    """Cloud Vision (DOCUMENT_TEXT_DETECTION) com a chave api_ocr_google do .env. Envia a imagem ao Google."""

    URL = "https://vision.googleapis.com/v1/images:annotate"

    def __init__(self):
        self.chave = config.ENV.get("api_ocr_google")
        if not self.chave:
            raise SystemExit("Falta api_ocr_google no .env")

    def __call__(self, img):
        ok, jpg = cv2.imencode(".jpg", img)
        corpo = json.dumps({"requests": [{
            "image": {"content": base64.b64encode(jpg.tobytes()).decode()},
            "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
            "imageContext": {"languageHints": ["pt"]},
        }]}).encode()
        req = urllib.request.Request(self.URL, data=corpo, method="POST",
                                     headers={"Content-Type": "application/json", "X-goog-api-key": self.chave})
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())["responses"][0]
        if "error" in resp:
            raise RuntimeError(resp["error"].get("message"))
        texto = resp.get("fullTextAnnotation", {}).get("text", "")
        return [[None, linha, None] for linha in texto.splitlines() if linha.strip()], None


CAMPOS_DOC = ["nome", "cpf", "rg", "data_nascimento", "numero_cnh", "categoria_cnh", "validade_cnh", "placa", "chassi",
              "renavam", "marca_modelo", "ano_fabricacao", "ano_modelo", "combustivel", "cor", "exercicio_licenciamento",
              "proprietario", "endereco", "cep", "data_documento", "quilometragem", "numero_motor"]

ESQUEMA_DOC = {
    "type": "object",
    "properties": {
        "tipo_documento": {"type": "string", "enum": ["CNH", "RG", "CPF", "CRLV", "NOTA_FISCAL", "COMPROVANTE_ENDERECO",
                                                      "CHASSI_GRAVADO", "PAINEL_KM", "MOTOR", "VEICULO", "OUTRO", "NENHUM"]},
        "documento_visivel": {"type": "boolean"},
        "corresponde_ao_tipo_da_foto": {"type": "boolean"},
        "legivel": {"type": "string", "enum": ["sim", "parcial", "nao"]},
        "problemas": {"type": "array", "items": {"type": "string"}},
        "campos": {"type": "object", "properties": {c: {"type": "string"} for c in CAMPOS_DOC},
                   "required": CAMPOS_DOC, "additionalProperties": False},
    },
    "required": ["tipo_documento", "documento_visivel", "corresponde_ao_tipo_da_foto", "legivel", "problemas", "campos"],
    "additionalProperties": False,
}


class ClaudeVisao:
    """SOMENTE TESTE (provisório até escolhermos o OCR definitivo).

    Claude lê a foto e devolve os campos já organizados (modelo: claude_modelo do .env). Envia a imagem à Anthropic.
    """

    def __init__(self):
        import anthropic

        chave = config.ENV.get("anthropic_api_key")
        self.client = anthropic.Anthropic(api_key=chave) if chave else anthropic.Anthropic()
        self.modelo = config.ENV.get("claude_modelo", "claude-opus-5-5")

    def __call__(self, img, tipo_foto):
        lado = max(img.shape[:2])
        if lado > 1568:  # acima disso a API reduz a imagem de qualquer jeito; enviar menor gasta menos
            escala = 1568 / lado
            img = cv2.resize(img, (int(img.shape[1] * escala), int(img.shape[0] * escala)))
        jpg = base64.standard_b64encode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()).decode()
        resposta = self.client.beta.messages.create(
            model=self.modelo,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"format": {"type": "json_schema", "schema": ESQUEMA_DOC}},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": jpg}},
                {"type": "text", "text": (
                    f"Foto de uma vistoria veicular. No sistema, ela está marcada como: \"{tipo_foto}\".\n"
                    "Diga que documento aparece de fato, se ele corresponde a essa marcação e se está legível. "
                    "Transcreva os campos exatamente como estão escritos (placa e chassi sem espaços). "
                    "Campo que não aparece ou não dá para ler: deixe vazio, nunca adivinhe. "
                    "Em problemas, liste o que impede a conferência (reflexo, cortado, desfocado, sem documento...).")},
            ]}],
        )
        if resposta.stop_reason in ("refusal", "max_tokens"):
            raise SystemExit(f"Claude não concluiu a leitura ({resposta.stop_reason})")
        return json.loads(next(b.text for b in resposta.content if b.type == "text"))


def ultimo_card():
    pastas = sorted(p for p in config.PASTA_SAIDA.glob("*") if (p / "card_cotacoes.json").exists())
    return pastas[-1] if pastas else None


def valores_do_card(pasta):
    campos = json.loads((pasta / "card_cotacoes.json").read_text(encoding="utf-8"))
    achados = {}
    for c in campos:
        rot = c["rotulo"].lower()
        for chave in ("placa", "chassi", "renavam"):
            if rot.startswith(chave) and c["valor"]:
                achados[chave] = c["valor"].upper().replace("-", "")
    return achados


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--placa", required=True)
    ap.add_argument("--todas", action="store_true", help="roda em todas as fotos, não só documentos")
    ap.add_argument("--motor", choices=["rapidocr", "google", "claude"], default="rapidocr",
                    help="rapidocr = grátis e local (padrão) · google = Cloud Vision · claude = SÓ TESTE, IA lê e já "
                         "devolve os campos (google e claude enviam as imagens para fora)")
    args = ap.parse_args()

    pasta_fotos = config.PASTA_AMOSTRAS / args.placa
    fotos = sorted(f for f in pasta_fotos.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    indice = pasta_fotos / "fotos.json"
    rotulos = {f["arquivo"]: f["rotulo"] for f in json.loads(indice.read_text(encoding="utf-8"))} if indice.exists() else {}
    if not args.todas:  # o nome da foto já diz o tipo: só os documentos interessam ao OCR
        fotos = [f for f in fotos if re.search(r"documento|cnh|crlv|comprovante|chassi|motor frontal|painel com registro|selfie frontal com placa", rotulos.get(f.name, f.name), re.I)]
    pasta_card = ultimo_card()
    card = valores_do_card(pasta_card) if pasta_card else {}
    print(f"{len(fotos)} fotos · campos do card: {card or 'não encontrados'}")

    ocr = {"google": GoogleVision, "claude": ClaudeVisao}.get(args.motor, RapidOCR)()
    qr = cv2.QRCodeDetector()
    relatorio = []
    for foto in fotos:
        img = ler_imagem(foto)
        if img is None:
            continue
        texto_qr, _, _ = qr.detectAndDecode(img)
        leitura_ia = None
        if args.motor == "claude":
            leitura_ia = ocr(img, rotulos.get(foto.name, foto.name))
            c = leitura_ia["campos"]
            linhas = [f"{k}: {v}" for k, v in c.items() if v]
            texto = " ".join(str(v) for v in c.values() if v).upper()
        else:
            # chassi gravado e documento fotografado de lado: testa 0°, 90° e 270° e fica com a leitura mais longa
            giros = [img, cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE), cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)]
            linhas = []
            for g in giros:
                resultado, _ = ocr(g)
                candidatas = [r[1] for r in (resultado or [])]
                if sum(map(len, candidatas)) > sum(map(len, linhas)):
                    linhas = candidatas
            texto = " ".join(linhas).upper()
        texto = texto + " " + texto.replace(" ", "")  # chassi costuma sair quebrado em pedaços
        encontrados = {k: sorted(set(re.findall(p, texto))) for k, p in PADROES.items()}
        encontrados["cpf"] = [c for c in encontrados["cpf"] if cpf_valido(c)]
        encontrados["renavam"] = [r for r in encontrados["renavam"] if renavam_valido(r)]
        conferencia = {}
        for chave, valor_card in card.items():
            lidos = [v.replace("-", "") for v in encontrados.get(chave, [])]
            if chave == "chassi" and not lidos:  # tolera O↔0, I↔1, Q↔0 lidos errado
                normal = texto.replace("O", "0").replace("I", "1").replace("Q", "0")
                lidos = ["(aprox) " + valor_card] if valor_card in normal.replace(" ", "") else []
                if lidos:
                    conferencia[chave] = "OK (com correção O↔0 / I↔1)"
                    continue
            if lidos:
                conferencia[chave] = "OK" if valor_card in lidos else f"DIVERGENTE (card {valor_card} × foto {lidos})"
        relatorio.append({"foto": foto.name, "tipo": rotulos.get(foto.name), "qr": texto_qr or None,
                          "encontrados": encontrados, "conferencia": conferencia, "texto": linhas, "leitura_ia": leitura_ia})
        if leitura_ia:  # não imprime dados pessoais: só o diagnóstico da foto
            print(f"- {rotulos.get(foto.name, foto.name)}: {leitura_ia['tipo_documento']} · documento visível: "
                  f"{'sim' if leitura_ia['documento_visivel'] else 'NÃO'} · legível: {leitura_ia['legivel']} · "
                  f"bate com o tipo da foto: {'sim' if leitura_ia['corresponde_ao_tipo_da_foto'] else 'NÃO'} · "
                  f"{conferencia or ''} {('· problemas: ' + '; '.join(leitura_ia['problemas'])) if leitura_ia['problemas'] else ''}")
        else:
            resumo = {k: v for k, v in encontrados.items() if v}
            print(f"- {rotulos.get(foto.name, foto.name)}: {resumo or 'nada reconhecido'} {conferencia or ''}{' · QR lido' if texto_qr else ''}")

    destino = pasta_fotos / f"ocr_relatorio_{args.motor}.json"
    destino.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRelatório em {destino}")


if __name__ == "__main__":
    main()
