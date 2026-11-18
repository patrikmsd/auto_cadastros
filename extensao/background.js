// Fala com o servidor do assistente. A página do CRM nunca acessa o servidor direto.
// Endereço e chave do analista ficam nas opções da extensão (chrome.storage); sem nada configurado, usa este PC.
const PADRAO = "http://127.0.0.1:8780";

async function config() {
  const c = await chrome.storage.local.get({servidor: PADRAO, chave: ""});
  return {servidor: (c.servidor || PADRAO).replace(/\/+$/, ""), H: {"X-Assistente": "1", "X-Chave": c.chave || ""}};
}

// imagem como data: URL (a página do CRM é https; assim não depende de http local)
async function comoDados(r) {
  const bytes = new Uint8Array(await r.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return `data:${r.headers.get("Content-Type") || "image/jpeg"};base64,${btoa(bin)}`;
}

async function json(r) {
  try { return await r.json(); } catch { return {erro: `resposta inválida do servidor (${r.status})`}; }
}

async function chamar(msg) {
  if (msg.tipo === "opcoes") { chrome.runtime.openOptionsPage(); return {status: 200}; }
  const {servidor: SERVIDOR, H} = await config();
  try {
    if (msg.tipo === "quem") {
      const r = await fetch(`${SERVIDOR}/api/quem`, {headers: H, cache: "no-store"});
      return {status: r.status, dados: await json(r)};
    }
    if (msg.tipo === "resultado") {
      const r = await fetch(`${SERVIDOR}/api/resultado/${encodeURIComponent(msg.chave)}`, {headers: H, cache: "no-store"});
      return {status: r.status, dados: await json(r)};
    }
    if (msg.tipo === "analisar") {
      const r = await fetch(`${SERVIDOR}/api/analisar`, {method: "POST", headers: {...H, "Content-Type": "application/json"},
                                                          body: JSON.stringify({codigo: msg.chave})});
      return {status: r.status, dados: await json(r)};
    }
    if (msg.tipo === "observacao") {
      const r = await fetch(`${SERVIDOR}/api/observacao`, {method: "POST", headers: {...H, "Content-Type": "application/json"},
                                                           body: JSON.stringify(msg.dados)});
      return {status: r.status, dados: await json(r)};
    }
    if (msg.tipo === "imagem") {
      const r = await fetch(`${SERVIDOR}/api/imagem/${encodeURIComponent(msg.chave)}?r=${encodeURIComponent(msg.rotulo)}${msg.mini ? "&mini=1" : ""}`, {headers: H});
      return r.ok ? {status: 200, dados: await comoDados(r)} : {status: r.status};
    }
    if (msg.tipo === "miniatura" || msg.tipo === "tela") {  // fotos e telas do card durante a análise
      const r = await fetch(`${SERVIDOR}/api/${msg.tipo}/${encodeURIComponent(msg.pedido)}/${msg.i}`, {headers: H, cache: "no-store"});
      return r.ok ? {status: 200, dados: await comoDados(r)} : {status: r.status};
    }
  } catch (e) {
    return {status: 0, erro: `servidor do assistente fora do ar (${SERVIDOR}). Confira o endereço nas opções da extensão.`};
  }
  return {status: 400};
}

chrome.runtime.onMessage.addListener((msg, _remetente, responder) => {
  chamar(msg).then(responder);
  return true;  // resposta assíncrona
});
