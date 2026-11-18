"""Chamada ao ChatGPT (API da OpenAI) com resposta em JSON e registro do custo. Chave: API_GPT no .env.

Teste:  python -m robo.gpt                  (lê chassi e painel do card de teste PKD6E70)
        python -m robo.gpt --modelo gpt-4.1-mini
"""
import argparse
import base64
import json
import time
import urllib.error
import urllib.request

from . import config, custo_ia

MODELO = config.ENV.get("gpt_modelo", "gpt-6-luna")


TENTATIVAS = 4  # erro passageiro (limite de uso, servidor da OpenAI, rede): tenta de novo com espera crescente
PASSAGEIROS = (408, 409, 429, 500, 502, 503, 504)


def _enviar(tarefa, corpo):
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(corpo).encode(),
                                 headers={"Authorization": f"Bearer {config.ENV.get('api_gpt', '')}",
                                          "Content-Type": "application/json"})
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                resposta = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            detalhe = e.read().decode(errors="replace")[:400]
            if e.code not in PASSAGEIROS or tentativa == TENTATIVAS:
                raise SystemExit(f"OpenAI respondeu {e.code}: {detalhe}")
            espera = float(e.headers.get("Retry-After") or 0) or 5 * 2 ** (tentativa - 1)
            print(f"   ⏳ {tarefa}: OpenAI respondeu {e.code}, nova tentativa em {espera:.0f}s ({tentativa}/{TENTATIVAS - 1})")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if tentativa == TENTATIVAS:
                raise SystemExit(f"OpenAI fora de alcance depois de {TENTATIVAS} tentativas: {e}")
            espera = 5 * 2 ** (tentativa - 1)
            print(f"   ⏳ {tarefa}: sem resposta da OpenAI ({e}), nova tentativa em {espera:.0f}s ({tentativa}/{TENTATIVAS - 1})")
        time.sleep(min(espera, 60))
    u = resposta["usage"]
    raciocinio = (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
    custo_ia.registrar(tarefa, resposta["model"], u["prompt_tokens"], u["completion_tokens"] - raciocinio,
                       cache=(u.get("prompt_tokens_details") or {}).get("cached_tokens", 0), raciocinio=raciocinio)
    config.ENV["gpt_modelo_usado"] = resposta["model"]
    return json.loads(resposta["choices"][0]["message"]["content"])


def perguntar_gpt_json(tarefa, sistema, conteudo, esquema, modelo=None, esforco=None):
    """Instrução fixa (sistema) + pergunta → dict no formato do esquema. A OpenAI reaproveita sozinha o começo
    repetido do prompt (cache automático acima de 1.024 tokens), por isso as regras vêm sempre primeiro e iguais."""
    corpo = {"model": modelo or MODELO,
             "messages": [{"role": "system", "content": sistema}, {"role": "user", "content": conteudo}],
             "response_format": {"type": "json_schema", "json_schema": {"name": "resposta", "schema": esquema, "strict": False}}}
    if esforco:
        corpo["reasoning_effort"] = esforco
    return _enviar(tarefa, corpo)


def perguntar_gpt(tarefa, texto, imagens=(), modelo=None, detalhe="high", esforco=None):
    """texto + imagens (caminhos) → dict. Mostra e registra o custo da chamada."""
    modelo = modelo or MODELO
    conteudo = [{"type": "text", "text": texto}]
    for img in imagens:
        tipo = "png" if str(img).lower().endswith(".png") else "jpeg"
        if tipo == "png":  # PNG do app CDT tem fundo transparente: a IA vê tudo preto. Põe fundo branco.
            import cv2

            from .ocr_teste import ler_imagem

            b64 = base64.b64encode(cv2.imencode(".png", ler_imagem(img))[1].tobytes()).decode()
        else:
            b64 = base64.b64encode(open(img, "rb").read()).decode()
        conteudo.append({"type": "image_url", "image_url": {"url": f"data:image/{tipo};base64,{b64}", "detail": detalhe}})
    corpo = {"model": modelo, "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": conteudo}]}
    if esforco:  # quanto o modelo "pensa" antes de responder (low, medium, high): é o que mais pesa no custo
        corpo["reasoning_effort"] = esforco
    return _enviar(tarefa, corpo)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--modelo", default=None)
    p.add_argument("--esforco", choices=["low", "medium", "high"], default=None)
    p.add_argument("--desvirar", action="store_true", help="gira a foto do chassi 180° antes de mandar")
    args = p.parse_args()
    pasta = config.PASTA_AMOSTRAS / "PKD6E70"
    fotos = [pasta / "24_numero_do_chassi_do_veiculo.jpg", pasta / "27_painel_com_registro_e_km_do_veiculo.jpg"]
    if args.desvirar:
        import cv2

        from .ocr_teste import ler_imagem

        girada = config.PASTA_SAIDA / "chassi_desvirado.jpg"  # imwrite não aceita caminho com acento: grava os bytes
        girada.write_bytes(cv2.imencode(".jpg", cv2.rotate(ler_imagem(fotos[0]), cv2.ROTATE_180))[1].tobytes())
        fotos[0] = girada
    pergunta = ("Foto 1: número do chassi (VIN, 17 caracteres) gravado no veículo; a foto pode estar de ponta-cabeça. "
                "Foto 2: painel. Responda em JSON: "
                '{"chassi": "...", "chassi_legivel": true/false, "km": "...", "observacoes": "..."}')
    tarefa = "teste chassi+painel" + (f" esforço {args.esforco}" if args.esforco else "") + (" desvirado" if args.desvirar else "")
    print(json.dumps(perguntar_gpt(tarefa, pergunta, fotos, args.modelo, esforco=args.esforco), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
